"""Text-based PDF parser for bank and credit card statements.

Handles statements where pdfplumber's extract_tables() returns nothing useful.
Uses regex patterns to extract transaction lines from raw text, with
bank-specific profiles for BofA and Chase.
"""

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

import pdfplumber

from src.models import Transaction

logger = logging.getLogger(__name__)


@dataclass
class BankProfile:
    """Defines how to parse a specific bank's statement format."""

    name: str
    # Regex patterns that identify this bank in the PDF text
    identifiers: list[str]
    # Section headers that mark the start of transaction blocks
    transaction_sections: list[str] = field(default_factory=list)
    # Section headers that mark sections to skip (summaries, etc.)
    skip_sections: list[str] = field(default_factory=list)
    # Regex pattern to match a transaction line
    # Must have named groups: month, day, description, amount
    # Optional named groups: year, post_month, post_day
    transaction_pattern: str = ""
    # How to determine debit vs credit
    # "sign" = negative amounts are debits
    # "section" = determined by which section the transaction is in
    debit_credit_mode: str = "sign"
    # Date format hint
    date_year_prefix: str = "20"  # prepended if year is 2 digits
    # Lines matching these patterns are continuation of previous description
    continuation_pattern: str | None = None
    # Pattern for lines to always ignore
    noise_patterns: list[str] = field(default_factory=list)


# --- Bank of America checking/savings statement profile ---
BOFA_PROFILE = BankProfile(
    name="bofa",
    identifiers=["bank of america", "bofa"],
    transaction_sections=[
        "deposits and other credits",
        "withdrawals and other debits",
        "checks",
        "other withdrawals",
        "other deposits",
    ],
    skip_sections=[
        "daily ending balance",
        "summary",
        "account number",
        "total deposits",
        "total withdrawals",
        "ending balance",
        "beginning balance",
        "customer service",
        "this page is intentionally",
    ],
    # BofA format: MM/DD/YY  Description  Amount  (optional running balance)
    # e.g. "01/05/26  Walmart Grocery Store #1234  52.30"
    # Some lines have a running balance after the amount
    transaction_pattern=(
        r"^(?P<month>\d{2})/(?P<day>\d{2})/(?P<year>\d{2})\s+"
        r"(?P<description>.+?)\s+"
        r"(?P<amount>-?[\d,]+\.\d{2})"
        r"(?:\s+[\d,]+\.\d{2})?$"  # optional trailing balance
    ),
    debit_credit_mode="section",
    noise_patterns=[
        r"^page \d+",
        r"^continued on",
        r"^\s*$",
        r"^adsit",
    ],
)

# --- Chase credit card statement profile ---
CHASE_CREDIT_PROFILE = BankProfile(
    name="chase_credit",
    identifiers=["chase", "jpmorgan chase", "cardmember"],
    transaction_sections=[
        "payments and other credits",
        "purchase",
        "fees charged",
        "interest charged",
    ],
    skip_sections=[
        "account summary",
        "payment information",
        "account activity",
        "interest charge calculation",
        "transactions",  # generic header, not a section we skip but we look for sub-sections
        "total fees charged",
        "total interest charged",
        "total this period",
        "new balance",
        "opening/closing",
    ],
    # Chase format: MM/DD  MM/DD  Description  Amount
    # post_date  trans_date  description  amount
    # e.g. "01/05  01/03  STARBUCKS STORE 12345  4.75"
    transaction_pattern=(
        r"^(?P<post_month>\d{2})/(?P<post_day>\d{2})\s+"
        r"(?P<month>\d{2})/(?P<day>\d{2})\s+"
        r"(?P<description>.+?)\s+"
        r"(?P<amount>-?[\d,]+\.\d{2})$"
    ),
    debit_credit_mode="section",
    noise_patterns=[
        r"^page \d+",
        r"^\s*$",
        r"^continued",
    ],
)

# All known profiles
BANK_PROFILES: list[BankProfile] = [BOFA_PROFILE, CHASE_CREDIT_PROFILE]


def detect_bank_profile(text: str) -> BankProfile | None:
    """Detect which bank profile matches the PDF text content."""
    text_lower = text.lower()
    for profile in BANK_PROFILES:
        for identifier in profile.identifiers:
            if identifier in text_lower:
                logger.info("Detected bank profile: %s", profile.name)
                return profile
    return None


