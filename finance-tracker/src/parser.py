"""Statement parser for CSV, Excel, and PDF bank/credit card statements."""

import logging
import os
import re
from datetime import datetime

import pandas as pd
import pdfplumber

from src.models import Transaction
from src.pdf_text_parser import parse_pdf_text_fallback

logger = logging.getLogger(__name__)

# Known header patterns for fuzzy column detection
DATE_PATTERNS = ["date", "trans date", "transaction date", "posting date", "post date"]
DESCRIPTION_PATTERNS = [
    "description",
    "memo",
    "details",
    "transaction description",
    "narrative",
    "particulars",
]
AMOUNT_PATTERNS = ["amount", "total", "transaction amount"]
DEBIT_PATTERNS = ["debit", "debit amount", "withdrawals", "withdrawal"]
CREDIT_PATTERNS = ["credit", "credit amount", "deposits", "deposit"]


def _fuzzy_match_column(columns: list[str], patterns: list[str]) -> str | None:
    """Find the first column whose lowercased name contains any of the patterns."""
    lower_cols = {col: col.strip().lower() for col in columns}
    for pattern in patterns:
        for original, lower in lower_cols.items():
            if pattern == lower or pattern in lower:
                return original
    return None


def _detect_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Auto-detect column mappings from a DataFrame using fuzzy header matching."""
    cols = list(df.columns)
    return {
        "date": _fuzzy_match_column(cols, DATE_PATTERNS),
        "description": _fuzzy_match_column(cols, DESCRIPTION_PATTERNS),
        "amount": _fuzzy_match_column(cols, AMOUNT_PATTERNS),
        "debit": _fuzzy_match_column(cols, DEBIT_PATTERNS),
        "credit": _fuzzy_match_column(cols, CREDIT_PATTERNS),
    }


def normalize_amount(value) -> float:
    """
    Convert an amount value to a float.

    Handles strings with currency symbols, commas, parentheses (negative).
    Positive = income, negative = expense.
    """
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return 0.0
    s = value.strip()
    if not s or s == "-":
        return 0.0
    # Detect parentheses as negative: (123.45) -> -123.45
    is_negative = False
    if s.startswith("(") and s.endswith(")"):
        is_negative = True
        s = s[1:-1]
    # Remove currency symbols and commas
    s = re.sub(r"[£€$,]", "", s).strip()
    # Handle trailing minus or leading minus
    if s.endswith("-"):
        is_negative = True
        s = s[:-1].strip()
    try:
        result = float(s)
    except ValueError:
        return 0.0
    return -result if is_negative else result


def _parse_date(value) -> datetime | None:
    """Try to parse a date from various common formats."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()
    if not isinstance(value, str):
        return None
    formats = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%d/%m/%Y", "%m-%d-%Y", "%Y/%m/%d"]
    s = value.strip()
    for fmt in formats:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _rows_to_transactions(df: pd.DataFrame, col_map: dict[str, str | None]) -> list[Transaction]:
    """Convert DataFrame rows to Transaction objects using detected column mapping."""
    transactions: list[Transaction] = []
    date_col = col_map["date"]
    desc_col = col_map["description"]
    amount_col = col_map["amount"]
    debit_col = col_map["debit"]
    credit_col = col_map["credit"]

    if not date_col or not desc_col:
        logger.warning("Could not detect required date/description columns")
        return transactions

    for _, row in df.iterrows():
        parsed_date = _parse_date(row.get(date_col))
        if parsed_date is None:
            continue

        description = str(row.get(desc_col, "")).strip()
        if not description:
            continue

        # Determine amount and transaction type
        if amount_col and pd.notna(row.get(amount_col)):
            amount = normalize_amount(row[amount_col])
            txn_type = "credit" if amount >= 0 else "debit"
        elif debit_col or credit_col:
            debit_val = normalize_amount(row.get(debit_col)) if debit_col and pd.notna(row.get(debit_col)) else 0.0
            credit_val = normalize_amount(row.get(credit_col)) if credit_col and pd.notna(row.get(credit_col)) else 0.0
            if debit_val != 0.0:
                amount = -abs(debit_val)
                txn_type = "debit"
            elif credit_val != 0.0:
                amount = abs(credit_val)
                txn_type = "credit"
            else:
                amount = 0.0
                txn_type = ""
        else:
            logger.warning("No amount/debit/credit columns detected, skipping row")
            continue

        transactions.append(
            Transaction(
                date=parsed_date.date(),
                description=description,
                amount=amount,
                transaction_type=txn_type,
            )
        )
    return transactions


