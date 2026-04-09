"""Tests for the text-based PDF parser with bank-specific profiles."""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.pdf_text_parser import (
    BOFA_PROFILE,
    CHASE_CREDIT_PROFILE,
    _determine_section_type,
    _infer_year,
    detect_bank_profile,
    parse_pdf_text_fallback,
    parse_text_transactions,
)


# --- Bank profile detection ---


class TestDetectBankProfile:
    def test_detect_bofa(self):
        text = "Bank of America\nStatement Period: 01/01/26 - 01/31/26"
        profile = detect_bank_profile(text)
        assert profile is not None
        assert profile.name == "bofa"

    def test_detect_bofa_case_insensitive(self):
        text = "BANK OF AMERICA N.A.\nYour checking account"
        profile = detect_bank_profile(text)
        assert profile is not None
        assert profile.name == "bofa"

    def test_detect_chase(self):
        text = "JPMorgan Chase Bank\nAccount Summary"
        profile = detect_bank_profile(text)
        assert profile is not None
        assert profile.name == "chase_credit"

    def test_detect_chase_cardmember(self):
        text = "Cardmember Service\nPayment Information"
        profile = detect_bank_profile(text)
        assert profile is not None
        assert profile.name == "chase_credit"

    def test_unknown_bank(self):
        text = "Some Unknown Bank\nStatement"
        profile = detect_bank_profile(text)
        assert profile is None


# --- Year inference ---


class TestInferYear:
    def test_four_digit_year_in_date(self):
        text = "Statement Period: 01/01/2026 - 01/31/2026"
        assert _infer_year(text) == 2026

    def test_two_digit_year(self):
        text = "Statement Period: 01/01/26 - 01/31/26"
        assert _infer_year(text) == 2026

    def test_standalone_year(self):
        text = "Statement for 2025\nAccount details"
        assert _infer_year(text) == 2025

    def test_no_year_found(self):
        text = "No date info here"
        year = _infer_year(text)
        # Should return current year as fallback
        assert isinstance(year, int)
        assert year >= 2024


# --- Section type detection ---


class TestDetermineSectionType:
    def test_deposit_is_credit(self):
        assert _determine_section_type("Deposits and other credits", BOFA_PROFILE) == "credit"

    def test_withdrawal_is_debit(self):
        assert _determine_section_type("Withdrawals and other debits", BOFA_PROFILE) == "debit"

    def test_purchase_is_debit(self):
        assert _determine_section_type("Purchases", CHASE_CREDIT_PROFILE) == "debit"

    def test_payment_is_credit(self):
        assert _determine_section_type("Payments and other credits", CHASE_CREDIT_PROFILE) == "credit"

    def test_fees_is_debit(self):
        assert _determine_section_type("Fees charged", CHASE_CREDIT_PROFILE) == "debit"

    def test_unknown_defaults_to_debit(self):
        assert _determine_section_type("Miscellaneous", BOFA_PROFILE) == "debit"



# --- BofA text parsing ---


