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
    BUDGET_EXCLUDED_CATEGORIES,
    append_manual_entries,
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
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be processed without writing to Sheets",
    )
    parser.add_argument(
        "--learn-categories",
        action="store_true",
        help="Suggest new keywords based on recategorized transactions",
    )
    parser.add_argument(
        "--reprocess",
        action="store_true",
        help="Clear processed files state and reprocess everything",
    )
    parser.add_argument(
        "--add-paystub",
        action="store_true",
        help="Interactively add a pay stub to the Manual Entry tab",
    )
    parser.add_argument(
        "--no-report",
        action="store_true",
        help="Suppress the monthly finance report after the pipeline run",
    )
    parser.add_argument(
        "--ui",
        action="store_true",
        help="Launch local web UI for monthly updates",
    )
    return parser.parse_args(argv)


def run_pipeline(config_path="config.yaml", dry_run=False, show_report=True):
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

    # Step 8: Categorize new transactions using keyword matching
    # Skip manual entries — they already have correct categories from the Manual Entry tab
    if all_transactions and config.categories:
        file_transactions = [t for t in all_transactions if t.source_file != "Manual Entry"]
        categorize_transactions(file_transactions, config.categories)

    # Step 9: Write transactions (merges with existing, preserves manual edits)
    if dry_run:
        print(f"Would process {len(all_transactions)} new transactions")
        print(f"Categories: {dict(sorted({t.category: sum(1 for x in all_transactions if x.category == t.category) for t in all_transactions}.items(), key=lambda x: -x[1]))}")
        return summary

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

    # Step 12: Print monthly finance report
    if show_report and monthly_metrics:
        _print_monthly_report(monthly_metrics, kpis, config.budgets)

    return summary


def _extract_keyword(description: str) -> str:
    """Extract a meaningful keyword from a transaction description.

    Strips common prefixes (pos, recurring, ach, debit card, etc.),
    removes dates, card numbers, transaction IDs, and special characters,
    then returns the first 2-3 distinctive words lowercased.
    """
    import re

    desc = description.lower().strip()

    # Strip common transaction prefixes
    prefixes = [
        r"^pos\s+",
        r"^recurring\s+",
        r"^ach\s+",
        r"^debit card\s+",
        r"^credit card\s+",
        r"^check card\s+",
        r"^purchase\s+",
        r"^point of sale\s+",
        r"^online\s+",
        r"^pre-auth\s+",
        r"^pending\s+",
        r"^external\s+",
        r"^visa\s+",
        r"^mastercard\s+",
    ]
    for prefix in prefixes:
        desc = re.sub(prefix, "", desc)

    # Remove dates in various formats (MM/DD/YYYY, MM/DD, YYYY-MM-DD, etc.)
    desc = re.sub(r"\d{1,2}/\d{1,2}(/\d{2,4})?", "", desc)
    desc = re.sub(r"\d{4}-\d{2}-\d{2}", "", desc)

    # Remove card numbers and transaction IDs (sequences of 4+ digits)
    desc = re.sub(r"\b\d{4,}\b", "", desc)
    # Remove trailing reference numbers like #1234
    desc = re.sub(r"#\w+", "", desc)

    # Remove special characters but keep spaces and hyphens between words
    desc = re.sub(r"[^a-z\s\-]", " ", desc)

    # Collapse whitespace
    desc = re.sub(r"\s+", " ", desc).strip()

    # Split into words and take first 2-3 meaningful ones
    words = [w for w in desc.split() if len(w) > 1]

    if len(words) >= 3:
        return " ".join(words[:3])
    elif len(words) >= 1:
        return " ".join(words[:2]) if len(words) >= 2 else words[0]
    else:
        return description.lower().strip()[:30]


def _update_config_file(config_path: str, accepted: dict[str, list[str]]) -> None:
    """Update config.yaml by inserting new keywords into existing category lines.

    Preserves the flow-style list format (single-line bracketed lists).
    """
    import re

    with open(config_path, "r") as f:
        lines = f.readlines()

    for category, new_keywords in accepted.items():
        if not new_keywords:
            continue

        for i, line in enumerate(lines):
            # Match lines like:  Groceries: ["walmart", "costco", ...]
            pattern = rf"^(\s*{re.escape(category)}\s*:\s*\[)(.*?)(\]\s*)$"
            m = re.match(pattern, line)
            if m:
                prefix = m.group(1)
                existing_content = m.group(2)
                suffix = m.group(3)

                # Build the new keyword entries
                additions = ", ".join(f'"{kw}"' for kw in new_keywords)

                # Append to existing content
                if existing_content.strip():
                    new_content = f"{existing_content}, {additions}"
                else:
                    new_content = additions

                lines[i] = f"{prefix}{new_content}{suffix}\n"
                break

    with open(config_path, "w") as f:
        f.writelines(lines)


