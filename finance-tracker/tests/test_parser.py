"""Tests for the statement parser module."""

import os
from datetime import date

import pandas as pd
import pytest

from src.parser import (
    _detect_columns,
    _fuzzy_match_column,
    normalize_amount,
    parse_csv,
    parse_excel,
)

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


# --- normalize_amount tests ---


class TestNormalizeAmount:
    def test_positive_float(self):
        assert normalize_amount(100.50) == 100.50

    def test_negative_float(self):
        assert normalize_amount(-42.0) == -42.0

    def test_integer(self):
        assert normalize_amount(10) == 10.0

    def test_string_plain(self):
        assert normalize_amount("123.45") == 123.45

    def test_string_with_dollar_sign(self):
        assert normalize_amount("$1,234.56") == 1234.56

    def test_string_with_euro_sign(self):
        assert normalize_amount("€99.99") == 99.99

    def test_parentheses_negative(self):
        assert normalize_amount("(50.00)") == -50.00

    def test_trailing_minus(self):
        assert normalize_amount("75.00-") == -75.00

    def test_leading_minus(self):
        assert normalize_amount("-30.00") == -30.00

    def test_empty_string(self):
        assert normalize_amount("") == 0.0

    def test_dash_only(self):
        assert normalize_amount("-") == 0.0

    def test_none_value(self):
        assert normalize_amount(None) == 0.0

    def test_non_numeric_string(self):
        assert normalize_amount("abc") == 0.0

    def test_zero(self):
        assert normalize_amount("0.00") == 0.0


# --- Column detection tests ---


class TestColumnDetection:
    def test_exact_match(self):
        cols = ["Date", "Description", "Amount"]
        assert _fuzzy_match_column(cols, ["date"]) == "Date"

    def test_fuzzy_match_substring(self):
        cols = ["Transaction Date", "Memo", "Total Amount"]
        assert _fuzzy_match_column(cols, ["date"]) == "Transaction Date"
        assert _fuzzy_match_column(cols, ["memo"]) == "Memo"

    def test_no_match(self):
        cols = ["Col1", "Col2"]
        assert _fuzzy_match_column(cols, ["date"]) is None

    def test_detect_columns_standard(self):
        df = pd.DataFrame(columns=["Date", "Description", "Amount"])
        result = _detect_columns(df)
        assert result["date"] == "Date"
        assert result["description"] == "Description"
        assert result["amount"] == "Amount"
        assert result["debit"] is None
        assert result["credit"] is None

    def test_detect_columns_debit_credit(self):
        df = pd.DataFrame(columns=["Transaction Date", "Memo", "Debit", "Credit"])
        result = _detect_columns(df)
        assert result["date"] == "Transaction Date"
        assert result["description"] == "Memo"
        assert result["amount"] is None
        assert result["debit"] == "Debit"
        assert result["credit"] == "Credit"

    def test_detect_columns_alternate_names(self):
        df = pd.DataFrame(columns=["Posting Date", "Narrative", "Withdrawals", "Deposits"])
        result = _detect_columns(df)
        assert result["date"] == "Posting Date"
        assert result["description"] == "Narrative"
        assert result["debit"] == "Withdrawals"
        assert result["credit"] == "Deposits"


# --- CSV parsing tests ---


class TestParseCSV:
    def test_parse_standard_csv(self):
        path = os.path.join(FIXTURES_DIR, "sample_statement.csv")
        transactions = parse_csv(path)
        assert len(transactions) == 5
        assert transactions[0].date == date(2024, 1, 5)
        assert transactions[0].description == "Walmart Grocery"
        assert transactions[0].amount == -52.30
        assert transactions[0].transaction_type == "debit"
        # Income row
        assert transactions[1].amount == 2500.00
        assert transactions[1].transaction_type == "credit"

    def test_parse_debit_credit_csv(self):
        path = os.path.join(FIXTURES_DIR, "debit_credit_statement.csv")
        transactions = parse_csv(path)
        assert len(transactions) == 4
        assert transactions[0].amount == -52.30
        assert transactions[0].transaction_type == "debit"
        assert transactions[1].amount == 2500.00
        assert transactions[1].transaction_type == "credit"

    def test_parse_nonexistent_csv(self):
        transactions = parse_csv("/nonexistent/file.csv")
        assert transactions == []

    def test_default_fields(self):
        path = os.path.join(FIXTURES_DIR, "sample_statement.csv")
        transactions = parse_csv(path)
        assert transactions[0].category == "Other"
        assert transactions[0].person == "Unknown"


