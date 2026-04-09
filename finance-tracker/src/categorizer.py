"""Transaction categorization based on keyword matching."""

import logging

from src.models import Transaction

logger = logging.getLogger(__name__)


def match_category(description: str, category_map: dict[str, list[str]]) -> str:
    """Find the best matching category for a transaction description.

    Uses case-insensitive substring matching. When multiple categories match,
    the one with the longest matching keyword wins (most specific match).
    Returns "Other" if no keywords match.
    """
    desc_lower = description.lower()
    best_category = "Other"
    best_keyword_len = 0

    for category, keywords in category_map.items():
        for keyword in keywords:
            keyword_lower = keyword.lower()
            if keyword_lower in desc_lower and len(keyword_lower) > best_keyword_len:
                best_category = category
                best_keyword_len = len(keyword_lower)

    return best_category


def categorize_transactions(
    transactions: list[Transaction],
    category_map: dict[str, list[str]],
) -> list[Transaction]:
    """Assign a category to each transaction based on description keyword matching.

    Mutates and returns the same transaction objects with updated category fields.
    """
    for txn in transactions:
        txn.category = match_category(txn.description, category_map)
    categorized_count = sum(1 for t in transactions if t.category != "Other")
    logger.info(
        "Categorized %d/%d transactions (%d as 'Other')",
        categorized_count,
        len(transactions),
        len(transactions) - categorized_count,
    )
    return transactions