def learn_categories(config_path: str) -> None:
    """Interactively learn new category keywords from manually recategorized transactions.

    Reads transactions from the Google Sheet, finds mismatches between the
    sheet category and what keyword matching would assign, extracts keywords,
    and interactively prompts the user to accept or reject each suggestion.
    Accepted keywords are written directly to config.yaml.
    """
    from collections import Counter
    from src.categorizer import match_category

    config = load_config(config_path)
    creds = get_credentials()
    sheets_service = build_sheets_service(creds)

    print("Reading transactions from sheet...")
    txns = read_existing_transactions(sheets_service, config.sheet_id)

    if not txns:
        print("No transactions found in the sheet.")
        return

    # Find mismatches: sheet category != keyword-matched category
    # Track keyword -> (category, count) for frequency info
    suggestions: dict[str, dict[str, Counter]] = {}  # {category: Counter({keyword: count})}

    for t in txns:
        cat = t.get("Category", "Other")
        desc = t.get("Description", "")
        if not cat or cat == "Other" or not desc:
            continue
        matched = match_category(desc, config.categories)
        if matched != cat:
            keyword = _extract_keyword(desc)
            if not keyword:
                continue
            if cat not in suggestions:
                suggestions[cat] = Counter()
            suggestions[cat][keyword] += 1

    if not suggestions:
        print("All transactions match their keywords. Nothing to learn.")
        return

    # Filter out keywords that already exist in the config
    filtered_suggestions: dict[str, list[tuple[str, int]]] = {}
    for cat in sorted(suggestions):
        existing = [k.lower() for k in config.categories.get(cat, [])]
        new_items = []
        for keyword, count in suggestions[cat].most_common():
            # Skip if keyword already exists or is a substring of an existing keyword
            already_covered = any(
                keyword in ex or ex in keyword for ex in existing
            )
            if not already_covered:
                new_items.append((keyword, count))
        if new_items:
            filtered_suggestions[cat] = new_items

    if not filtered_suggestions:
        print("All mismatched transactions are already covered by existing keywords.")
        return

    total_suggestions = sum(len(v) for v in filtered_suggestions.values())
    print(f"\nFound {total_suggestions} keyword suggestions across {len(filtered_suggestions)} categories.\n")

    # Interactive prompting
    accepted: dict[str, list[str]] = {}
    quit_all = False

    for cat in sorted(filtered_suggestions):
        if quit_all:
            break

        print(f"--- {cat} ---")
        skip_category = False

        for keyword, count in filtered_suggestions[cat]:
            if skip_category or quit_all:
                break

            times_str = "time" if count == 1 else "times"
            response = input(
                f'  Add "{keyword}" -> {cat}? (seen {count} {times_str}) [y/n/s/q]: '
            ).strip().lower()

            if response in ("y", "yes", ""):
                if cat not in accepted:
                    accepted[cat] = []
                accepted[cat].append(keyword)
            elif response in ("s", "skip"):
                skip_category = True
            elif response in ("q", "quit"):
                quit_all = True
            # 'n' or anything else = skip this keyword

        print()

    # Apply accepted keywords to config.yaml
    if not accepted:
        print("No keywords accepted. Config unchanged.")
        return

    _update_config_file(config_path, accepted)

    # Print summary
    print("=" * 50)
    for cat, keywords in sorted(accepted.items()):
        print(f"  Added {len(keywords)} keyword(s) to {cat}")
    total_added = sum(len(v) for v in accepted.values())
    print(f"\nUpdated {config_path} with {total_added} new keyword(s).")