def _find_header_row(path: str) -> int:
    """Find the row containing actual column headers (Date, Description, Amount).
    Some bank CSVs have summary rows before the real data."""
    try:
        with open(path, 'r', errors='replace') as f:
            for i, line in enumerate(f):
                lower = line.lower()
                if 'date' in lower and ('description' in lower or 'memo' in lower):
                    return i
                if i > 20:  # don't scan too far
                    break
    except Exception:
        pass
    return 0  # default to first row


def parse_csv(path: str) -> list[Transaction]:
    """Parse a CSV bank/credit card statement using pandas."""
    try:
        header_row = _find_header_row(path)
        df = pd.read_csv(path, header=header_row, on_bad_lines="skip")
        col_map = _detect_columns(df)
        if not col_map.get("date") or not col_map.get("description"):
            # Try without skipping rows as fallback
            df = pd.read_csv(path, on_bad_lines="skip")
            col_map = _detect_columns(df)
        return _rows_to_transactions(df, col_map)
    except Exception:
        logger.warning("Failed to parse CSV: %s", path, exc_info=True)
        return []


def parse_excel(path: str) -> list[Transaction]:
    """Parse an Excel bank/credit card statement using pandas."""
    try:
        df = pd.read_excel(path)
        col_map = _detect_columns(df)
        return _rows_to_transactions(df, col_map)
    except Exception:
        logger.warning("Failed to parse Excel: %s", path, exc_info=True)
        return []


def parse_pdf(path: str) -> list[Transaction]:
    """Parse a PDF bank/credit card statement.

    First tries pdfplumber table extraction. If no tables are found (common with
    real bank statements like BofA and Chase), falls back to text-based parsing
    with bank-specific profiles.
    """
    try:
        transactions: list[Transaction] = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                tables = page.extract_tables()
                for table in tables:
                    if not table or len(table) < 2:
                        continue
                    # First row is headers
                    headers = [str(h).strip() if h else "" for h in table[0]]
                    df = pd.DataFrame(table[1:], columns=headers)
                    col_map = _detect_columns(df)
                    transactions.extend(_rows_to_transactions(df, col_map))

        if transactions:
            return transactions

        # Fallback: text-based parsing for real bank statements
        logger.info("No tables found in PDF %s, trying text-based parsing", path)
        return parse_pdf_text_fallback(path)
    except Exception:
        logger.warning("Failed to parse PDF: %s", path, exc_info=True)
        return []


# MIME type to parser routing
MIME_CSV = "text/csv"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_XLS = "application/vnd.ms-excel"
MIME_PDF = "application/pdf"

# File extension fallback mapping
EXTENSION_MAP = {
    ".csv": "csv",
    ".xlsx": "excel",
    ".xls": "excel",
    ".pdf": "pdf",
}

MIME_MAP = {
    MIME_CSV: "csv",
    "application/csv": "csv",
    "text/comma-separated-values": "csv",
    MIME_XLSX: "excel",
    MIME_XLS: "excel",
    MIME_PDF: "pdf",
}

# Credit card issuers that use inverted sign convention
# (positive = purchase, negative = payment/refund)
INVERTED_SIGN_PATTERNS = ["discover"]


def _is_discover_file(file_name: str) -> bool:
    """Check if a file is from an issuer with inverted sign convention."""
    name_lower = file_name.lower()
    return any(p in name_lower for p in INVERTED_SIGN_PATTERNS)


