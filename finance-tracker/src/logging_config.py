"""Centralized logging configuration and run summary for the Finance Tracker."""

import logging
import os
from dataclasses import dataclass, field


@dataclass
class RunSummary:
    """Tracks statistics for a single run of the finance tracker pipeline."""

    files_processed: int = 0
    files_skipped: int = 0
    transactions_found: int = 0
    errors: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        """Record an error that occurred during the run."""
        self.errors.append(message)

    def format_summary(self) -> str:
        """Return a human-readable summary string."""
        lines = [
            "=== Run Summary ===",
            f"Files processed: {self.files_processed}",
            f"Files skipped:   {self.files_skipped}",
            f"Transactions:    {self.transactions_found}",
            f"Errors:          {len(self.errors)}",
        ]
        if self.errors:
            lines.append("Error details:")
            for err in self.errors:
                lines.append(f"  - {err}")
        return "\n".join(lines)


def setup_logging(log_level: str = "INFO", log_dir: str = "logs") -> None:
    """Configure the root logger with a file handler and a console handler.

    Args:
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        log_dir: Directory for the log file. Created if it doesn't exist.
    """
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "finance_tracker.log")

    level = getattr(logging, log_level.upper(), logging.INFO)

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Avoid adding duplicate handlers on repeated calls
    if not any(isinstance(h, logging.FileHandler) for h in root_logger.handlers):
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    if not any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root_logger.handlers
    ):
        console_handler = logging.StreamHandler()
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)
