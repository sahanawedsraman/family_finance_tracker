"""Gemini-based PDF parser and categorizer for financial statements.

Uploads PDFs directly to Gemini for native document understanding,
which handles scanned documents, complex layouts, and multi-column
formats much better than text extraction.
Falls back to pdfplumber text extraction if direct upload fails.
"""

import json
import logging
import os
import re

import google.genai as genai
from google.genai import types as genai_types
import pdfplumber

from src.models import Transaction

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a financial document parser. You will receive a bank statement, credit card statement, investment statement, or pay statement PDF.

Your job is to extract EVERY individual transaction and return them as a JSON array. Be thorough — do not miss any transactions. Go through every page carefully.

For each transaction, return:
- "date": the transaction date in "YYYY-MM-DD" format. Infer the year from context in the statement (statement period, closing date, etc). If only MM/DD is shown, use the statement year. For pay stubs, use the pay date or pay period end date.
- "description": the merchant/payee name or line item description, cleaned up (remove reference numbers, extra whitespace, card numbers)
- "amount": a number. Negative for money spent/debited/withheld, positive for income/credits/payments received.
- "category": classify into one of these categories based on the description: Groceries, Dining, Utilities, Transportation, Entertainment, Healthcare, Amazon, Shopping, Subscriptions, Housing, Travel, Income, Taxes, Transfer, Investment, Retirement, Insurance, Education, Childcare, Fees, Other
- "type": "debit" if money was spent/withheld, "credit" if money was received

Rules:
- Extract ALL transactions from ALL pages of the document.
- Do NOT include summary lines, totals, balance lines, interest calculations, or fee summaries — only actual transactions.
- Do NOT include opening/closing balance entries.
- For credit card statements: payments made TO the card are "credit" (positive), purchases are "debit" (negative).
- For bank statements: deposits are "credit" (positive), withdrawals are "debit" (negative).
- For investment statements (Fidelity, Vanguard, Schwab, etc.): contributions are "credit", withdrawals are "debit", dividends are "credit". Categorize as "Investment".
- For pay statements / pay stubs:
  - Extract ONLY the CURRENT PAY PERIOD amounts, NOT year-to-date (YTD) totals.
  - Gross pay / salary / regular earnings → "Income" (positive amount, type "credit")
  - Bonus, overtime, commission → "Income" (positive amount, type "credit")
  - Federal income tax withholding → "Taxes" (negative amount, type "debit", description "Federal Income Tax")
  - State income tax withholding → "Taxes" (negative amount, type "debit", description "State Income Tax")
  - Social Security / OASDI → "Taxes" (negative amount, type "debit", description "Social Security")
  - Medicare → "Taxes" (negative amount, type "debit", description "Medicare")
  - 401k / 403b / Roth 401k contributions → "Retirement" (negative amount, type "debit")
  - Health insurance / medical / dental / vision premiums → "Healthcare" (negative amount, type "debit")
  - Life insurance / disability insurance → "Insurance" (negative amount, type "debit")
  - HSA / FSA contributions → "Healthcare" (negative amount, type "debit")
  - Do NOT include net pay as a separate transaction — it is the result, not a transaction.
  - Do NOT include YTD totals — only current period amounts.
- If you cannot determine the date for a line, skip it.
- Clean up descriptions: remove trailing reference numbers, card suffixes, excessive punctuation.

Return ONLY a JSON array. No markdown, no explanation, no code fences. Just the raw JSON array."""


def _extract_pdf_text(path: str) -> str:
    """Extract all text from a PDF file using pdfplumber."""
    pages = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n\n".join(pages)


def _parse_gemini_response(text: str) -> list[dict]:
    """Parse the JSON array from Gemini's response, handling common formatting issues."""
    cleaned = text.strip()
    # Strip markdown code fences if present
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*\n?", "", cleaned)
        cleaned = re.sub(r"\n?```\s*$", "", cleaned)
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse Gemini response as JSON: %s", e)
        logger.debug("Raw response: %s", text[:500])
        return []

    if not isinstance(data, list):
        logger.error("Gemini response is not a JSON array")
        return []

    return data


