"""Google Drive file downloader with recursive folder scanning and state tracking."""

import io
import json
import logging
import os
from dataclasses import dataclass

from googleapiclient.http import MediaIoBaseDownload

from src.retry import retry_api_call

logger = logging.getLogger(__name__)

# Supported MIME types for bank/credit card statements
SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "text/csv",
    "application/csv",
    "text/comma-separated-values",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",  # .xlsx
    "application/vnd.ms-excel",  # .xls
}

FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


@dataclass
class DriveFile:
    """Represents a file discovered in Google Drive."""

    id: str
    name: str
    mime_type: str
    local_path: str = ""
    folder_path: str = ""  # e.g. "Raman/Chase" for nested folders


def load_processed_state(state_path: str) -> set[str]:
    """Load set of previously processed file IDs from a JSON file."""
    if not os.path.exists(state_path):
        return set()
    try:
        with open(state_path, "r") as f:
            data = json.load(f)
        return set(data)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Corrupted state file %s, starting fresh", state_path)
        return set()


def save_processed_state(state_path: str, processed_ids: set[str]) -> None:
    """Save the set of processed file IDs to a JSON file."""
    with open(state_path, "w") as f:
        json.dump(sorted(processed_ids), f, indent=2)


def filter_new_files(files: list[DriveFile], processed_ids: set[str]) -> list[DriveFile]:
    """Return only files that haven't been processed yet."""
    return [f for f in files if f.id not in processed_ids]


def download_files(service, files: list[DriveFile], download_dir: str) -> list[DriveFile]:
    """Download files to a local directory. Returns list with local_path populated."""
    os.makedirs(download_dir, exist_ok=True)
    downloaded: list[DriveFile] = []
    for drive_file in files:
        local_path = os.path.join(download_dir, drive_file.name)
        try:
            def _download(df=drive_file, lp=local_path):
                request = service.files().get_media(fileId=df.id)
                with open(lp, "wb") as fh:
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        _, done = downloader.next_chunk()

            retry_api_call(_download)
            drive_file.local_path = local_path
            downloaded.append(drive_file)
            logger.info("Downloaded %s to %s", drive_file.name, local_path)
        except Exception:
            logger.warning("Failed to download %s", drive_file.name, exc_info=True)
    return downloaded


SHORTCUT_MIME_TYPE = "application/vnd.google-apps.shortcut"


def list_files(service, folder_id: str) -> list[DriveFile]:
    """Recursively list all supported files in a Drive folder and its subfolders."""
    results: list[DriveFile] = []
    _list_files_recursive(service, folder_id, results, folder_path="")
    return results


def _list_files_recursive(service, folder_id: str, results: list[DriveFile], folder_path: str = "") -> None:
    """Recursively scan a folder, collecting supported files and descending into subfolders.
    Also follows Google Drive shortcuts to folders and files."""
    page_token = None
    while True:
        def _list_page(pt=page_token):
            return (
                service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name, mimeType, shortcutDetails)",
                    pageToken=pt,
                )
                .execute()
            )

        response = retry_api_call(_list_page)

        for item in response.get("files", []):
            mime = item["mimeType"]

            # Follow shortcuts
            if mime == SHORTCUT_MIME_TYPE:
                shortcut = item.get("shortcutDetails", {})
                target_id = shortcut.get("targetId")
                target_mime = shortcut.get("targetMimeType", "")
                if not target_id:
                    continue
                if target_mime == FOLDER_MIME_TYPE:
                    subfolder_path = f"{folder_path}/{item['name']}" if folder_path else item["name"]
                    _list_files_recursive(service, target_id, results, subfolder_path)
                elif target_mime in SUPPORTED_MIME_TYPES:
                    results.append(
                        DriveFile(
                            id=target_id,
                            name=item["name"],
                            mime_type=target_mime,
                            folder_path=folder_path,
                        )
                    )
            elif mime == FOLDER_MIME_TYPE:
                subfolder_path = f"{folder_path}/{item['name']}" if folder_path else item["name"]
                _list_files_recursive(service, item["id"], results, subfolder_path)
            elif mime in SUPPORTED_MIME_TYPES:
                results.append(
                    DriveFile(
                        id=item["id"],
                        name=item["name"],
                        mime_type=mime,
                        folder_path=folder_path,
                    )
                )

        page_token = response.get("nextPageToken")
        if not page_token:
            break
