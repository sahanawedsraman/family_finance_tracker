"""Tests for the logging configuration and run summary module."""

import logging
import os

import pytest

from src.logging_config import RunSummary, setup_logging


class TestRunSummary:
    def test_initial_state(self):
        summary = RunSummary()
        assert summary.files_processed == 0
        assert summary.files_skipped == 0
        assert summary.transactions_found == 0
        assert summary.errors == []

    def test_add_error(self):
        summary = RunSummary()
        summary.add_error("Failed to parse file.csv")
        summary.add_error("API timeout")
        assert len(summary.errors) == 2
        assert "file.csv" in summary.errors[0]

    def test_format_summary_no_errors(self):
        summary = RunSummary(files_processed=5, files_skipped=1, transactions_found=42)
        text = summary.format_summary()
        assert "Files processed: 5" in text
        assert "Files skipped:   1" in text
        assert "Transactions:    42" in text
        assert "Errors:          0" in text
        assert "Error details" not in text

    def test_format_summary_with_errors(self):
        summary = RunSummary(files_processed=3, transactions_found=10)
        summary.add_error("bad file")
        summary.add_error("timeout")
        text = summary.format_summary()
        assert "Errors:          2" in text
        assert "Error details:" in text
        assert "  - bad file" in text
        assert "  - timeout" in text

    def test_format_summary_header(self):
        summary = RunSummary()
        text = summary.format_summary()
        assert text.startswith("=== Run Summary ===")