def detect_person(file_path: str, file_name: str, person_patterns: dict[str, list[str]], folder_path: str = "") -> str:
    """
    Detect which person a statement belongs to based on Drive folder path, local path, and filename.

    Checks in order: Drive folder path (most reliable), local file path, then filename.

    Args:
        file_path: Local path to the file.
        file_name: The filename itself.
        person_patterns: Mapping of person name -> list of patterns to match.
        folder_path: The Google Drive folder path (e.g. "Raman/Chase").

    Returns:
        The matched person name, or "Unknown" if no match.
    """
    # Check Drive folder path first (e.g. "Raman" or "Raman/Chase")
    if folder_path:
        lower_folder = folder_path.lower()
        for person, patterns in person_patterns.items():
            for pattern in patterns:
                if pattern.lower() in lower_folder:
                    return person

    # Fall back to local path and filename
    lower_path = file_path.lower()
    lower_name = file_name.lower()

    for person, patterns in person_patterns.items():
        for pattern in patterns:
            p = pattern.lower()
            if p in lower_name or p in lower_path:
                return person
    return "Unknown"


def parse_file(
    file_path: str,
    file_name: str = "",
    mime_type: str = "",
    person_patterns: dict[str, list[str]] | None = None,
    gemini_config=None,
    folder_path: str = "",
) -> list[Transaction]:
    """
    Route to the correct parser based on MIME type or file extension.
    Uses Gemini for PDFs when configured, falls back to regex-based parsing.
    Assigns person and source_file to each transaction.

    Args:
        file_path: Local path to the downloaded file.
        file_name: Original filename (used for person detection).
        mime_type: MIME type from Google Drive (optional, falls back to extension).
        person_patterns: Mapping of person name -> list of patterns.
        gemini_config: Optional GeminiConfig for LLM-based PDF parsing.
        folder_path: Google Drive folder path for person detection.

    Returns:
        List of Transaction objects with person and source_file populated.
    """
    if not file_name:
        file_name = os.path.basename(file_path)

    # Determine parser type from MIME type or file extension
    parser_type = MIME_MAP.get(mime_type)
    if not parser_type:
        _, ext = os.path.splitext(file_name)
        parser_type = EXTENSION_MAP.get(ext.lower())

    if parser_type == "csv":
        transactions = parse_csv(file_path)
    elif parser_type == "excel":
        transactions = parse_excel(file_path)
    elif parser_type == "pdf":
        # Use Gemini if configured
        if gemini_config and gemini_config.enabled:
            from src.llm_parser import parse_pdf_with_gemini

            logger.info("Routing PDF to Gemini parser: %s", file_name)
            transactions = parse_pdf_with_gemini(
                file_path,
                api_key=gemini_config.api_key,
                model=gemini_config.model,
                fallback_models=gemini_config.fallback_models,
            )
            if not transactions:
                logger.warning("Gemini returned no results for %s — check if the PDF is readable", file_name)
        else:
            logger.info("Gemini not configured, using regex PDF parser for: %s", file_name)
            transactions = parse_pdf(file_path)
    else:
        logger.warning("Unsupported file type for %s (mime: %s)", file_name, mime_type)
        return []

    # Flip signs for Discover credit card CSVs (positive = purchase, negative = payment/refund)
    # Our convention: negative = spending, positive = income
    if _is_discover_file(file_name):
        for txn in transactions:
            txn.amount = -txn.amount
            txn.transaction_type = "debit" if txn.amount < 0 else "credit"
        logger.info("Flipped signs for Discover file: %s", file_name)

    # Assign person and source file
    person = "Unknown"
    if person_patterns:
        person = detect_person(file_path, file_name, person_patterns, folder_path)

    for txn in transactions:
        txn.source_file = file_name
        txn.person = person

    logger.info(
        "Parsed %d transactions from %s (type=%s, person=%s)",
        len(transactions),
        file_name,
        parser_type,
        person,
    )
    return transactions