# --- Excel parsing tests ---


class TestParseExcel:
    def test_parse_standard_excel(self):
        path = os.path.join(FIXTURES_DIR, "sample_statement.xlsx")
        transactions = parse_excel(path)
        assert len(transactions) == 4
        assert transactions[0].date == date(2024, 1, 5)
        assert transactions[0].description == "Walmart Grocery"
        assert transactions[0].amount == -52.30
        assert transactions[0].transaction_type == "debit"
        assert transactions[1].amount == 2500.00
        assert transactions[1].transaction_type == "credit"

    def test_parse_nonexistent_excel(self):
        transactions = parse_excel("/nonexistent/file.xlsx")
        assert transactions == []


# --- PDF parsing tests ---

from unittest.mock import MagicMock, patch

from src.parser import parse_pdf


class TestParsePDF:
    def _mock_pdf_with_table(self, table_data):
        """Create a mock pdfplumber PDF with a single page containing one table."""
        mock_page = MagicMock()
        mock_page.extract_tables.return_value = [table_data]
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        return mock_pdf

    @patch("src.parser.pdfplumber")
    def test_parse_pdf_standard(self, mock_pdfplumber):
        table = [
            ["Date", "Description", "Amount"],
            ["2024-01-05", "Walmart Grocery", "-52.30"],
            ["2024-01-07", "Direct Deposit", "2500.00"],
            ["2024-01-10", "Starbucks Coffee", "-4.75"],
        ]
        mock_pdfplumber.open.return_value = self._mock_pdf_with_table(table)

        transactions = parse_pdf("dummy.pdf")
        assert len(transactions) == 3
        assert transactions[0].date == date(2024, 1, 5)
        assert transactions[0].description == "Walmart Grocery"
        assert transactions[0].amount == -52.30
        assert transactions[0].transaction_type == "debit"
        assert transactions[1].amount == 2500.00
        assert transactions[1].transaction_type == "credit"

    @patch("src.parser.pdfplumber")
    def test_parse_pdf_debit_credit_columns(self, mock_pdfplumber):
        table = [
            ["Date", "Details", "Debit", "Credit"],
            ["2024-01-05", "Grocery Store", "52.30", ""],
            ["2024-01-07", "Salary", "", "3000.00"],
        ]
        mock_pdfplumber.open.return_value = self._mock_pdf_with_table(table)

        transactions = parse_pdf("dummy.pdf")
        assert len(transactions) == 2
        assert transactions[0].amount == -52.30
        assert transactions[0].transaction_type == "debit"
        assert transactions[1].amount == 3000.00
        assert transactions[1].transaction_type == "credit"

    @patch("src.parser.pdfplumber")
    def test_parse_pdf_empty_table(self, mock_pdfplumber):
        mock_page = MagicMock()
        mock_page.extract_tables.return_value = [[]]
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        transactions = parse_pdf("dummy.pdf")
        assert transactions == []

    @patch("src.parser.pdfplumber")
    def test_parse_pdf_no_tables(self, mock_pdfplumber):
        mock_page = MagicMock()
        mock_page.extract_tables.return_value = []
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        transactions = parse_pdf("dummy.pdf")
        assert transactions == []

    @patch("src.parser.pdfplumber")
    def test_parse_pdf_malformed_raises(self, mock_pdfplumber):
        mock_pdfplumber.open.side_effect = Exception("Corrupted PDF")
        transactions = parse_pdf("bad.pdf")
        assert transactions == []

    @patch("src.parser.pdfplumber")
    def test_parse_pdf_multiple_pages(self, mock_pdfplumber):
        table1 = [
            ["Date", "Description", "Amount"],
            ["2024-01-05", "Store A", "-10.00"],
        ]
        table2 = [
            ["Date", "Description", "Amount"],
            ["2024-02-10", "Store B", "-20.00"],
        ]
        page1 = MagicMock()
        page1.extract_tables.return_value = [table1]
        page2 = MagicMock()
        page2.extract_tables.return_value = [table2]
        mock_pdf = MagicMock()
        mock_pdf.pages = [page1, page2]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        transactions = parse_pdf("multi_page.pdf")
        assert len(transactions) == 2
        assert transactions[0].description == "Store A"
        assert transactions[1].description == "Store B"