class TestBofaTextParsing:
    BOFA_STATEMENT = """Bank of America
Statement Period: 01/01/26 - 01/31/26
Account Number: XXXX-XXXX-1234

Account Summary
Beginning Balance  $5,000.00
Deposits and Other Credits  $3,500.00
Withdrawals and Other Debits  $1,200.50
Ending Balance  $7,299.50

Deposits and other credits
01/07/26  DIRECT DEP EMPLOYER INC  2,500.00  5,500.00
01/15/26  VENMO PAYMENT FROM JANE  1,000.00  7,200.00

Withdrawals and other debits
01/05/26  WALMART GROCERY STORE #1234  52.30  4,947.70
01/10/26  STARBUCKS COFFEE #5678  4.75  5,495.25
01/20/26  SHELL GAS STATION  45.00  7,155.00
01/25/26  AMAZON.COM PURCHASE  89.99  7,065.01

Daily ending balance
01/01/26  5,000.00
01/05/26  4,947.70
"""

    def test_parse_bofa_deposits(self):
        txns = parse_text_transactions(self.BOFA_STATEMENT, BOFA_PROFILE)
        credits = [t for t in txns if t.transaction_type == "credit"]
        assert len(credits) == 2
        assert credits[0].description == "DIRECT DEP EMPLOYER INC"
        assert credits[0].amount == 2500.00
        assert credits[0].date == date(2026, 1, 7)

    def test_parse_bofa_withdrawals(self):
        txns = parse_text_transactions(self.BOFA_STATEMENT, BOFA_PROFILE)
        debits = [t for t in txns if t.transaction_type == "debit"]
        assert len(debits) == 4
        assert debits[0].description == "WALMART GROCERY STORE #1234"
        assert debits[0].amount == -52.30
        assert debits[0].date == date(2026, 1, 5)

    def test_parse_bofa_total_count(self):
        txns = parse_text_transactions(self.BOFA_STATEMENT, BOFA_PROFILE)
        assert len(txns) == 6

    def test_parse_bofa_skips_daily_balance(self):
        txns = parse_text_transactions(self.BOFA_STATEMENT, BOFA_PROFILE)
        # Daily ending balance section should be skipped
        descriptions = [t.description for t in txns]
        assert all("5,000.00" not in d for d in descriptions)

    def test_parse_bofa_year_detection(self):
        txns = parse_text_transactions(self.BOFA_STATEMENT, BOFA_PROFILE)
        assert all(t.date.year == 2026 for t in txns)

    def test_parse_bofa_with_explicit_year(self):
        txns = parse_text_transactions(self.BOFA_STATEMENT, BOFA_PROFILE, statement_year=2025)
        # Explicit year should be overridden by the 2-digit year in the date
        assert all(t.date.year == 2026 for t in txns)


# --- Chase credit card text parsing ---


class TestChaseTextParsing:
    CHASE_STATEMENT = """JPMorgan Chase Bank, N.A.
Cardmember Service
Statement Date: 01/31/2026
Account Number: XXXX-XXXX-XXXX-5678

Account Summary
Previous Balance  $500.00
Payments and Credits  -$500.00
Purchases  +$347.50
New Balance  $347.50

Payment Information
Payment Due Date: 02/25/2026
Minimum Payment Due: $25.00

Payments and other credits
01/03  01/02  PAYMENT THANK YOU  500.00
01/15  01/14  RETURN AMAZON.COM  25.00

Purchase
01/05  01/03  STARBUCKS STORE 12345  4.75
01/10  01/08  WALMART SUPERCENTER  125.30
01/12  01/11  NETFLIX.COM  15.99
01/20  01/18  SHELL OIL GAS STATION  45.00
01/25  01/24  AMAZON.COM AMZN.COM  156.46

Total fees charged this period  $0.00
Total interest charged this period  $0.00
"""

    def test_parse_chase_purchases(self):
        txns = parse_text_transactions(self.CHASE_STATEMENT, CHASE_CREDIT_PROFILE, statement_year=2026)
        debits = [t for t in txns if t.transaction_type == "debit"]
        assert len(debits) == 5
        assert debits[0].description == "STARBUCKS STORE 12345"
        assert debits[0].amount == -4.75

    def test_parse_chase_payments(self):
        txns = parse_text_transactions(self.CHASE_STATEMENT, CHASE_CREDIT_PROFILE, statement_year=2026)
        credits = [t for t in txns if t.transaction_type == "credit"]
        assert len(credits) == 2
        assert credits[0].description == "PAYMENT THANK YOU"
        assert credits[0].amount == 500.00

    def test_parse_chase_total_count(self):
        txns = parse_text_transactions(self.CHASE_STATEMENT, CHASE_CREDIT_PROFILE, statement_year=2026)
        assert len(txns) == 7

    def test_parse_chase_dates_use_transaction_date(self):
        txns = parse_text_transactions(self.CHASE_STATEMENT, CHASE_CREDIT_PROFILE, statement_year=2026)
        # The transaction date (second date) is used
        starbucks = [t for t in txns if "STARBUCKS" in t.description][0]
        assert starbucks.date == date(2026, 1, 3)

    def test_parse_chase_skips_summary(self):
        txns = parse_text_transactions(self.CHASE_STATEMENT, CHASE_CREDIT_PROFILE, statement_year=2026)
        descriptions = [t.description for t in txns]
        assert "Previous Balance" not in descriptions
        assert "New Balance" not in descriptions