def _print_monthly_report(monthly_metrics, kpis, budgets):
    """Print a formatted monthly financial report to the console.

    Focuses on the latest month in monthly_metrics. Shows income/spending summary,
    budget progress bars, month-over-month trends, and insights.
    """
    from datetime import datetime as _dt

    # Get the latest month's combined metrics
    combined = [m for m in monthly_metrics if m.person == "Combined"]
    if not combined:
        return

    sorted_combined = sorted(combined, key=lambda m: m.month)
    latest = sorted_combined[-1]

    # Parse month for display
    try:
        month_date = _dt.strptime(latest.month, "%Y-%m")
        month_display = month_date.strftime("%B %Y")
    except ValueError:
        month_display = latest.month

    # Compute display values
    income = latest.total_income
    spending = abs(latest.total_expenses)
    saved = latest.net_savings
    savings_rate = max(latest.savings_rate, 0.0)

    # Build the report header
    title = f"\U0001f4b0 Monthly Finance Report — {month_display}"
    box_width = max(len(title) + 10, 56)
    inner_width = box_width - 2  # account for border characters

    lines = []
    lines.append(f"╭{'─' * inner_width}╮")
    lines.append(f"│{title:^{inner_width}}│")
    lines.append(f"╯{'─' * inner_width}╰")
    lines.append("")

    # Income / Spending / Saved / Savings Rate summary
    lines.append(
        f"  Income          ${income:,.0f}"
        f"{'':6s}Spending        ${spending:,.0f}"
    )
    lines.append(
        f"  Saved           ${saved:,.0f}"
        f"{'':6s}Savings Rate    {savings_rate:.1f}%"
    )
    lines.append("")

    # Budget Status section
    budget_cats = []
    for cat, budget_amt in budgets.items():
        if cat in BUDGET_EXCLUDED_CATEGORIES:
            continue
        if budget_amt <= 0:
            continue
        actual = latest.category_spending.get(cat, 0)
        if actual <= 0:
            continue
        pct = actual / budget_amt * 100
        budget_cats.append((cat, actual, budget_amt, pct))

    # Sort by utilization descending
    budget_cats.sort(key=lambda x: x[3], reverse=True)

    if budget_cats:
        lines.append(f"  ── Budget Status {'─' * 40}")

        # Find longest category name for alignment
        max_cat_len = max(len(cat) for cat, _, _, _ in budget_cats)
        max_cat_len = max(max_cat_len, 12)

        for cat, actual, budget_amt, pct in budget_cats:
            # Build progress bar (11 chars wide)
            bar_width = 11
            filled = min(int(pct / 100 * bar_width), bar_width)
            empty = bar_width - filled
            bar = "█" * filled + "░" * empty

            # Over-budget warning
            warning = " ⚠" if pct > 100 else ""

            # Format the line
            spent_str = f"${actual:,.0f}"
            budget_str = f"${budget_amt:,.0f}"
            amount_part = f"{spent_str} / {budget_str}"
            lines.append(
                f"  {cat:<{max_cat_len}}  {amount_part:<20s}{bar}  {pct:.0f}%{warning}"
            )

        lines.append("")

    # Month-over-Month section
    if kpis.mom_trends:
        sorted_months = sorted(kpis.mom_trends.keys())
        latest_month_key = latest.month

        # Spending MoM from kpis.mom_trends
        spending_mom = kpis.mom_trends.get(latest_month_key)

        # Income MoM: compute from the two latest combined metrics
        income_mom = None
        if len(sorted_combined) >= 2:
            prev_income = sorted_combined[-2].total_income
            curr_income = sorted_combined[-1].total_income
            if prev_income > 0:
                income_mom = (curr_income - prev_income) / prev_income * 100

        if spending_mom is not None or income_mom is not None:
            lines.append(f"  ── Month-over-Month {'─' * 37}")

            if spending_mom is not None:
                arrow = "↑" if spending_mom >= 0 else "↓"
                lines.append(
                    f"  Spending: {arrow} {abs(spending_mom):.1f}% vs last month"
                )

            if income_mom is not None:
                arrow = "↑" if income_mom >= 0 else "↓"
                lines.append(
                    f"  Income: {arrow} {abs(income_mom):.1f}% vs last month"
                )

            lines.append("")

    # Insights section
    if kpis.insights:
        lines.append(f"  ── Insights {'─' * 45}")
        for insight in kpis.insights[:3]:
            lines.append(f"  • {insight}")
        lines.append("")

    # Print the full report
    print("\n" + "\n".join(lines))


def _print_other_summary(config_path: str) -> None:
    """Print a summary of uncategorized transactions."""
    try:
        config = load_config(config_path)
        creds = get_credentials()
        sheets = build_sheets_service(creds)
        txns = read_existing_transactions(sheets, config.sheet_id)
        others = [t for t in txns if t.get("Category") == "Other"]
        if others:
            print(f"\n⚠ {len(others)} transactions categorized as 'Other':")
            seen = set()
            for t in others[:15]:
                desc = t.get("Description", "")[:50]
                if desc not in seen:
                    print(f"  • {t.get('Date', '')} — {desc} — {t.get('Amount', '')}")
                    seen.add(desc)
            if len(others) > 15:
                print(f"  ... and {len(others) - 15} more")
            print("  Fix these in the Transactions tab of your Google Sheet.\n")
    except Exception:
        pass