def _dict_to_transaction(d: dict) -> Transaction | None:
    """Convert a parsed dict from Gemini into a Transaction object."""
    from datetime import datetime

    date_str = d.get("date", "")
    if not date_str:
        return None

    try:
        txn_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        logger.debug("Skipping transaction with invalid date: %s", date_str)
        return None

    description = str(d.get("description", "")).strip()
    if not description:
        return None

    try:
        amount = float(d.get("amount", 0))
    except (ValueError, TypeError):
        return None

    category = str(d.get("category", "Other")).strip()
    txn_type = str(d.get("type", "")).strip().lower()
    if txn_type not in ("debit", "credit"):
        txn_type = "debit" if amount < 0 else "credit"

    return Transaction(
        date=txn_date,
        description=description,
        amount=amount,
        category=category,
        transaction_type=txn_type,
    )


def _is_rate_limit_error(e: Exception) -> bool:
    """Check if an exception is a rate limit (429) error."""
    error_str = str(e)
    return "429" in error_str or "RESOURCE_EXHAUSTED" in error_str


def _call_gemini_for_pdf(
    client,
    model: str,
    pdf_bytes: bytes,
    filename: str,
) -> str | None:
    """Single attempt to get a response from Gemini for a PDF. Returns response text or None."""
    try:
        response = client.models.generate_content(
            model=model,
            contents=[
                genai_types.Content(
                    parts=[
                        genai_types.Part.from_bytes(
                            data=pdf_bytes,
                            mime_type="application/pdf",
                        ),
                        genai_types.Part.from_text(
                            text="Extract all transactions from this financial statement. This could be a bank statement, credit card statement, investment statement, or pay stub. For pay stubs, extract each earnings line and each deduction line as separate transactions using only the current period amounts, not YTD."
                        ),
                    ],
                ),
            ],
            config=genai_types.GenerateContentConfig(
                system_instruction=SYSTEM_PROMPT,
                temperature=0.1,
            ),
        )
        result = response.text
        logger.info("Gemini (%s) response length for %s: %d chars", model, filename, len(result) if result else 0)
        return result
    except Exception as e:
        logger.warning("Gemini (%s) failed for %s: %s", model, filename, e)
        return None


def parse_pdf_with_gemini(
    path: str,
    api_key: str,
    model: str = "gemini-2.0-flash",
    fallback_models: list[str] | None = None,
) -> list[Transaction]:
    """Parse a PDF statement using Gemini for extraction and categorization.

    Tries the primary model first, then fallback models on rate limit errors.
    Falls back to pdfplumber text extraction if all models fail.

    Args:
        path: Path to the PDF file.
        api_key: Gemini API key.
        model: Primary Gemini model name.
        fallback_models: List of fallback model names to try on rate limit.

    Returns:
        List of Transaction objects.
    """
    client = genai.Client(api_key=api_key)
    filename = os.path.basename(path)
    models_to_try = [model] + (fallback_models or [])

    # Read PDF bytes once
    with open(path, "rb") as f:
        pdf_bytes = f.read()

    # Try each model
    response_text = None
    for m in models_to_try:
        logger.info("Trying Gemini model %s for: %s", m, filename)
        response_text = _call_gemini_for_pdf(client, m, pdf_bytes, filename)
        if response_text:
            break
        logger.info("Model %s failed, trying next fallback...", m)

    # Fallback to text extraction if all models failed
    if response_text is None:
        text = _extract_pdf_text(path)
        if not text.strip():
            logger.warning("No text extracted from PDF: %s", path)
            return []

        logger.info("All models failed for PDF upload. Trying text extraction for %s", filename)

        for m in models_to_try:
            try:
                response = client.models.generate_content(
                    model=m,
                    contents=text,
                    config=genai_types.GenerateContentConfig(
                        system_instruction=SYSTEM_PROMPT,
                        temperature=0.1,
                    ),
                )
                response_text = response.text
                if response_text:
                    logger.info("Text extraction with %s succeeded for %s", m, filename)
                    break
            except Exception as e:
                logger.warning("Text extraction with %s failed for %s: %s", m, filename, e)

    if response_text is None:
        logger.error("All Gemini models and fallbacks failed for %s", filename)
        return []

    # Step 3: Parse response
    raw_transactions = _parse_gemini_response(response_text)
    logger.info("Gemini returned %d raw transactions for %s", len(raw_transactions), filename)

    # Step 4: Convert to Transaction objects
    transactions = []
    for d in raw_transactions:
        txn = _dict_to_transaction(d)
        if txn:
            transactions.append(txn)

    logger.info(
        "Parsed %d valid transactions from %s via Gemini",
        len(transactions),
        filename,
    )
    return transactions


