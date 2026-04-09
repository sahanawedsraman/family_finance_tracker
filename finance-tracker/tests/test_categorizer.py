"""Tests for the transaction categorizer module."""

from datetime import date

import pytest

from src.categorizer import categorize_transactions, match_category
from src.models import Transaction


CATEGORY_MAP = {
    "Groceries": ["walmart", "costco", "trader joe", "whole foods"],
    "Dining": ["restaurant", "mcdonald", "starbucks", "uber eats"],
    "Transportation": ["gas station", "uber", "lyft"],
    "Entertainment": ["netflix", "spotify"],
    "Shopping": ["amazon", "target"],
}


def _make_txn(description: str) -> Transaction:
    return Transaction(date=date(2025, 1, 15), description=description, amount=-50.0)


# --- match_category tests ---


class TestMatchCategory:
    def test_exact_keyword_match(self):
        assert match_category("walmart", CATEGORY_MAP) == "Groceries"

    def test_substring_match(self):
        assert match_category("WALMART SUPERCENTER #1234", CATEGORY_MAP) == "Groceries"

    def test_case_insensitive(self):
        assert match_category("STARBUCKS COFFEE", CATEGORY_MAP) == "Dining"
        assert match_category("Netflix Monthly", CATEGORY_MAP) == "Entertainment"
        assert match_category("sPOTiFy PrEmIuM", CATEGORY_MAP) == "Entertainment"

    def test_no_match_returns_other(self):
        assert match_category("random unknown vendor", CATEGORY_MAP) == "Other"

    def test_empty_description_returns_other(self):
        assert match_category("", CATEGORY_MAP) == "Other"

    def test_longest_match_wins(self):
        """When 'uber eats' and 'uber' both match, 'uber eats' (longer) should win -> Dining."""
        assert match_category("Uber Eats delivery", CATEGORY_MAP) == "Dining"

    def test_longest_match_shorter_keyword_category(self):
        """'uber' alone should match Transportation, not Dining."""
        assert match_category("Uber ride to airport", CATEGORY_MAP) == "Transportation"

    def test_empty_category_map(self):
        assert match_category("walmart purchase", {}) == "Other"

    def test_multiple_categories_longest_keyword_wins(self):
        """'gas station' (11 chars) should beat 'gas' if it were a keyword."""
        custom_map = {
            "Fuel": ["gas"],
            "Transportation": ["gas station"],
        }
        assert match_category("Shell Gas Station", custom_map) == "Transportation"


# --- categorize_transactions tests ---


class TestCategorizeTransactions:
    def test_categorizes_all_transactions(self):
        txns = [
            _make_txn("Walmart groceries"),
            _make_txn("Netflix subscription"),
            _make_txn("Unknown vendor"),
        ]
        result = categorize_transactions(txns, CATEGORY_MAP)

        assert result[0].category == "Groceries"
        assert result[1].category == "Entertainment"
        assert result[2].category == "Other"

    def test_returns_same_transaction_objects(self):
        txns = [_make_txn("Costco bulk buy")]
        result = categorize_transactions(txns, CATEGORY_MAP)
        assert result is txns
        assert result[0] is txns[0]

    def test_empty_transaction_list(self):
        result = categorize_transactions([], CATEGORY_MAP)
        assert result == []

    def test_preserves_other_fields(self):
        txn = Transaction(
            date=date(2025, 3, 1),
            description="Target shopping",
            amount=-120.0,
            person="John",
            source_file="chase_john.csv",
            transaction_type="debit",
        )
        categorize_transactions([txn], CATEGORY_MAP)
        assert txn.category == "Shopping"
        assert txn.person == "John"
        assert txn.source_file == "chase_john.csv"
        assert txn.amount == -120.0
