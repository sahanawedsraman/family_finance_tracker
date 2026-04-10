"""Finance Tracker - CLI entry point.

Implements the full pipeline:
load config -> authenticate -> download -> parse -> categorize -> compute metrics -> write sheets
"""

import argparse
import logging
import os
import sys
import tempfile

from src.auth import build_drive_service, build_sheets_service, get_credentials
from src.categorizer import categorize_transactions
from src.config import load_config
from src.drive import (
    download_files,
    filter_new_files,
    list_files,
    load_processed_state,
    save_processed_state,
)
from src.logging_config import RunSummary, setup_logging
from src.metrics import compute_kpis, compute_monthly_metrics, compute_annual_metrics
from src.parser import parse_file
from src.models import Transaction
from src.sheets import (
    create_or_get_sheet,
    read_existing_transactions,
    write_to_sheet,
    write_transactions_tab,
)

logger = logging.getLogger(__name__)

STATE_FILE = "processed_files.json"


def parse_args(argv=None):
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Finance Tracker: pull statements from Google Drive, "
            "parse them, and write metrics to Google Sheets."
        ),
    )
    parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="Path to the YAML configuration file",
    )
    return parser.parse_args(argv)


def run_pipeline(config_path="config.yaml"):
    """Run the full finance tracker pipeline.

    Returns:
        RunSummary with stats from the run.
    """
    # Step 1: Load configuration
    config = load_config(config_path)

    # Step 2: Set up logging
    setup_logging(log_level=config.log_level)
    logger.info("Starting Finance Tracker pipeline")

    summary = RunSummary()

    # Step 3: Authenticate with Google APIs
    try:
        creds = get_credentials()
        drive_service = build_drive_service(creds)
        sheets_service = build_sheets_service(creds)
    except Exception as e:
        logger.error("Authentication failed: %s", e)
        summary.add_error(f"Authentication failed: {e}")
        return summary

    # Step 4: List files from Google Drive
    try:
        all_drive_files = list_files(drive_service, config.drive_folder_id)
        logger.info("Found %d files in Drive folder", len(all_drive_files))
    except Exception as e:
        logger.error("Failed to list Drive files: %s", e)
        summary.add_error(f"Failed to list Drive files: {e}")
        return summary

    # Step 5: Filter out already-processed files
    processed_ids = load_processed_state(STATE_FILE)
    new_files = filter_new_files(all_drive_files, processed_ids)
    summary.files_skipped = len(all_drive_files) - len(new_files)
    logger.info(
        "%d new files to process, %d already processed",
        len(new_files),
        summary.files_skipped,
    )

    # Step 6: Download new files
    all_transactions = []
    newly_processed_ids = set()

    if new_files:
        with tempfile.TemporaryDirectory() as tmp_dir:
            downloaded = download_files(drive_service, new_files, tmp_dir)
            summary.files_processed = len(downloaded)

            # Step 7: Parse each downloaded file
            person_patterns = {
                p.name: p.patterns for p in config.persons
            }
            for df in downloaded:
                try:
                    txns = parse_file(
                        file_path=df.local_path,
                        file_name=df.name,
                        mime_type=df.mime_type,
                        person_patterns=person_patterns,
                        gemini_config=config.gemini,
                        folder_path=df.folder_path,
                    )
                    all_transactions.extend(txns)
                    newly_processed_ids.add(df.id)
                except Exception as e:
                    msg = f"Failed to parse {df.name}: {e}"
                    logger.warning(msg)
                    summary.add_error(msg)
    else:
        logger.info("No new files found")

    summary.transactions_found = len(all_transactions)

    # Step 7b: Read manual entries from the Sheet
    manual_txns = []
    if config.sheet_id:
        try:
            from src.sheets import read_manual_entries

            manual_txns = read_manual_entries(sheets_service, config.sheet_id)
            if manual_txns:
                logger.info("Read %d manual entries from Sheet", len(manual_txns))
        except Exception as e:
            logger.warning("Failed to read manual entries: %s", e)

    # Step 7c: Deduplicate — if manual entries cover pay details for a month,
    # remove payroll-related deposits from bank statements for that month
    if manual_txns:
        # Find months that have manual Income entries (pay stubs entered manually)
        pay_categories = {"Income", "Taxes", "Retirement", "Healthcare", "Insurance"}
        manual_pay_months = set()
        for t in manual_txns:
            if t.category in pay_categories:
                manual_pay_months.add(t.date.strftime("%Y-%m"))

        if manual_pay_months:
            # Filter out payroll deposits from bank statements for those months
            # Payroll deposits are typically large positive amounts categorized as
            # Income or Transfer from bank statement parsing
            before_count = len(all_transactions)
            payroll_keywords = [
                "payroll", "direct dep", "salary", "wage", "ach deposit",
                "employer", "company pay", "net pay",
            ]

            def _is_payroll_deposit(txn):
                if txn.source_file == "Manual Entry":
                    return False
                month = txn.date.strftime("%Y-%m")
                if month not in manual_pay_months:
                    return False
                if txn.amount <= 0:
                    return False
                desc_lower = txn.description.lower()
                return any(kw in desc_lower for kw in payroll_keywords)

            all_transactions = [t for t in all_transactions if not _is_payroll_deposit(t)]
            removed = before_count - len(all_transactions)
            if removed:
                logger.info(
                    "Removed %d payroll deposits from bank statements (covered by manual entries)",
                    removed,
                )

        all_transactions.extend(manual_txns)
        summary.transactions_found = len(all_transactions)

    # Notify user about failed files
    failed_files = [e for e in (summary.errors or []) if "Failed to parse" in e]
    if failed_files:
        print("\n⚠️  Some files could not be parsed automatically.")
        print("You can enter their data manually in the 'Manual Entry' tab of your Google Sheet.")
        print("Columns: Date | Description | Amount | Category | Person")
        print("Example: 2025-01-15 | Gross Pay | 5000 | Income | Raman")
        print("         2025-01-15 | Federal Tax | -800 | Taxes | Raman")
        print("         2025-01-15 | 401k | -500 | Retirement | Raman\n")

    # Step 8: Categorize only NEW transactions (not ones already in the Sheet)
    if all_transactions and config.gemini and config.gemini.enabled:
        from src.llm_parser import categorize_with_gemini

        categorize_with_gemini(
            all_transactions,
            api_key=config.gemini.api_key,
            model=config.gemini.model,
            fallback_models=config.gemini.fallback_models,
        )
        if config.categories:
            from src.categorizer import match_category

            for txn in all_transactions:
                if txn.category == "Other":
                    txn.category = match_category(txn.description, config.categories)
    elif all_transactions:
        categorize_transactions(all_transactions, config.categories)

    # Step 9: Write transactions (merges with existing, preserves manual edits)
    try:
        spreadsheet_id = create_or_get_sheet(
            sheets_service, config.sheet_id, config.sheet_name
        )

        write_transactions_tab(sheets_service, spreadsheet_id, all_transactions)

        # Step 9b: Read back ALL merged transactions (includes manual category edits)
        from datetime import datetime as _dt

        merged_rows = read_existing_transactions(sheets_service, spreadsheet_id)
        all_merged_txns = []
        for r in merged_rows:
            date_str = r.get("Date", "")
            if not date_str:
                continue
            try:
                txn_date = _dt.strptime(date_str, "%Y-%m-%d").date()
            except ValueError:
                continue
            try:
                amount = float(str(r.get("Amount", 0)).replace(",", "").replace("$", ""))
            except ValueError:
                continue
            from src.models import Transaction as _Txn

            all_merged_txns.append(Transaction(
                date=txn_date,
                description=r.get("Description", ""),
                amount=amount,
                category=r.get("Category", "Other"),
                person=r.get("Person", "Unknown"),
                source_file=r.get("Source File", ""),
                transaction_type=r.get("Type", ""),
            ))

        logger.info("Read back %d merged transactions for metrics", len(all_merged_txns))
        # Debug: show amount distribution
        pos = sum(1 for t in all_merged_txns if t.amount > 0)
        neg = sum(1 for t in all_merged_txns if t.amount < 0)
        zero = sum(1 for t in all_merged_txns if t.amount == 0)
        logger.info("Amount distribution: %d positive, %d negative, %d zero", pos, neg, zero)

    except Exception as e:
        msg = f"Failed to write/read transactions: {e}"
        logger.error(msg)
        summary.add_error(msg)
        return summary

    # Step 10: Compute metrics from merged transactions
    person_names = [p.name for p in config.persons]
    monthly_metrics = compute_monthly_metrics(
        all_merged_txns, config.budgets, person_names
    )
    annual_metrics = compute_annual_metrics(monthly_metrics, person_names)
    kpis = compute_kpis(monthly_metrics, config.budgets)

    # Step 11: Write remaining tabs
    try:
        sheet_id = write_to_sheet(
            service=sheets_service,
            sheet_id=spreadsheet_id,
            sheet_name=config.sheet_name,
            transactions=[],  # empty — already written in step 9
            monthly_metrics=monthly_metrics,
            annual_metrics=annual_metrics,
            kpis=kpis,
            budgets=config.budgets,
        )
        logger.info("Output written to Google Sheet: %s", sheet_id)
    except Exception as e:
        msg = f"Failed to write to Google Sheets: {e}"
        logger.error(msg)
        summary.add_error(msg)
        return summary

    # Step 11: Save processed file state after successful completion
    if newly_processed_ids:
        updated_ids = processed_ids | newly_processed_ids
        save_processed_state(STATE_FILE, updated_ids)
        logger.info("Saved processed state: %d total files", len(updated_ids))

    return summary


def main(argv=None):
    """Main entry point for the finance tracker pipeline."""
    args = parse_args(argv)
    config_path = args.config

    if not os.path.isfile(config_path):
        print(f"Error: Configuration file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    summary = run_pipeline(config_path)

    # Print end-of-run summary
    summary_text = summary.format_summary()
    logger.info("\n%s", summary_text)
    print(summary_text)

    if summary.errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