def add_paystub(config_path: str) -> None:
    """Interactively collect pay stub details and write to the Manual Entry tab."""
    from datetime import date, datetime

    config = load_config(config_path)

    # 1. Prompt for person
    persons = config.persons
    print("\nSelect person:")
    for i, p in enumerate(persons, 1):
        print(f"  {i}. {p.name}")
    while True:
        choice = input("Person number: ").strip()
        try:
            idx = int(choice)
            if 1 <= idx <= len(persons):
                person = persons[idx - 1].name
                break
        except ValueError:
            pass
        print(f"  Please enter a number between 1 and {len(persons)}")

    # 2. Prompt for pay date
    today_str = date.today().strftime("%Y-%m-%d")
    date_input = input(f"Pay date [{today_str}]: ").strip()
    if not date_input:
        pay_date = today_str
    else:
        parsed = None
        for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                parsed = datetime.strptime(date_input, fmt).date()
                break
            except ValueError:
                continue
        if parsed is None:
            print("Invalid date format. Using today.")
            pay_date = today_str
        else:
            pay_date = parsed.strftime("%Y-%m-%d")

    def _parse_amount(prompt: str, required: bool = True) -> float:
        """Prompt for a positive dollar amount. Strips $ and commas."""
        while True:
            raw = input(prompt).strip().replace("$", "").replace(",", "")
            if not raw and not required:
                return 0.0
            try:
                val = float(raw)
                if val < 0:
                    print("  Please enter a positive number.")
                    continue
                return val
            except ValueError:
                if required:
                    print("  Please enter a valid number.")
                else:
                    return 0.0

    # 3-9. Prompt for amounts
    gross = _parse_amount("Gross pay: ")
    federal = _parse_amount("Federal tax: ")
    state = _parse_amount("State tax: ")
    fica = _parse_amount("Social Security + Medicare (FICA): ")
    retirement = _parse_amount("401k contribution: ")
    health = _parse_amount("Health insurance: ")
    other = _parse_amount("Other deductions (0 if none): ", required=False)

    # 10. Show summary
    net = gross - federal - state - fica - retirement - health - other
    print("\n" + "=" * 50)
    print(f"  Pay Stub Summary for {person} on {pay_date}")
    print("=" * 50)
    print(f"  Gross Pay:          ${gross:,.2f}")
    print(f"  Federal Tax:       -${federal:,.2f}")
    print(f"  State Tax:         -${state:,.2f}")
    print(f"  FICA:              -${fica:,.2f}")
    print(f"  401k:              -${retirement:,.2f}")
    print(f"  Health Insurance:  -${health:,.2f}")
    if other > 0:
        print(f"  Other Deductions:  -${other:,.2f}")
    print("-" * 50)
    print(f"  Net Take-Home:      ${net:,.2f}")
    print("=" * 50)

    confirm = input("\nWrite to Google Sheets? [y/N]: ").strip().lower()
    if confirm not in ("y", "yes"):
        print("Cancelled.")
        return

    # 11. Build rows and write
    entries = [
        [pay_date, "Gross Pay", str(gross), "Gross Pay", person],
        [pay_date, "Federal Tax", str(-federal), "Taxes", person],
        [pay_date, "State Tax", str(-state), "Taxes", person],
        [pay_date, "FICA", str(-fica), "Taxes", person],
        [pay_date, "401k", str(-retirement), "Retirement", person],
        [pay_date, "Health Insurance", str(-health), "Benefits", person],
    ]
    if other > 0:
        entries.append([pay_date, "Other Deductions", str(-other), "Benefits", person])

    creds = get_credentials()
    service = build_sheets_service(creds)
    append_manual_entries(service, config.sheet_id, entries)
    print(f"\nDone! {len(entries)} entries written to the Manual Entry tab.")


