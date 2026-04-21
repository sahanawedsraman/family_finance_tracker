"""Google Sheets data store for the Finance Tracker.

Uses Google Sheets purely as a database — no charts, no formatting.
All visualization is handled by the GitHub Pages dashboard.
"""

import logging
from datetime import datetime

from src.metrics import AnnualMetrics, KPIs, MonthlyMetrics
from src.models import Transaction
from src.retry import retry_api_call

logger = logging.getLogger(__name__)

# Tab names
TAB_TRANSACTIONS = "Transactions"
TAB_MONTHLY_SUMMARY = "Monthly Summary"
TAB_ANNUAL_SUMMARY = "Annual Summary"
TAB_CATEGORY_BREAKDOWN = "Category Breakdown"
TAB_KPIS = "KPIs"
TAB_BUDGET_STATUS = "Budget Status"
TAB_MANUAL_ENTRY = "Manual Entry"
TAB_METADATA = "Metadata"

ALL_TABS = [
    TAB_TRANSACTIONS,
    TAB_MONTHLY_SUMMARY,
    TAB_ANNUAL_SUMMARY,
    TAB_CATEGORY_BREAKDOWN,
    TAB_KPIS,
    TAB_BUDGET_STATUS,
    TAB_MANUAL_ENTRY,
    TAB_METADATA,
]

ALL_CATEGORIES = [
    "Groceries", "Dining", "Utilities", "Transportation", "Entertainment",
    "Healthcare", "Amazon", "Shopping", "Subscriptions", "Housing", "Travel",
    "Income", "Taxes", "Transfer", "Investment", "Retirement", "Insurance",
    "Education", "Childcare", "Fees", "Other",
]

MANUAL_ENTRY_CATEGORIES = ["Gross Pay", "Taxes", "Retirement", "Benefits"]

# Categories excluded from budget tracking
BUDGET_EXCLUDED_CATEGORIES = {"Income", "Taxes", "Retirement", "Investment", "Transfer", "Healthcare", "Utilities"}


# ── Core helpers ──

def create_or_get_sheet(service, sheet_id: str | None, name: str) -> str:
    """Create a new spreadsheet or ensure all tabs exist. Returns spreadsheet ID."""
    spreadsheets = service.spreadsheets()

    if sheet_id is None:
        body = {
            "properties": {"title": name},
            "sheets": [{"properties": {"title": tab}} for tab in ALL_TABS],
        }
        result = retry_api_call(lambda: spreadsheets.create(body=body).execute())
        new_id = result["spreadsheetId"]
        logger.info("Created spreadsheet '%s': %s", name, new_id)
        return new_id

    meta = retry_api_call(lambda: spreadsheets.get(spreadsheetId=sheet_id).execute())
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    requests = [{"addSheet": {"properties": {"title": t}}} for t in ALL_TABS if t not in existing]
    if requests:
        retry_api_call(lambda: spreadsheets.batchUpdate(
            spreadsheetId=sheet_id, body={"requests": requests}
        ).execute())
    return sheet_id


def _clear(service, sid: str, tab: str):
    retry_api_call(lambda: service.spreadsheets().values().clear(
        spreadsheetId=sid, range=tab, body={}
    ).execute())


def _write(service, sid: str, tab: str, rows: list[list]):
    retry_api_call(lambda: service.spreadsheets().values().update(
        spreadsheetId=sid, range=f"{tab}!A1",
        valueInputOption="USER_ENTERED", body={"values": rows}
    ).execute())


def _make_txn_key(date_str: str, description: str, amount) -> str:
    amt = str(amount).replace(",", "").replace("$", "")
    try:
        amt_f = float(amt)
    except (ValueError, TypeError):
        amt_f = 0.0
    return f"{date_str}|{description.strip().lower()}|{amt_f:.2f}"


# ── Transactions (merge-based, preserves manual edits) ──

def read_existing_transactions(service, sid: str) -> list[dict]:
    result = retry_api_call(lambda: service.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB_TRANSACTIONS}!A:G"
    ).execute())
    rows = result.get("values", [])
    if len(rows) < 2:
        return []
    headers = rows[0]
    return [{headers[i]: (row[i] if i < len(row) else "") for i in range(len(headers))} for row in rows[1:]]