# --- Fallback integration ---


class TestParsePdfTextFallback:
    @patch("src.pdf_text_parser.pdfplumber")
    def test_fallback_with_bofa_pdf(self, mock_pdfplumber):
        page = MagicMock()
        page.extract_text.return_value = """Bank of America
Statement Period: 01/01/26 - 01/31/26

Deposits and other credits
01/07/26  PAYROLL DEPOSIT  3000.00  8000.00

Withdrawals and other debits
01/05/26  GROCERY STORE  50.00  4950.00
"""
        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        txns = parse_pdf_text_fallback("bofa_statement.pdf")
        assert len(txns) == 2
        assert txns[0].amount == 3000.00
        assert txns[0].transaction_type == "credit"
        assert txns[1].amount == -50.00
        assert txns[1].transaction_type == "debit"

    @patch("src.pdf_text_parser.pdfplumber")
    def test_fallback_empty_text(self, mock_pdfplumber):
        page = MagicMock()
        page.extract_text.return_value = ""
        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        txns = parse_pdf_text_fallback("empty.pdf")
        assert txns == []

    @patch("src.pdf_text_parser.pdfplumber")
    def test_fallback_unknown_bank(self, mock_pdfplumber):
        page = MagicMock()
        page.extract_text.return_value = "Some Random Bank\n01/05/26  Purchase  50.00"
        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        txns = parse_pdf_text_fallback("unknown.pdf")
        assert txns == []

    @patch("src.pdf_text_parser.pdfplumber")
    def test_fallback_exception_handling(self, mock_pdfplumber):
        mock_pdfplumber.open.side_effect = Exception("Corrupted")
        txns = parse_pdf_text_fallback("bad.pdf")
        assert txns == []

    @patch("src.pdf_text_parser.pdfplumber")
    def test_fallback_multi_page(self, mock_pdfplumber):
        page1 = MagicMock()
        page1.extract_text.return_value = """Bank of America
Statement Period: 02/01/26 - 02/28/26

Withdrawals and other debits
02/05/26  STORE A  10.00  4990.00
"""
        page2 = MagicMock()
        page2.extract_text.return_value = """Withdrawals and other debits
02/15/26  STORE B  20.00  4970.00
"""
        mock_pdf = MagicMock()
        mock_pdf.pages = [page1, page2]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdfplumber.open.return_value = mock_pdf

        txns = parse_pdf_text_fallback("multi_page.pdf")
        assert len(txns) == 2
        assert txns[0].description == "STORE A"
        assert txns[1].description == "STORE B"


# --- Edge cases ---


class TestEdgeCases:
    def test_bofa_amount_with_commas(self):
        text = """Bank of America
Statement Period: 01/01/26 - 01/31/26

Deposits and other credits
01/15/26  LARGE DEPOSIT  12,500.00  17,500.00
"""
        txns = parse_text_transactions(text, BOFA_PROFILE)
        assert len(txns) == 1
        assert txns[0].amount == 12500.00

    def test_invalid_date_skipped(self):
        text = """Bank of America
Statement Period: 01/01/26 - 01/31/26

Withdrawals and other debits
13/45/26  BAD DATE TRANSACTION  50.00  4950.00
01/10/26  GOOD TRANSACTION  25.00  4925.00
"""
        txns = parse_text_transactions(text, BOFA_PROFILE)
        assert len(txns) == 1
        assert txns[0].description == "GOOD TRANSACTION"

    def test_empty_text(self):
        txns = parse_text_transactions("", BOFA_PROFILE)
        assert txns == []

    def test_no_transaction_sections(self):
        text = """Bank of America
Statement Period: 01/01/26 - 01/31/26
Account Summary
Beginning Balance  $5,000.00
"""
        txns = parse_text_transactions(text, BOFA_PROFILE)
        assert txns == []
