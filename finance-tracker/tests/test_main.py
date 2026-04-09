"""Integration tests for the main pipeline with mocked Google APIs."""

import json
import os
import textwrap
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
import yaml

from main import main, parse_args, run_pipeline
from src.drive import DriveFile
from src.logging_config import RunSummary
from src.models import Transaction


@pytest.fixture
def config_file(tmp_path):
    """Create a temporary config.yaml for testing."""
    config = {
        "drive_folder_id": "test_folder_123",
        "sheet_id": "test_sheet_456",
        "sheet_name": "Test Finance Tracker",
        "persons": [
            {"name": "Alice", "patterns": ["alice"]},
            {"name": "Bob", "patterns": ["bob"]},
        ],
        "categories": {
            "Groceries": ["walmart", "costco"],
            "Dining": ["restaurant", "starbucks"],
        },
        "budgets": {
            "Groceries": 500,
            "Dining": 200,
        },
        "log_level": "WARNING",
    }
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f)
    return str(config_path)


@pytest.fixture
def sample_csv(tmp_path):
    """Create a sample CSV statement file."""
    csv_content = textwrap.dedent("""\
        Date,Description,Amount
        2025-01-15,Walmart Groceries,-120.50
        2025-01-16,Starbucks Coffee,-5.75
        2025-01-20,Salary Deposit,3000.00
    """)
    csv_path = tmp_path / "alice_statement.csv"
    csv_path.write_text(csv_content)
    return str(csv_path)


@pytest.fixture
def mock_drive_service(sample_csv):
    """Create a mock Drive service that returns a single CSV file."""
    service = MagicMock()

    # list_files mock: return one CSV file
    list_response = {
        "files": [
            {
                "id": "file_001",
                "name": "alice_statement.csv",
                "mimeType": "text/csv",
            }
        ],
        "nextPageToken": None,
    }
    service.files().list().execute.return_value = list_response

    # download mock: copy the sample CSV to the download location
    def mock_get_media(fileId):
        mock_request = MagicMock()
        # Read the actual CSV content
        with open(sample_csv, "rb") as f:
            content = f.read()

        class FakeDownloader:
            def __init__(self, data):
                self._data = data
                self._done = False

            def next_chunk(self):
                self._done = True
                return None, True

        mock_request._content = content
        mock_request.execute.return_value = content
        return mock_request

    service.files().get_media = mock_get_media

    return service


@pytest.fixture
def mock_sheets_service():
    """Create a mock Sheets service."""
    service = MagicMock()

    # create_or_get_sheet: return existing sheet ID
    service.spreadsheets().get().execute.return_value = {
        "spreadsheetId": "test_sheet_456",
        "sheets": [
            {"properties": {"title": "Transactions", "sheetId": 0}},
            {"properties": {"title": "Monthly Summary", "sheetId": 1}},
            {"properties": {"title": "Category Breakdown", "sheetId": 2}},
            {"properties": {"title": "KPIs", "sheetId": 3}},
            {"properties": {"title": "Budget Status", "sheetId": 4}},
        ],
    }
    service.spreadsheets().values().clear().execute.return_value = {}
    service.spreadsheets().values().update().execute.return_value = {}
    service.spreadsheets().batchUpdate().execute.return_value = {}

    return service


class TestParseArgs:
    def test_default_config(self):
        args = parse_args([])
        assert args.config == "config.yaml"

    def test_custom_config(self):
        args = parse_args(["--config", "my_config.yaml"])
        assert args.config == "my_config.yaml"


