"""Tests for the Google Drive downloader module."""

from unittest.mock import MagicMock, patch, call

import pytest

from src.drive import (
    DriveFile,
    FOLDER_MIME_TYPE,
    SUPPORTED_MIME_TYPES,
    list_files,
)


def _make_list_response(files, next_page_token=None):
    """Helper to build a mock Drive files().list().execute() response."""
    resp = {"files": files}
    if next_page_token:
        resp["nextPageToken"] = next_page_token
    return resp


def _build_mock_service(responses_by_folder: dict[str, list[dict]]):
    """Build a mock Drive service where each folder_id maps to a list response.

    responses_by_folder: { folder_id: [list_response, ...] }
    Each folder_id can have multiple responses to simulate pagination.
    """
    service = MagicMock()

    def list_side_effect(**kwargs):
        query = kwargs.get("q", "")
        # Extract folder id from query like "'folder123' in parents and trashed = false"
        folder_id = query.split("'")[1]
        mock_request = MagicMock()
        responses = responses_by_folder.get(folder_id, [_make_list_response([])])
        # Pop the first response each call to support pagination
        if len(responses) > 1:
            mock_request.execute.return_value = responses.pop(0)
        else:
            mock_request.execute.return_value = responses[0]
        return mock_request

    service.files.return_value.list.side_effect = list_side_effect
    return service