# --- File routing and person detection tests ---

from src.parser import detect_person, parse_file


class TestDetectPerson:
    PATTERNS = {
        "John": ["john", "chase_john", "amex_john"],
        "Jane": ["jane", "chase_jane", "bofa_jane"],
    }

    def test_match_in_filename(self):
        assert detect_person("/tmp/downloads/chase_john_jan.csv", "chase_john_jan.csv", self.PATTERNS) == "John"

    def test_match_in_folder_path(self):
        assert detect_person("/tmp/downloads/jane/statement.csv", "statement.csv", self.PATTERNS) == "Jane"

    def test_case_insensitive(self):
        assert detect_person("/tmp/JOHN/file.csv", "file.csv", self.PATTERNS) == "John"

    def test_no_match(self):
        assert detect_person("/tmp/downloads/unknown.csv", "unknown.csv", self.PATTERNS) == "Unknown"

    def test_empty_patterns(self):
        assert detect_person("/tmp/john.csv", "john.csv", {}) == "Unknown"


class TestParseFile:
    FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
    PERSON_PATTERNS = {
        "John": ["john"],
        "Jane": ["jane"],
    }

    def test_route_csv_by_mime(self):
        path = os.path.join(self.FIXTURES_DIR, "sample_statement.csv")
        txns = parse_file(path, file_name="statement.csv", mime_type="text/csv")
        assert len(txns) == 5
        assert all(t.source_file == "statement.csv" for t in txns)

    def test_route_excel_by_mime(self):
        path = os.path.join(self.FIXTURES_DIR, "sample_statement.xlsx")
        txns = parse_file(
            path,
            file_name="statement.xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        assert len(txns) == 4

    def test_route_csv_by_extension(self):
        path = os.path.join(self.FIXTURES_DIR, "sample_statement.csv")
        txns = parse_file(path, file_name="sample_statement.csv")
        assert len(txns) == 5

    def test_route_excel_by_extension(self):
        path = os.path.join(self.FIXTURES_DIR, "sample_statement.xlsx")
        txns = parse_file(path, file_name="sample_statement.xlsx")
        assert len(txns) == 4

    @patch("src.parser.pdfplumber")
    def test_route_pdf_by_mime(self, mock_pdfplumber):
        table = [
            ["Date", "Description", "Amount"],
            ["2024-01-05", "Store", "-10.00"],
        ]
        mock_page = MagicMock()
        mock_page.extract_tables.return_value = [table]
        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        txns = parse_file("dummy.pdf", file_name="stmt.pdf", mime_type="application/pdf")
        assert len(txns) == 1

    def test_unsupported_type_returns_empty(self):
        txns = parse_file("file.txt", file_name="file.txt", mime_type="text/plain")
        assert txns == []

    def test_person_assigned_from_filename(self):
        path = os.path.join(self.FIXTURES_DIR, "sample_statement.csv")
        txns = parse_file(
            path,
            file_name="chase_john_jan.csv",
            mime_type="text/csv",
            person_patterns=self.PERSON_PATTERNS,
        )
        assert all(t.person == "John" for t in txns)

    def test_person_assigned_from_path(self):
        path = os.path.join(self.FIXTURES_DIR, "sample_statement.csv")
        # Simulate a path that contains "jane" in a parent folder
        # We pass the real path for reading but the person detection uses file_path
        txns = parse_file(
            path,
            file_name="statement.csv",
            mime_type="text/csv",
            person_patterns={"Jane": ["fixtures"]},  # "fixtures" is in the path
        )
        assert all(t.person == "Jane" for t in txns)

    def test_person_unknown_when_no_patterns(self):
        path = os.path.join(self.FIXTURES_DIR, "sample_statement.csv")
        txns = parse_file(path, file_name="statement.csv", mime_type="text/csv")
        assert all(t.person == "Unknown" for t in txns)

    def test_file_name_defaults_to_basename(self):
        path = os.path.join(self.FIXTURES_DIR, "sample_statement.csv")
        txns = parse_file(path, mime_type="text/csv")
        assert all(t.source_file == "sample_statement.csv" for t in txns)