CATEGORIZE_PROMPT = """You are a financial transaction categorizer. You will receive a JSON array of transactions, each with "description" and "amount" fields.

For each transaction, return the same array but with a "category" field added/updated.

Classify each transaction into exactly one of these categories:
Groceries, Dining, Utilities, Transportation, Entertainment, Healthcare, Amazon, Shopping, Subscriptions, Housing, Travel, Income, Taxes, Transfer, Investment, Retirement, Insurance, Education, Childcare, Fees, Other

Rules:
- Use the description and amount to determine the category.
- Positive amounts are typically income, transfers in, or refunds.
- Negative amounts are expenses.
- Be specific: "COSTCO" is Groceries, "NETFLIX" is Subscriptions, "SHELL OIL" is Transportation.
- Amazon, AMZN, Amazon Fresh, Amazon Prime purchases are Amazon.
- Payroll, direct deposits, and salary are Income.
- Federal tax, state tax, Social Security, Medicare, FICA withholdings are Taxes.
- 401k, 403b, IRA, pension contributions are Retirement.
- Fidelity, Vanguard, Schwab, brokerage contributions/purchases are Investment.
- Zelle, Venmo, wire transfers are Transfer.
- Daycare, preschool, childcare are Childcare.

Return ONLY a JSON array of objects with "index" (0-based position) and "category". No markdown, no explanation. Example:
[{"index": 0, "category": "Groceries"}, {"index": 1, "category": "Dining"}]"""


def categorize_with_gemini(
    transactions: list[Transaction],
    api_key: str,
    model: str = "gemini-2.0-flash",
    fallback_models: list[str] | None = None,
    batch_size: int = 50,
) -> None:
    """Categorize transactions using Gemini. Mutates transactions in place.

    Tries fallback models on rate limit errors.

    Args:
        transactions: List of Transaction objects to categorize.
        api_key: Gemini API key.
        model: Primary Gemini model name.
        fallback_models: Fallback models to try on rate limit.
        batch_size: Number of transactions per API call.
    """
    if not transactions:
        return

    client = genai.Client(api_key=api_key)
    models_to_try = [model] + (fallback_models or [])

    for batch_start in range(0, len(transactions), batch_size):
        # Pace requests to avoid per-minute rate limits
        if batch_start > 0:
            import time
            time.sleep(2)

        batch = transactions[batch_start:batch_start + batch_size]

        payload = [
            {"index": i, "description": t.description, "amount": t.amount}
            for i, t in enumerate(batch)
        ]

        categorized = False
        for m in models_to_try:
            try:
                response = client.models.generate_content(
                    model=m,
                    contents=json.dumps(payload),
                    config=genai_types.GenerateContentConfig(
                        system_instruction=CATEGORIZE_PROMPT,
                        temperature=0.1,
                    ),
                )
                response_text = response.text
                results = _parse_gemini_response(response_text)

                for item in results:
                    idx = item.get("index")
                    cat = item.get("category", "Other")
                    if isinstance(idx, int) and 0 <= idx < len(batch):
                        batch[idx].category = cat

                logger.info(
                    "Gemini (%s) categorized batch of %d transactions (offset %d)",
                    m, len(batch), batch_start,
                )
                categorized = True
                break
            except Exception as e:
                logger.warning(
                    "Gemini (%s) categorization failed for batch at offset %d: %s",
                    m, batch_start, e,
                )

        if not categorized:
            logger.error("All models failed to categorize batch at offset %d", batch_start)