class TestRunPipeline:
    """Integration tests for run_pipeline with mocked APIs."""

    @patch("main.save_processed_state")
    @patch("main.load_processed_state", return_value=set())
    @patch("main.write_to_sheet", return_value="test_sheet_456")
    @patch("main.list_files")
    @patch("main.download_files")
    @patch("main.build_sheets_service")
    @patch("main.build_drive_service")
    @patch("main.get_credentials")
    def test_full_pipeline_with_new_files(
        self,
        mock_get_creds,
        mock_build_drive,
        mock_build_sheets,
        mock_download,
        mock_list,
        mock_write_sheet,
        mock_load_state,
        mock_save_state,
        config_file,
        sample_csv,
    ):
        """Test the full pipeline processes new files end-to-end."""
        mock_get_creds.return_value = MagicMock()
        mock_build_drive.return_value = MagicMock()
        mock_build_sheets.return_value = MagicMock()

        drive_file = DriveFile(
            id="file_001",
            name="alice_statement.csv",
            mime_type="text/csv",
            local_path=sample_csv,
        )
        mock_list.return_value = [drive_file]
        mock_download.return_value = [drive_file]

        summary = run_pipeline(config_file)

        assert summary.files_processed == 1
        assert summary.files_skipped == 0
        assert summary.transactions_found == 3
        assert len(summary.errors) == 0

        # Verify write_to_sheet was called with transactions
        mock_write_sheet.assert_called_once()
        call_kwargs = mock_write_sheet.call_args
        transactions = call_kwargs.kwargs.get("transactions") or call_kwargs[1].get("transactions")
        if transactions is None:
            # positional args
            transactions = call_kwargs[0][3]
        assert len(transactions) == 3

        # Verify state was saved
        mock_save_state.assert_called_once()
        saved_ids = mock_save_state.call_args[0][1]
        assert "file_001" in saved_ids

    @patch("main.save_processed_state")
    @patch("main.load_processed_state", return_value=set())
    @patch("main.write_to_sheet", return_value="test_sheet_456")
    @patch("main.list_files", return_value=[])
    @patch("main.build_sheets_service")
    @patch("main.build_drive_service")
    @patch("main.get_credentials")
    def test_pipeline_no_new_files(
        self,
        mock_get_creds,
        mock_build_drive,
        mock_build_sheets,
        mock_list,
        mock_write_sheet,
        mock_load_state,
        mock_save_state,
        config_file,
    ):
        """Test pipeline handles no new files gracefully."""
        mock_get_creds.return_value = MagicMock()
        mock_build_drive.return_value = MagicMock()
        mock_build_sheets.return_value = MagicMock()

        summary = run_pipeline(config_file)

        assert summary.files_processed == 0
        assert summary.transactions_found == 0
        assert len(summary.errors) == 0

        # write_to_sheet should still be called (with empty data)
        mock_write_sheet.assert_called_once()

        # State should NOT be saved (no new files processed)
        mock_save_state.assert_not_called()

    @patch("main.load_processed_state")
    @patch("main.write_to_sheet", return_value="test_sheet_456")
    @patch("main.list_files")
    @patch("main.download_files")
    @patch("main.build_sheets_service")
    @patch("main.build_drive_service")
    @patch("main.get_credentials")
    def test_pipeline_skips_already_processed(
        self,
        mock_get_creds,
        mock_build_drive,
        mock_build_sheets,
        mock_download,
        mock_list,
        mock_write_sheet,
        mock_load_state,
        config_file,
    ):
        """Test pipeline skips files that were already processed."""
        mock_get_creds.return_value = MagicMock()
        mock_build_drive.return_value = MagicMock()
        mock_build_sheets.return_value = MagicMock()

        drive_file = DriveFile(
            id="file_001",
            name="alice_statement.csv",
            mime_type="text/csv",
        )
        mock_list.return_value = [drive_file]
        mock_load_state.return_value = {"file_001"}  # already processed

        summary = run_pipeline(config_file)

        assert summary.files_processed == 0
        assert summary.files_skipped == 1
        assert summary.transactions_found == 0
        mock_download.assert_not_called()

    @patch("main.get_credentials", side_effect=Exception("Auth error"))
    def test_pipeline_auth_failure(self, mock_get_creds, config_file):
        """Test pipeline handles authentication failure."""
        summary = run_pipeline(config_file)

        assert len(summary.errors) == 1
        assert "Authentication failed" in summary.errors[0]

    @patch("main.load_processed_state", return_value=set())
    @patch("main.list_files", side_effect=Exception("Drive API error"))
    @patch("main.build_sheets_service")
    @patch("main.build_drive_service")
    @patch("main.get_credentials")
    def test_pipeline_drive_list_failure(
        self,
        mock_get_creds,
        mock_build_drive,
        mock_build_sheets,
        mock_list,
        mock_load_state,
        config_file,
    ):
        """Test pipeline handles Drive listing failure."""
        mock_get_creds.return_value = MagicMock()
        mock_build_drive.return_value = MagicMock()
        mock_build_sheets.return_value = MagicMock()

        summary = run_pipeline(config_file)

        assert len(summary.errors) == 1
        assert "Drive" in summary.errors[0]

    @patch("main.save_processed_state")
    @patch("main.load_processed_state", return_value=set())
    @patch("main.write_to_sheet", side_effect=Exception("Sheets API error"))
    @patch("main.list_files")
    @patch("main.download_files")
    @patch("main.build_sheets_service")
    @patch("main.build_drive_service")
    @patch("main.get_credentials")
    def test_pipeline_sheets_write_failure(
        self,
        mock_get_creds,
        mock_build_drive,
        mock_build_sheets,
        mock_download,
        mock_list,
        mock_write_sheet,
        mock_load_state,
        mock_save_state,
        config_file,
        sample_csv,
    ):
        """Test pipeline handles Sheets write failure without saving state."""
        mock_get_creds.return_value = MagicMock()
        mock_build_drive.return_value = MagicMock()
        mock_build_sheets.return_value = MagicMock()

        drive_file = DriveFile(
            id="file_001",
            name="alice_statement.csv",
            mime_type="text/csv",
            local_path=sample_csv,
        )
        mock_list.return_value = [drive_file]
        mock_download.return_value = [drive_file]

        summary = run_pipeline(config_file)

        assert len(summary.errors) == 1
        assert "Google Sheets" in summary.errors[0]
        # State should NOT be saved on failure
        mock_save_state.assert_not_called()

    @patch("main.save_processed_state")
    @patch("main.load_processed_state", return_value=set())
    @patch("main.write_to_sheet", return_value="test_sheet_456")
    @patch("main.list_files")
    @patch("main.download_files")
    @patch("main.build_sheets_service")
    @patch("main.build_drive_service")
    @patch("main.get_credentials")
    def test_pipeline_categorizes_transactions(
        self,
        mock_get_creds,
        mock_build_drive,
        mock_build_sheets,
        mock_download,
        mock_list,
        mock_write_sheet,
        mock_load_state,
        mock_save_state,
        config_file,
        sample_csv,
    ):
        """Test that transactions are categorized before writing."""
        mock_get_creds.return_value = MagicMock()
        mock_build_drive.return_value = MagicMock()
        mock_build_sheets.return_value = MagicMock()

        drive_file = DriveFile(
            id="file_001",
            name="alice_statement.csv",
            mime_type="text/csv",
            local_path=sample_csv,
        )
        mock_list.return_value = [drive_file]
        mock_download.return_value = [drive_file]

        summary = run_pipeline(config_file)

        # Check that write_to_sheet received categorized transactions
        call_kwargs = mock_write_sheet.call_args
        transactions = call_kwargs.kwargs.get("transactions") or call_kwargs[1].get("transactions")
        if transactions is None:
            transactions = call_kwargs[0][3]

        categories = {t.category for t in transactions}
        # Walmart should be Groceries, Starbucks should be Dining
        assert "Groceries" in categories
        assert "Dining" in categories

    @patch("main.save_processed_state")
    @patch("main.load_processed_state", return_value=set())
    @patch("main.write_to_sheet", return_value="test_sheet_456")
    @patch("main.list_files")
    @patch("main.download_files")
    @patch("main.build_sheets_service")
    @patch("main.build_drive_service")
    @patch("main.get_credentials")
    def test_pipeline_computes_metrics(
        self,
        mock_get_creds,
        mock_build_drive,
        mock_build_sheets,
        mock_download,
        mock_list,
        mock_write_sheet,
        mock_load_state,
        mock_save_state,
        config_file,
        sample_csv,
    ):
        """Test that metrics and KPIs are computed and passed to sheet writer."""
        mock_get_creds.return_value = MagicMock()
        mock_build_drive.return_value = MagicMock()
        mock_build_sheets.return_value = MagicMock()

        drive_file = DriveFile(
            id="file_001",
            name="alice_statement.csv",
            mime_type="text/csv",
            local_path=sample_csv,
        )
        mock_list.return_value = [drive_file]
        mock_download.return_value = [drive_file]

        run_pipeline(config_file)

        call_kwargs = mock_write_sheet.call_args
        monthly_metrics = call_kwargs.kwargs.get("monthly_metrics") or call_kwargs[1].get("monthly_metrics")
        kpis = call_kwargs.kwargs.get("kpis") or call_kwargs[1].get("kpis")
        if monthly_metrics is None:
            monthly_metrics = call_kwargs[0][4]
            kpis = call_kwargs[0][5]

        assert len(monthly_metrics) > 0
        assert kpis is not None


class TestMainEntryPoint:
    """Test the main() CLI entry point."""

    def test_missing_config_exits(self):
        """Test that main exits with error for missing config file."""
        with pytest.raises(SystemExit) as exc_info:
            main(["--config", "/nonexistent/config.yaml"])
        assert exc_info.value.code == 1

    @patch("main.run_pipeline")
    def test_main_calls_pipeline(self, mock_run, config_file):
        """Test that main calls run_pipeline and prints summary."""
        mock_run.return_value = RunSummary(
            files_processed=2, transactions_found=10
        )
        # Should not raise
        main(["--config", config_file])
        mock_run.assert_called_once_with(config_file)

    @patch("main.run_pipeline")
    def test_main_exits_on_errors(self, mock_run, config_file):
        """Test that main exits with code 1 when there are errors."""
        summary = RunSummary()
        summary.add_error("Something went wrong")
        mock_run.return_value = summary

        with pytest.raises(SystemExit) as exc_info:
            main(["--config", config_file])
        assert exc_info.value.code == 1
