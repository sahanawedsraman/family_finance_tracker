"""Google OAuth 2.0 authentication for Drive and Sheets APIs."""

import logging
import os
import stat

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def get_credentials(
    credentials_path: str = "credentials.json",
    token_path: str = "token.json",
) -> Credentials:
    """Load or create OAuth credentials.

    - If token_path exists and the token is valid, reuse it.
    - If the token is expired but has a refresh token, refresh it.
    - Otherwise, run the interactive OAuth flow using credentials_path.

    The resulting token is saved to token_path for future runs.
    """
    creds: Credentials | None = None

    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)

    if creds and creds.valid:
        logger.info("Loaded valid credentials from %s", token_path)
        return creds

    if creds and creds.expired and creds.refresh_token:
        logger.info("Refreshing expired credentials")
        creds.refresh(Request())
    else:
        logger.info("Running OAuth flow using %s", credentials_path)
        flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
        creds = flow.run_local_server(port=0)

    # Persist the token for the next run (owner-only permissions).
    with open(token_path, "w") as token_file:
        token_file.write(creds.to_json())
    os.chmod(token_path, stat.S_IRUSR | stat.S_IWUSR)
    logger.info("Saved credentials to %s", token_path)

    return creds


def build_drive_service(creds: Credentials):
    """Build and return a Google Drive API v3 service."""
    logger.info("Building Google Drive API service")
    return build("drive", "v3", credentials=creds)


def build_sheets_service(creds: Credentials):
    """Build and return a Google Sheets API v4 service."""
    logger.info("Building Google Sheets API service")
    return build("sheets", "v4", credentials=creds)