def _infer_year(text: str) -> int:
    """Try to infer the statement year from text like 'Statement Period: 01/01/26 - 01/31/26'."""
    # Look for date patterns with 4-digit years
    match = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if match:
        return int(match.group(3))
    # Look for 2-digit year patterns
    match = re.search(r"(\d{2})/(\d{2})/(\d{2})", text)
    if match:
        yr = int(match.group(3))
        return 2000 + yr if yr < 100 else yr
    # Look for standalone 4-digit year
    match = re.search(r"\b(20\d{2})\b", text)
    if match:
        return int(match.group(1))
    return datetime.now().year


def _determine_section_type(section_name: str, profile: BankProfile) -> str:
    """Determine if a section contains debits or credits based on its header."""
    s = section_name.lower()
    credit_keywords = ["deposit", "credit", "payment", "refund"]
    debit_keywords = ["withdrawal", "debit", "purchase", "fee", "interest", "check"]
    for kw in credit_keywords:
        if kw in s:
            return "credit"
    for kw in debit_keywords:
        if kw in s:
            return "debit"
    return "debit"  # default assumption


def parse_text_transactions(
    full_text: str,
    profile: BankProfile,
    statement_year: int | None = None,
) -> list[Transaction]:
    """Parse transactions from raw PDF text using a bank profile.

    Args:
        full_text: The complete extracted text from all PDF pages.
        profile: The bank profile to use for parsing.
        statement_year: Override year for transactions. If None, inferred from text.

    Returns:
        List of Transaction objects.
    """
    if statement_year is None:
        statement_year = _infer_year(full_text)

    lines = full_text.split("\n")
    transactions: list[Transaction] = []
    current_section: str | None = None
    current_section_type: str = "debit"
    txn_re = re.compile(profile.transaction_pattern, re.IGNORECASE)
    noise_res = [re.compile(p, re.IGNORECASE) for p in profile.noise_patterns]

    in_skip_section = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # Check if this is noise
        if any(nr.search(stripped) for nr in noise_res):
            continue

        # Check if we're entering a skip section
        stripped_lower = stripped.lower()
        if any(skip in stripped_lower for skip in profile.skip_sections):
            in_skip_section = True
            current_section = None
            continue

        # Check if we're entering a transaction section
        matched_section = False
        for section in profile.transaction_sections:
            if section in stripped_lower:
                current_section = section
                current_section_type = _determine_section_type(section, profile)
                in_skip_section = False
                matched_section = True
                logger.debug("Entered section: %s (type=%s)", section, current_section_type)
                break

        if matched_section:
            continue

        if in_skip_section:
            continue

        # Try to match a transaction line
        match = txn_re.match(stripped)
        if match:
            groups = match.groupdict()
            month = int(groups["month"])
            day = int(groups["day"])

            # Handle year
            if "year" in groups and groups["year"]:
                yr = int(groups["year"])
                year = 2000 + yr if yr < 100 else yr
            else:
                year = statement_year

            try:
                txn_date = datetime(year, month, day).date()
            except ValueError:
                logger.debug("Invalid date %d/%d/%d in line: %s", month, day, year, stripped)
                continue

            description = groups["description"].strip()
            amount_str = groups["amount"].replace(",", "")

            try:
                amount = float(amount_str)
            except ValueError:
                continue

            # Determine debit/credit and sign
            if profile.debit_credit_mode == "section":
                if current_section_type == "debit":
                    txn_type = "debit"
                    amount = -abs(amount)
                else:
                    txn_type = "credit"
                    amount = abs(amount)
            else:
                # sign mode
                if amount < 0:
                    txn_type = "debit"
                else:
                    txn_type = "credit"

            transactions.append(
                Transaction(
                    date=txn_date,
                    description=description,
                    amount=amount,
                    transaction_type=txn_type,
                )
            )

    logger.info(
        "Text parser extracted %d transactions using %s profile (year=%d)",
        len(transactions),
        profile.name,
        statement_year,
    )
    return transactions


def parse_pdf_text_fallback(path: str) -> list[Transaction]:
    """Parse a PDF statement using text extraction when table extraction fails.

    This is the fallback parser for real bank/credit card statements that
    don't have proper table structures in the PDF.

    Args:
        path: Path to the PDF file.

    Returns:
        List of Transaction objects, or empty list if parsing fails.
    """
    try:
        full_text = ""
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    full_text += page_text + "\n"

        if not full_text.strip():
            logger.warning("No text extracted from PDF: %s", path)
            return []

        profile = detect_bank_profile(full_text)
        if profile is None:
            logger.warning("Could not detect bank profile for PDF: %s", path)
            return []

        return parse_text_transactions(full_text, profile)

    except Exception:
        logger.warning("Failed to parse PDF with text fallback: %s", path, exc_info=True)
        return []