class TestSetupLogging:
    def test_creates_log_directory(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        setup_logging(log_level="DEBUG", log_dir=log_dir)
        assert os.path.isdir(log_dir)
        # Clean up handlers added to root logger
        logging.getLogger().handlers.clear()

    def test_creates_log_file(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        setup_logging(log_level="INFO", log_dir=log_dir)
        log_file = os.path.join(log_dir, "finance_tracker.log")
        # Write a log message to flush to file
        test_logger = logging.getLogger("test_setup")
        test_logger.info("test message")
        # Check file was created
        assert os.path.isfile(log_file)
        logging.getLogger().handlers.clear()

    def test_file_and_console_handlers_added(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        root = logging.getLogger()
        root.handlers.clear()
        setup_logging(log_level="INFO", log_dir=log_dir)
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        stream_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert len(file_handlers) == 1
        assert len(stream_handlers) == 1
        root.handlers.clear()

    def test_no_duplicate_handlers_on_repeated_calls(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        root = logging.getLogger()
        root.handlers.clear()
        setup_logging(log_level="INFO", log_dir=log_dir)
        setup_logging(log_level="INFO", log_dir=log_dir)
        file_handlers = [h for h in root.handlers if isinstance(h, logging.FileHandler)]
        stream_handlers = [
            h for h in root.handlers
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        ]
        assert len(file_handlers) == 1
        assert len(stream_handlers) == 1
        root.handlers.clear()

    def test_sets_log_level(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        root = logging.getLogger()
        root.handlers.clear()
        setup_logging(log_level="DEBUG", log_dir=log_dir)
        assert root.level == logging.DEBUG
        root.handlers.clear()

    def test_invalid_log_level_defaults_to_info(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        root = logging.getLogger()
        root.handlers.clear()
        setup_logging(log_level="INVALID", log_dir=log_dir)
        assert root.level == logging.INFO
        root.handlers.clear()

    def test_log_message_written_to_file(self, tmp_path):
        log_dir = str(tmp_path / "test_logs")
        root = logging.getLogger()
        root.handlers.clear()
        setup_logging(log_level="INFO", log_dir=log_dir)
        test_logger = logging.getLogger("test_write")
        test_logger.info("hello from test")
        # Flush handlers
        for h in root.handlers:
            h.flush()
        log_file = os.path.join(log_dir, "finance_tracker.log")
        content = open(log_file).read()
        assert "hello from test" in content
        root.handlers.clear()


class TestModuleLogging:
    """Verify that key modules emit log messages for important operations."""

    def test_config_logs_on_load(self, tmp_path, caplog):
        """Config loader should log when configuration is loaded."""
        import yaml
        config_data = {
            "drive_folder_id": "abc123",
            "persons": [{"name": "A", "patterns": ["a"]}],
            "categories": {"Food": ["grocery"]},
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))
        with caplog.at_level(logging.INFO, logger="src.config"):
            from src.config import load_config
            load_config(str(config_file))
        assert any("Configuration loaded" in r.message for r in caplog.records)

    def test_categorizer_logs_on_categorize(self, caplog):
        """Categorizer should log categorization results."""
        from datetime import date
        from src.categorizer import categorize_transactions
        from src.models import Transaction
        txns = [
            Transaction(date=date(2025, 1, 1), description="walmart", amount=-50.0),
            Transaction(date=date(2025, 1, 2), description="unknown", amount=-20.0),
        ]
        category_map = {"Groceries": ["walmart"]}
        with caplog.at_level(logging.INFO, logger="src.categorizer"):
            categorize_transactions(txns, category_map)
        assert any("Categorized" in r.message for r in caplog.records)

    def test_metrics_logs_on_compute(self, caplog):
        """Metrics engine should log when monthly metrics are computed."""
        from datetime import date
        from src.metrics import compute_monthly_metrics
        from src.models import Transaction
        txns = [
            Transaction(date=date(2025, 1, 15), description="salary", amount=5000.0, person="A"),
        ]
        with caplog.at_level(logging.INFO, logger="src.metrics"):
            compute_monthly_metrics(txns, {}, ["A"])
        assert any("monthly metrics" in r.message.lower() for r in caplog.records)

    def test_kpis_logs_on_compute(self, caplog):
        """KPI computation should log results."""
        from datetime import date
        from src.metrics import compute_kpis, compute_monthly_metrics
        from src.models import Transaction
        txns = [
            Transaction(date=date(2025, 1, 15), description="salary", amount=5000.0, person="A"),
            Transaction(date=date(2025, 1, 16), description="groceries", amount=-200.0, person="A"),
        ]
        metrics = compute_monthly_metrics(txns, {"Groceries": 300}, ["A"])
        with caplog.at_level(logging.INFO, logger="src.metrics"):
            compute_kpis(metrics, {"Groceries": 300})
        assert any("KPIs" in r.message for r in caplog.records)

    def test_parser_logs_on_parse_file(self, tmp_path, caplog):
        """Parser should log the number of transactions parsed from a file."""
        import pandas as pd
        csv_file = tmp_path / "test.csv"
        df = pd.DataFrame({
            "Date": ["2025-01-01"],
            "Description": ["Test purchase"],
            "Amount": ["-50.00"],
        })
        df.to_csv(str(csv_file), index=False)
        with caplog.at_level(logging.INFO, logger="src.parser"):
            from src.parser import parse_file
            parse_file(str(csv_file), file_name="test.csv", mime_type="text/csv")
        assert any("Parsed" in r.message for r in caplog.records)

    def test_drive_logs_on_download(self, caplog):
        """Drive downloader should log successful downloads."""
        from unittest.mock import MagicMock, patch
        from src.drive import DriveFile, download_files

        mock_service = MagicMock()
        drive_file = DriveFile(id="1", name="stmt.csv", mime_type="text/csv")

        # Mock the download to write a dummy file
        mock_downloader = MagicMock()
        mock_downloader.next_chunk.return_value = (None, True)

        with patch("src.drive.MediaIoBaseDownload", return_value=mock_downloader):
            with caplog.at_level(logging.INFO, logger="src.drive"):
                download_files(mock_service, [drive_file], str(caplog.handler.baseFilename + "_dl") if hasattr(caplog.handler, 'baseFilename') else "/tmp/test_dl")

        assert any("Downloaded" in r.message for r in caplog.records)