def write_transactions_tab(service, sid: str, transactions: list[Transaction]) -> None:
    existing = read_existing_transactions(service, sid)
    existing_keys = {}
    for row in existing:
        key = _make_txn_key(row.get("Date", ""), row.get("Description", ""), row.get("Amount", 0))
        existing_keys[key] = row

    merged = list(existing)
    new_count = 0
    for t in transactions:
        key = _make_txn_key(t.date.isoformat(), t.description, t.amount)
        if key not in existing_keys:
            merged.append({
                "Date": t.date.isoformat(), "Description": t.description,
                "Amount": t.amount, "Category": t.category, "Person": t.person,
                "Source File": t.source_file, "Type": t.transaction_type,
            })
            existing_keys[key] = merged[-1]
            new_count += 1

    merged.sort(key=lambda r: r.get("Date", ""), reverse=True)
    _clear(service, sid, TAB_TRANSACTIONS)

    headers = ["Date", "Description", "Amount", "Category", "Person", "Source File", "Type"]
    rows = [headers] + [[r.get(h, "") for h in headers] for r in merged]
    _write(service, sid, TAB_TRANSACTIONS, rows)

    # Category dropdown on column D
    if len(rows) > 1:
        meta = retry_api_call(lambda: service.spreadsheets().get(spreadsheetId=sid).execute())
        tab_id = next((s["properties"]["sheetId"] for s in meta["sheets"]
                       if s["properties"]["title"] == TAB_TRANSACTIONS), None)
        if tab_id is not None:
            retry_api_call(lambda: service.spreadsheets().batchUpdate(
                spreadsheetId=sid, body={"requests": [{"setDataValidation": {
                    "range": {"sheetId": tab_id, "startRowIndex": 1, "endRowIndex": len(rows),
                              "startColumnIndex": 3, "endColumnIndex": 4},
                    "rule": {"condition": {"type": "ONE_OF_LIST",
                             "values": [{"userEnteredValue": c} for c in ALL_CATEGORIES]},
                             "showCustomUi": True, "strict": False}
                }}]}
            ).execute())

    logger.info("Transactions: %d existing, %d new, %d total", len(existing), new_count, len(merged))


# ── Summary tabs (pure data, no formatting) ──

def write_monthly_summary_tab(service, sid: str, metrics: list[MonthlyMetrics]) -> None:
    _clear(service, sid, TAB_MONTHLY_SUMMARY)
    headers = ["Month", "Total Income", "Total Expenses", "Net Savings", "Savings Rate (%)", "Avg Daily Spending"]
    rows = [headers]
    for m in metrics:
        rows.append([m.month, m.total_income, m.total_expenses, m.net_savings,
                     round(m.savings_rate, 2), round(m.avg_daily_spending, 2)])
    _write(service, sid, TAB_MONTHLY_SUMMARY, rows)


def write_annual_summary_tab(service, sid: str, annual_metrics: list[AnnualMetrics]) -> None:
    _clear(service, sid, TAB_ANNUAL_SUMMARY)
    headers = ["Year", "Total Income", "Total Expenses", "Net Savings", "Savings Rate (%)", "Avg Monthly Spending"]
    rows = [headers]
    for m in annual_metrics:
        rows.append([m.year, round(m.total_income, 2), round(m.total_expenses, 2),
                     round(m.net_savings, 2), round(m.savings_rate, 2), round(m.avg_monthly_spending, 2)])
    _write(service, sid, TAB_ANNUAL_SUMMARY, rows)


def write_category_breakdown_tab(service, sid: str, metrics: list[MonthlyMetrics], budgets: dict[str, float]) -> None:
    _clear(service, sid, TAB_CATEGORY_BREAKDOWN)
    combined = [m for m in metrics if m.person == "Combined"]
    all_categories = sorted({cat for m in combined for cat in m.category_spending} - BUDGET_EXCLUDED_CATEGORIES)
    headers = ["Month"] + all_categories
    budget_row = ["Budget"] + [budgets.get(cat, "") for cat in all_categories]
    rows = [headers, budget_row]
    for m in combined:
        rows.append([m.month] + [m.category_spending.get(cat, 0.0) for cat in all_categories])
    _write(service, sid, TAB_CATEGORY_BREAKDOWN, rows)


def write_kpis_tab(service, sid: str, kpis: KPIs, metrics: list[MonthlyMetrics]) -> None:
    _clear(service, sid, TAB_KPIS)
    rows = [
        ["Overall Savings Rate (%)", round(kpis.overall_savings_rate, 2)],
        ["Total Budget", kpis.total_budget],
        ["Total Actual Spending", kpis.total_actual],
        ["Budget Health (%)", round(kpis.budget_health_pct, 2)],
        [],
        ["Top Spending Categories", "Amount"],
    ]
    for cat, amount in kpis.top_categories:
        rows.append([cat, round(amount, 2)])
    rows.append([])
    rows.append(["Month", "Spending Change (%)"])
    for month in sorted(kpis.mom_trends):
        rows.append([month, round(kpis.mom_trends[month], 2)])
    rows.append([])
    if kpis.insights:
        rows.append(["Insights & Recommendations", ""])
        for insight in kpis.insights:
            rows.append([insight, ""])
    _write(service, sid, TAB_KPIS, rows)


def write_budget_status_tab(service, sid: str, metrics: list[MonthlyMetrics], budgets: dict[str, float]) -> None:
    _clear(service, sid, TAB_BUDGET_STATUS)
    combined = [m for m in metrics if m.person == "Combined"]
    num_months = len(combined) if combined else 1
    total_spending = {}
    for m in combined:
        for cat, amt in m.category_spending.items():
            total_spending[cat] = total_spending.get(cat, 0.0) + amt
    all_cats = sorted((set(budgets.keys()) | set(total_spending.keys())) - BUDGET_EXCLUDED_CATEGORIES)
    headers = ["Category", "Total Budget", "Total Actual", "Difference", "Status"]
    rows = [headers]
    for cat in all_cats:
        budget_total = budgets.get(cat, 0.0) * num_months
        actual = total_spending.get(cat, 0.0)
        diff = budget_total - actual
        rows.append([cat, round(budget_total, 2), round(actual, 2), round(diff, 2),
                     "Under Budget" if diff >= 0 else "Over Budget"])
    _write(service, sid, TAB_BUDGET_STATUS, rows)


