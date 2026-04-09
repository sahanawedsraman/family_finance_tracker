"""Tests for Google OAuth authentication module."""

import json
import os
from unittest.mock import MagicMock, mock_open, patch

import pytest

from src.auth import SCOPES, build_drive_service, build_sheets_service, get_credentials


class TestScopes:
    """Verify the declared OAuth scopes."""

    def test_drive_readonly_scope(self):
        assert "https://www.googleapis.com/auth/drive.readonly" in SCOPES

    def test_sheets_readwrite_scope(self):
        assert "https://www.googleapis.com/auth/spreadsheets" in SCOPES

    def test_exactly_two_scopes(self):
        assert len(SCOPES) == 2


class TestGetCredentials:
    """Tests for get_credentials() covering token caching, refresh, and OAuth flow."""

    @patch("src.auth.Credentials")
    @patch("src.auth.os.path.exists", return_value=True)
    def test_returns_valid_cached_token(self, mock_exists, mock_creds_cls):
        """When token.json exists and the token is valid, return it directly."""
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        result = get_credentials(token_path="token.json")

        mock_creds_cls.from_authorized_user_file.assert_called_once_with("token.json", SCOPES)
        assert result is mock_creds

    @patch("builtins.open", new_callable=mock_open)
    @patch("src.auth.Request")
    @patch("src.auth.Credentials")
    @patch("src.auth.os.path.exists", return_value=True)
    def test_refreshes_expired_token(self, mock_exists, mock_creds_cls, mock_request_cls, mock_file):
        """When the cached token is expired but has a refresh token, refresh it."""
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh-tok"
        mock_creds.to_json.return_value = '{"token": "refreshed"}'
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        result = get_credentials(token_path="token.json")

        mock_creds.refresh.assert_called_once_with(mock_request_cls())
        mock_file.assert_called_once_with("token.json", "w")
        assert result is mock_creds

    @patch("builtins.open", new_callable=mock_open)
    @patch("src.auth.InstalledAppFlow")
    @patch("src.auth.os.path.exists", return_value=False)
    def test_runs_oauth_flow_when_no_token(self, mock_exists, mock_flow_cls, mock_file):
        """When no token.json exists, run the full OAuth flow."""
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "new"}'
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_creds
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        result = get_credentials(
            credentials_path="credentials.json",
            token_path="token.json",
        )

        mock_flow_cls.from_client_secrets_file.assert_called_once_with("credentials.json", SCOPES)
        mock_flow.run_local_server.assert_called_once_with(port=0)
        mock_file.assert_called_once_with("token.json", "w")
        assert result is mock_creds

    @patch("builtins.open", new_callable=mock_open)
    @patch("src.auth.InstalledAppFlow")
    @patch("src.auth.Credentials")
    @patch("src.auth.os.path.exists", return_value=True)
    def test_runs_oauth_flow_when_token_invalid_no_refresh(
        self, mock_exists, mock_creds_cls, mock_flow_cls, mock_file
    ):
        """When token exists but is invalid and has no refresh token, run OAuth flow."""
        stale_creds = MagicMock()
        stale_creds.valid = False
        stale_creds.expired = False
        stale_creds.refresh_token = None
        mock_creds_cls.from_authorized_user_file.return_value = stale_creds

        new_creds = MagicMock()
        new_creds.to_json.return_value = '{"token": "brand-new"}'
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = new_creds
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        result = get_credentials(token_path="token.json")

        mock_flow.run_local_server.assert_called_once()
        assert result is new_creds

    @patch("builtins.open", new_callable=mock_open)
    @patch("src.auth.InstalledAppFlow")
    @patch("src.auth.os.path.exists", return_value=False)
    def test_saves_token_after_oauth_flow(self, mock_exists, mock_flow_cls, mock_file):
        """Verify the token is written to disk after a fresh OAuth flow."""
        mock_creds = MagicMock()
        mock_creds.to_json.return_value = '{"token": "saved"}'
        mock_flow = MagicMock()
        mock_flow.run_local_server.return_value = mock_creds
        mock_flow_cls.from_client_secrets_file.return_value = mock_flow

        get_credentials(token_path="my_token.json")

        mock_file.assert_called_once_with("my_token.json", "w")
        mock_file().write.assert_called_once_with('{"token": "saved"}')

    @patch("src.auth.os.path.exists", return_value=True)
    def test_uses_custom_paths(self, mock_exists):
        """Verify custom credentials_path and token_path are forwarded."""
        with patch("src.auth.Credentials") as mock_creds_cls:
            mock_creds = MagicMock()
            mock_creds.valid = True
            mock_creds_cls.from_authorized_user_file.return_value = mock_creds

            get_credentials(
                credentials_path="custom_creds.json",
                token_path="custom_token.json",
            )

            mock_creds_cls.from_authorized_user_file.assert_called_once_with(
                "custom_token.json", SCOPES
            )


class TestBuildServices:
    """Tests for service builder helpers."""

    @patch("src.auth.build")
    def test_build_drive_service(self, mock_build):
        creds = MagicMock()
        build_drive_service(creds)
        mock_build.assert_called_once_with("drive", "v3", credentials=creds)

    @patch("src.auth.build")
    def test_build_sheets_service(self, mock_build):
        creds = MagicMock()
        build_sheets_service(creds)
        mock_build.assert_called_once_with("sheets", "v4", credentials=creds)

    @patch("src.auth.build")
    def test_drive_service_returns_build_result(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        result = build_drive_service(MagicMock())
        assert result is mock_service

    @patch("src.auth.build")
    def test_sheets_service_returns_build_result(self, mock_build):
        mock_service = MagicMock()
        mock_build.return_value = mock_service
        result = build_sheets_service(MagicMock())
        assert result is mock_service