class TestListFiles:
    """Tests for list_files() — recursive folder scanning."""

    def test_empty_folder(self):
        service = _build_mock_service({"root": [_make_list_response([])]})
        result = list_files(service, "root")
        assert result == []

    def test_flat_folder_with_supported_files(self):
        files = [
            {"id": "1", "name": "stmt.pdf", "mimeType": "application/pdf"},
            {"id": "2", "name": "stmt.csv", "mimeType": "text/csv"},
            {"id": "3", "name": "stmt.xlsx", "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"},
        ]
        service = _build_mock_service({"root": [_make_list_response(files)]})
        result = list_files(service, "root")

        assert len(result) == 3
        assert all(isinstance(f, DriveFile) for f in result)
        assert result[0].name == "stmt.pdf"
        assert result[1].name == "stmt.csv"
        assert result[2].name == "stmt.xlsx"

    def test_unsupported_mime_types_are_filtered(self):
        files = [
            {"id": "1", "name": "stmt.pdf", "mimeType": "application/pdf"},
            {"id": "2", "name": "photo.jpg", "mimeType": "image/jpeg"},
            {"id": "3", "name": "doc.docx", "mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
        ]
        service = _build_mock_service({"root": [_make_list_response(files)]})
        result = list_files(service, "root")

        assert len(result) == 1
        assert result[0].name == "stmt.pdf"

    def test_recursive_subfolder_scanning(self):
        root_files = [
            {"id": "sub1", "name": "subfolder", "mimeType": FOLDER_MIME_TYPE},
            {"id": "1", "name": "root.csv", "mimeType": "text/csv"},
        ]
        sub_files = [
            {"id": "2", "name": "nested.pdf", "mimeType": "application/pdf"},
        ]
        service = _build_mock_service({
            "root": [_make_list_response(root_files)],
            "sub1": [_make_list_response(sub_files)],
        })
        result = list_files(service, "root")

        assert len(result) == 2
        names = {f.name for f in result}
        assert names == {"root.csv", "nested.pdf"}

    def test_deeply_nested_folders(self):
        service = _build_mock_service({
            "root": [_make_list_response([
                {"id": "l1", "name": "level1", "mimeType": FOLDER_MIME_TYPE},
            ])],
            "l1": [_make_list_response([
                {"id": "l2", "name": "level2", "mimeType": FOLDER_MIME_TYPE},
            ])],
            "l2": [_make_list_response([
                {"id": "1", "name": "deep.csv", "mimeType": "text/csv"},
            ])],
        })
        result = list_files(service, "root")

        assert len(result) == 1
        assert result[0].name == "deep.csv"

    def test_pagination(self):
        page1_files = [
            {"id": "1", "name": "first.pdf", "mimeType": "application/pdf"},
        ]
        page2_files = [
            {"id": "2", "name": "second.csv", "mimeType": "text/csv"},
        ]
        service = _build_mock_service({
            "root": [
                _make_list_response(page1_files, next_page_token="token2"),
                _make_list_response(page2_files),
            ],
        })
        result = list_files(service, "root")

        assert len(result) == 2
        names = {f.name for f in result}
        assert names == {"first.pdf", "second.csv"}

    def test_xls_mime_type_supported(self):
        files = [
            {"id": "1", "name": "old.xls", "mimeType": "application/vnd.ms-excel"},
        ]
        service = _build_mock_service({"root": [_make_list_response(files)]})
        result = list_files(service, "root")

        assert len(result) == 1
        assert result[0].mime_type == "application/vnd.ms-excel"

    def test_drive_file_fields(self):
        files = [
            {"id": "abc123", "name": "stmt.pdf", "mimeType": "application/pdf"},
        ]
        service = _build_mock_service({"root": [_make_list_response(files)]})
        result = list_files(service, "root")

        f = result[0]
        assert f.id == "abc123"
        assert f.name == "stmt.pdf"
        assert f.mime_type == "application/pdf"
        assert f.local_path == ""


import json
import os
import tempfile

from src.drive import (
    download_files,
    filter_new_files,
    load_processed_state,
    save_processed_state,
)


class TestFilterNewFiles:
    """Tests for filter_new_files()."""

    def test_all_new(self):
        files = [DriveFile(id="1", name="a.pdf", mime_type="application/pdf")]
        result = filter_new_files(files, set())
        assert len(result) == 1

    def test_all_processed(self):
        files = [DriveFile(id="1", name="a.pdf", mime_type="application/pdf")]
        result = filter_new_files(files, {"1"})
        assert result == []

    def test_mixed(self):
        files = [
            DriveFile(id="1", name="a.pdf", mime_type="application/pdf"),
            DriveFile(id="2", name="b.csv", mime_type="text/csv"),
            DriveFile(id="3", name="c.xlsx", mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ]
        result = filter_new_files(files, {"1", "3"})
        assert len(result) == 1
        assert result[0].id == "2"


class TestProcessedState:
    """Tests for load_processed_state() and save_processed_state()."""

    def test_load_missing_file(self):
        result = load_processed_state("/nonexistent/path.json")
        assert result == set()

    def test_save_and_load(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_processed_state(path, {"a", "b", "c"})
            loaded = load_processed_state(path)
            assert loaded == {"a", "b", "c"}
        finally:
            os.unlink(path)

    def test_load_corrupted_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("not valid json{{{")
            path = f.name
        try:
            result = load_processed_state(path)
            assert result == set()
        finally:
            os.unlink(path)

    def test_save_creates_sorted_json(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            path = f.name
        try:
            save_processed_state(path, {"c", "a", "b"})
            with open(path) as f:
                data = json.load(f)
            assert data == ["a", "b", "c"]
        finally:
            os.unlink(path)


class TestDownloadFiles:
    """Tests for download_files()."""

    def test_successful_download(self):
        service = MagicMock()
        file_content = b"fake pdf content"

        mock_request = MagicMock()
        service.files.return_value.get_media.return_value = mock_request

        # Simulate MediaIoBaseDownload behavior
        with patch("src.drive.MediaIoBaseDownload") as mock_dl_class:
            mock_downloader = MagicMock()
            mock_downloader.next_chunk.return_value = (None, True)
            mock_dl_class.return_value = mock_downloader

            files = [DriveFile(id="1", name="stmt.pdf", mime_type="application/pdf")]

            with tempfile.TemporaryDirectory() as tmpdir:
                result = download_files(service, files, tmpdir)

                assert len(result) == 1
                assert result[0].local_path == os.path.join(tmpdir, "stmt.pdf")
                service.files.return_value.get_media.assert_called_once_with(fileId="1")

    def test_download_failure_skips_file(self):
        service = MagicMock()
        service.files.return_value.get_media.side_effect = Exception("API error")

        files = [DriveFile(id="1", name="bad.pdf", mime_type="application/pdf")]

        with tempfile.TemporaryDirectory() as tmpdir:
            result = download_files(service, files, tmpdir)
            assert result == []

    def test_multiple_files_partial_failure(self):
        service = MagicMock()

        call_count = 0

        def get_media_side_effect(fileId):
            nonlocal call_count
            call_count += 1
            if fileId == "bad":
                raise Exception("fail")
            return MagicMock()

        service.files.return_value.get_media.side_effect = get_media_side_effect

        with patch("src.drive.MediaIoBaseDownload") as mock_dl_class:
            mock_downloader = MagicMock()
            mock_downloader.next_chunk.return_value = (None, True)
            mock_dl_class.return_value = mock_downloader

            files = [
                DriveFile(id="good1", name="a.pdf", mime_type="application/pdf"),
                DriveFile(id="bad", name="b.pdf", mime_type="application/pdf"),
                DriveFile(id="good2", name="c.csv", mime_type="text/csv"),
            ]

            with tempfile.TemporaryDirectory() as tmpdir:
                result = download_files(service, files, tmpdir)

                assert len(result) == 2
                assert result[0].name == "a.pdf"
                assert result[1].name == "c.csv"

    def test_download_creates_directory(self):
        service = MagicMock()

        with patch("src.drive.MediaIoBaseDownload") as mock_dl_class:
            mock_downloader = MagicMock()
            mock_downloader.next_chunk.return_value = (None, True)
            mock_dl_class.return_value = mock_downloader

            files = [DriveFile(id="1", name="stmt.pdf", mime_type="application/pdf")]

            with tempfile.TemporaryDirectory() as tmpdir:
                nested = os.path.join(tmpdir, "sub", "dir")
                result = download_files(service, files, nested)

                assert len(result) == 1
                assert os.path.isdir(nested)