def _print_health_check(config_path: str) -> None:
    """Print missing data warnings and a next-steps checklist."""
    try:
        from datetime import date, timedelta

        config = load_config(config_path)
        creds = get_credentials()
        sheets_service = build_sheets_service(creds)

        # Read all transactions and manual entries
        txns = read_existing_transactions(sheets_service, config.sheet_id)
        from src.sheets import read_manual_entries
        manual_entries = read_manual_entries(sheets_service, config.sheet_id)

        today = date.today()
        current_month = today.strftime("%Y-%m")
        current_month_nice = today.strftime("%B %Y")

        # Also check previous month if we're in the first 5 days
        check_prev_month = today.day <= 5
        prev_month_date = today.replace(day=1) - timedelta(days=1)
        prev_month = prev_month_date.strftime("%Y-%m")
        prev_month_nice = prev_month_date.strftime("%B %Y")

        person_names = [p.name for p in config.persons]

        # --- Missing statement data ---
        # Find which persons have bank statement transactions (not manual) for current/prev month
        persons_with_current_data = set()
        persons_with_prev_data = set()
        for t in txns:
            date_str = t.get("Date", "")
            source = t.get("Source File", "")
            person = t.get("Person", "")
            if source == "Manual Entry" or not date_str or not person:
                continue
            month_str = date_str[:7]  # "YYYY-MM"
            if month_str == current_month:
                persons_with_current_data.add(person)
            if month_str == prev_month:
                persons_with_prev_data.add(person)

        missing_current = [p for p in person_names if p not in persons_with_current_data]
        missing_prev = []
        if check_prev_month:
            missing_prev = [p for p in person_names if p not in persons_with_prev_data]

        # --- Pay stub check ---
        pay_stub_info = {}  # person -> most recent date
        for entry in manual_entries:
            if entry.category in ("Income", "Gross Pay"):
                person = entry.person
                if person not in pay_stub_info or entry.date > pay_stub_info[person]:
                    pay_stub_info[person] = entry.date

        stale_pay_stubs = {}  # person -> last date (if > 45 days old)
        for person in person_names:
            last_date = pay_stub_info.get(person)
            if last_date and (today - last_date).days > 45:
                stale_pay_stubs[person] = last_date

        # --- Print warnings ---
        print()
        for person in missing_prev:
            print(f"  ⚠ No {person} bank statements found for {prev_month_nice}")

        for person in missing_current:
            print(f"  ⚠ No {person} bank statements found for {current_month_nice}")

        for person in person_names:
            last_date = pay_stub_info.get(person)
            if last_date:
                nice = last_date.strftime("%B %Y")
                print(f"  \U0001f4b0 Last pay stub: {person} — {nice}")
                if person in stale_pay_stubs:
                    print(f"     ↳ Consider adding {person}'s latest pay stub")

        # --- Next steps checklist ---
        checklist_items = []

        # Pipeline success (always true if we got here)
        checklist_items.append(("✓", "Pipeline ran successfully"))

        # Upload statements for missing persons (current month)
        for person in missing_current:
            checklist_items.append(("⬚", f"Upload {current_month_nice} statements for: {person}"))

        # Upload statements for missing persons (previous month)
        if check_prev_month:
            for person in missing_prev:
                checklist_items.append(("⬚", f"Upload {prev_month_nice} statements for: {person}"))

        # Pay stub actions
        for person, last_date in stale_pay_stubs.items():
            nice = last_date.strftime("%B %Y")
            checklist_items.append(("⬚", f"Add pay stub for: {person} (last: {nice})"))

        # All persons have current data
        if not missing_current:
            checklist_items.append(("✓", "All persons have current month data"))

        # Print checklist
        print(f"\n── Monthly Checklist ─────────────────────────────────────────")
        for mark, text in checklist_items:
            print(f"{mark} {text}")
        print("── Done ─────────────────────────────────────────────────────")

    except Exception as e:
        logger.debug("Health check skipped: %s", e)


def main(argv=None):
    """Main entry point for the finance tracker pipeline."""
    args = parse_args(argv)
    config_path = args.config

    if not os.path.isfile(config_path):
        print(f"Error: Configuration file not found: {config_path}", file=sys.stderr)
        sys.exit(1)

    if args.ui:
        from src.web import launch_ui
        launch_ui(config_path=config_path)
        return

    if args.add_paystub:
        add_paystub(config_path)
        return

    if args.learn_categories:
        learn_categories(config_path)
        return

    if args.reprocess:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
            print("Cleared processed files state. Reprocessing all files.")

    if args.dry_run:
        print("DRY RUN — no data will be written to Sheets\n")

    summary = run_pipeline(
        config_path, dry_run=args.dry_run, show_report=not args.no_report
    )

    # Print end-of-run summary
    summary_text = summary.format_summary()
    logger.info("\n%s", summary_text)
    print(summary_text)

    # Print "Other" transactions summary and health check
    if not args.dry_run:
        _print_other_summary(config_path)
        _print_health_check(config_path)

    if summary.errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