# ── Manual Entry ──

def initialize_manual_entry_tab(service, sid: str) -> None:
    result = retry_api_call(lambda: service.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB_MANUAL_ENTRY}!A1:A1"
    ).execute())
    if not result.get("values"):
        _write(service, sid, TAB_MANUAL_ENTRY, [
            ["Date", "Description", "Amount", "Category", "Person"],
            ["2025-01-15", "Gross Pay", "5000", "Gross Pay", "Raman"],
            ["2025-01-15", "Federal Tax", "-800", "Taxes", "Raman"],
            ["2025-01-15", "401k", "-500", "Retirement", "Raman"],
            ["2025-01-15", "Health Insurance", "-200", "Benefits", "Raman"],
            ["", "(delete examples and enter your data)", "", "", ""],
        ])

    meta = retry_api_call(lambda: service.spreadsheets().get(spreadsheetId=sid).execute())
    tab_id = next((s["properties"]["sheetId"] for s in meta["sheets"]
                   if s["properties"]["title"] == TAB_MANUAL_ENTRY), None)
    if tab_id is not None:
        retry_api_call(lambda: service.spreadsheets().batchUpdate(
            spreadsheetId=sid, body={"requests": [{"setDataValidation": {
                "range": {"sheetId": tab_id, "startRowIndex": 1,
                          "startColumnIndex": 3, "endColumnIndex": 4},
                "rule": {"condition": {"type": "ONE_OF_LIST",
                         "values": [{"userEnteredValue": c} for c in MANUAL_ENTRY_CATEGORIES]},
                         "showCustomUi": True, "strict": False}
            }}]}
        ).execute())


def read_manual_entries(service, sid: str) -> list[Transaction]:
    from datetime import datetime as _dt
    result = retry_api_call(lambda: service.spreadsheets().values().get(
        spreadsheetId=sid, range=f"{TAB_MANUAL_ENTRY}!A:E"
    ).execute())
    rows = result.get("values", [])
    if len(rows) < 2:
        return []
    txns = []
    category_map = {"Gross Pay": "Income", "Benefits": "Healthcare"}
    for i, row in enumerate(rows[1:], start=2):
        if len(row) < 3:
            continue
        date_str = row[0].strip() if row[0] else ""
        desc = row[1].strip() if len(row) > 1 else ""
        amt_str = row[2].strip() if len(row) > 2 else ""
        cat = row[3].strip() if len(row) > 3 else "Other"
        person = row[4].strip() if len(row) > 4 else "Unknown"
        if not date_str or not desc or "delete" in desc.lower():
            continue
        cat = category_map.get(cat, cat)
        txn_date = None
        for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"]:
            try:
                txn_date = _dt.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue
        if not txn_date:
            continue
        try:
            amount = float(amt_str.replace(",", "").replace("$", ""))
        except ValueError:
            continue
        txns.append(Transaction(
            date=txn_date, description=desc, amount=amount, category=cat,
            person=person, source_file="Manual Entry",
            transaction_type="credit" if amount >= 0 else "debit",
        ))
    logger.info("Read %d manual entries", len(txns))
    return txns


# ── Metadata (last updated timestamp) ──

def write_metadata(service, sid: str) -> None:
    _clear(service, sid, TAB_METADATA)
    now = datetime.now().strftime("%Y-%m-%d %I:%M %p")
    _write(service, sid, TAB_METADATA, [
        ["Key", "Value"],
        ["Last Updated", now],
        ["Updated By", "Finance Tracker CLI"],
    ])
    logger.info("Updated metadata: %s", now)


# ── Main write function ──

def write_to_sheet(
    service, sheet_id: str | None, sheet_name: str,
    transactions: list[Transaction],
    monthly_metrics: list[MonthlyMetrics],
    annual_metrics: list[AnnualMetrics],
    kpis: KPIs, budgets: dict[str, float],
) -> str:
    sid = create_or_get_sheet(service, sheet_id, sheet_name)

    if transactions:
        write_transactions_tab(service, sid, transactions)
    write_monthly_summary_tab(service, sid, monthly_metrics)
    write_annual_summary_tab(service, sid, annual_metrics)
    write_category_breakdown_tab(service, sid, monthly_metrics, budgets)
    write_kpis_tab(service, sid, kpis, monthly_metrics)
    write_budget_status_tab(service, sid, monthly_metrics, budgets)
    initialize_manual_entry_tab(service, sid)
    write_metadata(service, sid)

    logger.info("All data written to spreadsheet %s", sid)
    return sid
