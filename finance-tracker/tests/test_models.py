"""Unit tests for the Transaction data model."""

from datetime import date

from src.models import Transaction


class TestTransactionCreation:
    """Tests for creating Transaction instances."""

    def test_create_with_all_fields(self):
        t = Transaction(
            date=date(2025, 3, 15),
            description="Grocery Store",
            amount=-52.30,
            category="Groceries",
            person="John",
            source_file="chase_john_march.csv",
            transaction_type="debit",
        )
        assert t.date == date(2025, 3, 15)
        assert t.description == "Grocery Store"
        assert t.amount == -52.30
        assert t.category == "Groceries"
        assert t.person == "John"
        assert t.source_file == "chase_john_march.csv"
        assert t.transaction_type == "debit"

    def test_create_with_required_fields_only(self):
        t = Transaction(
            date=date(2025, 1, 1),
            description="Direct Deposit",
            amount=3000.00,
        )
        assert t.date == date(2025, 1, 1)
        assert t.description == "Direct Deposit"
        assert t.amount == 3000.00

    def test_default_category_is_other(self):
        t = Transaction(date=date(2025, 1, 1), description="Test", amount=0)
        assert t.category == "Other"

    def test_default_person_is_unknown(self):
        t = Transaction(date=date(2025, 1, 1), description="Test", amount=0)
        assert t.person == "Unknown"

    def test_default_source_file_is_empty(self):
        t = Transaction(date=date(2025, 1, 1), description="Test", amount=0)
        assert t.source_file == ""

    def test_default_transaction_type_is_empty(self):
        t = Transaction(date=date(2025, 1, 1), description="Test", amount=0)
        assert t.transaction_type == ""

    def test_positive_amount_for_income(self):
        t = Transaction(date=date(2025, 2, 1), description="Paycheck", amount=5000.0)
        assert t.amount > 0

    def test_negative_amount_for_expense(self):
        t = Transaction(date=date(2025, 2, 1), description="Rent", amount=-2500.0)
        assert t.amount < 0

    def test_zero_amount(self):
        t = Transaction(date=date(2025, 2, 1), description="Refund offset", amount=0.0)
        assert t.amount == 0.0

    def test_equality(self):
        kwargs = dict(date=date(2025, 1, 1), description="Test", amount=10.0)
        assert Transaction(**kwargs) == Transaction(**kwargs)

    def test_inequality_different_amount(self):
        base = dict(date=date(2025, 1, 1), description="Test")
        assert Transaction(**base, amount=10.0) != Transaction(**base, amount=20.0)
