"""Data models for the Finance Tracker application."""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Transaction:
    """Represents a single financial transaction parsed from a statement."""

    date: date
    description: str
    amount: float  # positive = income, negative = expense
    category: str = "Other"
    person: str = "Unknown"
    source_file: str = ""
    transaction_type: str = ""  # "debit" or "credit"
