"""Google Sheets writer for the Finance Tracker application.

Writes transaction data, monthly summaries, category breakdowns, KPIs,
and budget status to a Google Sheet with multiple tabs.
"""

import logging

from src.metrics import AnnualMetrics, KPIs, MonthlyMetrics
from src.models import Transaction
from src.retry import retry_api_call

logger = logging.getLogger(__name__)

# Tab names used across the spreadsheet
TAB_TRANSACTIONS = "Transactions"
TAB_MONTHLY_SUMMARY = "Monthly Summary"
TAB_ANNUAL_SUMMARY = "Annual Summary"
TAB_CATEGORY_BREAKDOWN = "Category Breakdown"
TAB_KPIS = "KPIs"
TAB_BUDGET_STATUS = "Budget Status"
TAB_MANUAL_ENTRY = "Manual Entry"

ALL_TABS = [
    TAB_TRANSACTIONS,
    TAB_MONTHLY_SUMMARY,
    TAB_ANNUAL_SUMMARY,
    TAB_CATEGORY_BREAKDOWN,
    TAB_KPIS,
    TAB_BUDGET_STATUS,
    TAB_MANUAL_ENTRY,
]


def create_or_get_sheet(service, sheet_id: str | None, name: str) -> str:
    """Create a new spreadsheet or verify an existing one.

    If sheet_id is None, creates a new spreadsheet with the given name
    and the required tabs. Otherwise, verifies the sheet exists and
    ensures all required tabs are present.

    Returns the spreadsheet ID.
    """
    spreadsheets = service.spreadsheets()

    if sheet_id is None:
        # Create new spreadsheet with all required tabs
        body = {
            "properties": {"title": name},
            "sheets": [{"properties": {"title": tab}} for tab in ALL_TABS],
        }
        result = retry_api_call(lambda: spreadsheets.create(body=body).execute())
        new_id = result["spreadsheetId"]
        logger.info("Created new spreadsheet '%s' with ID: %s", name, new_id)
        return new_id

    # Verify existing spreadsheet and ensure tabs exist
    meta = retry_api_call(lambda: spreadsheets.get(spreadsheetId=sheet_id).execute())
    existing_tabs = {s["properties"]["title"] for s in meta.get("sheets", [])}

    requests = []
    for tab in ALL_TABS:
        if tab not in existing_tabs:
            requests.append({"addSheet": {"properties": {"title": tab}}})

    if requests:
        retry_api_call(
            lambda: spreadsheets.batchUpdate(
                spreadsheetId=sheet_id, body={"requests": requests}
            ).execute()
        )
        logger.info("Added missing tabs to spreadsheet %s", sheet_id)

    return sheet_id


def _clear_sheet(service, spreadsheet_id: str, tab_name: str) -> None:
    """Clear all data from a tab."""
    retry_api_call(
        lambda: service.spreadsheets().values().clear(
            spreadsheetId=spreadsheet_id,
            range=tab_name,
            body={},
        ).execute()
    )


def _get_tab_id(service, spreadsheet_id: str, tab_name: str) -> int:
    """Get the numeric sheet ID for a named tab."""
    meta = retry_api_call(
        lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    )
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == tab_name:
            return sheet["properties"]["sheetId"]
    raise ValueError(f"Tab '{tab_name}' not found in spreadsheet")


def _write_rows(service, spreadsheet_id: str, tab_name: str, rows: list[list]) -> None:
    """Write rows of data to a tab, starting at A1."""
    retry_api_call(
        lambda: service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{tab_name}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": rows},
        ).execute()
    )


def delete_all_charts(service, spreadsheet_id: str) -> None:
    """Delete all embedded charts from the spreadsheet."""
    meta = retry_api_call(
        lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    )
    chart_ids = []
    for sheet in meta.get("sheets", []):
        for chart in sheet.get("charts", []):
            chart_ids.append(chart["chartId"])

    if not chart_ids:
        return

    requests = [{"deleteEmbeddedObject": {"objectId": cid}} for cid in chart_ids]
    retry_api_call(
        lambda: service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests},
        ).execute()
    )
    logger.info("Deleted %d existing charts", len(chart_ids))


def add_chart(service, spreadsheet_id: str, sheet_id: int, chart_spec: dict) -> None:
    """Add an embedded chart to a sheet tab using the Sheets API batchUpdate.

    Args:
        service: Google Sheets API service.
        spreadsheet_id: The spreadsheet ID.
        sheet_id: Numeric ID of the tab to anchor the chart to.
        chart_spec: A dict describing the chart (chartType, domains, series, position, title, etc.).
            Expected keys:
            - title: str
            - chart_type: str (BAR, LINE, PIE, COLUMN)
            - domains: list of source range dicts
            - series: list of source range dicts with optional targetAxis
            - anchor_cell: dict with rowIndex, columnIndex
            - stacked: bool (optional)
            - legend_position: str (optional)
    """
    # Build source ranges for domains and series
    domain_sources = []
    for d in chart_spec.get("domains", []):
        domain_sources.append({"domain": {"sourceRange": {"sources": [d]}}})

    series_list = []
    for s in chart_spec.get("series", []):
        raw_range = s.get("range", s)
        entry = {"series": {"sourceRange": {"sources": [raw_range]}}}
        if "targetAxis" in s:
            entry["targetAxis"] = s["targetAxis"]
        if "color" in s:
            entry["color"] = s["color"]
        series_list.append(entry)

    # Determine chart body based on type
    chart_type = chart_spec.get("chart_type", "COLUMN")
    is_pie = chart_type == "PIE"

    if is_pie:
        basic_chart = None
        pie_chart = {
            "legendPosition": chart_spec.get("legend_position", "RIGHT_LEGEND"),
            "domain": domain_sources[0]["domain"] if domain_sources else {},
            "series": series_list[0]["series"] if series_list else {},
        }
    else:
        basic_chart = {
            "chartType": chart_type,
            "legendPosition": chart_spec.get("legend_position", "BOTTOM_LEGEND"),
            "domains": domain_sources,
            "series": series_list,
            "headerCount": 1,
        }
        # stackedType is only valid for BAR and COLUMN charts
        if chart_type in ("BAR", "COLUMN"):
            basic_chart["stackedType"] = "STACKED" if chart_spec.get("stacked") else "NOT_STACKED"
        pie_chart = None

    spec = {
        "title": chart_spec.get("title", ""),
        "basicChart": basic_chart,
        "pieChart": pie_chart,
    }
    # Remove None chart type
    spec = {k: v for k, v in spec.items() if v is not None}

    anchor = chart_spec.get("anchor_cell", {"rowIndex": 0, "columnIndex": 0})
    request = {
        "addChart": {
            "chart": {
                "spec": spec,
                "position": {
                    "overlayPosition": {
                        "anchorCell": {
                            "sheetId": sheet_id,
                            "rowIndex": anchor.get("rowIndex", 0),
                            "columnIndex": anchor.get("columnIndex", 0),
                        },
                    },
                },
            },
        },
    }

    retry_api_call(
        lambda: service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [request]},
        ).execute()
    )
    logger.info("Added chart '%s' to sheet %d", chart_spec.get("title", ""), sheet_id)


def _source_range(sheet_id: int, start_row: int, end_row: int, start_col: int, end_col: int) -> dict:
    """Build a GridRange dict for chart source ranges."""
    return {
        "sheetId": sheet_id,
        "startRowIndex": start_row,
        "endRowIndex": end_row,
        "startColumnIndex": start_col,
        "endColumnIndex": end_col,
    }


def add_monthly_summary_charts(
    service, spreadsheet_id: str, sheet_id: int, metrics: list[MonthlyMetrics]
) -> None:
    """Add charts to the Monthly Summary tab.

    Charts:
    - Income vs Expenses bar chart (Combined rows)
    - Net savings trend line (Combined rows)
    - Stacked bar per person per month
    """
    combined = [m for m in metrics if m.person == "Combined"]
    if not combined:
        return

    # Data layout: row 0 = header, then rows for each metric entry
    # Find combined row indices (0-based, +1 for header)
    combined_indices = [i + 1 for i, m in enumerate(metrics) if m.person == "Combined"]
    total_rows = len(metrics) + 1  # +1 for header

    # Chart 1: Income vs Expenses bar chart (uses all rows, filtered by Combined)
    # We use the full data range; the chart will show all rows including per-person
    # For simplicity, use the full range — the chart shows Month (col 0), Income (col 2), Expenses (col 3)
    add_chart(service, spreadsheet_id, sheet_id, {
        "title": "Monthly Income vs Expenses",
        "chart_type": "COLUMN",
        "domains": [_source_range(sheet_id, 0, total_rows, 0, 1)],
        "series": [
            {"range": _source_range(sheet_id, 0, total_rows, 2, 3)},
            {"range": _source_range(sheet_id, 0, total_rows, 3, 4)},
        ],
        "anchor_cell": {"rowIndex": total_rows + 1, "columnIndex": 0},
    })

    # Chart 2: Net savings trend line
    add_chart(service, spreadsheet_id, sheet_id, {
        "title": "Net Savings Trend",
        "chart_type": "LINE",
        "domains": [_source_range(sheet_id, 0, total_rows, 0, 1)],
        "series": [
            {"range": _source_range(sheet_id, 0, total_rows, 4, 5)},
        ],
        "anchor_cell": {"rowIndex": total_rows + 1, "columnIndex": 8},
    })


def add_category_breakdown_charts(
    service, spreadsheet_id: str, sheet_id: int, num_categories: int, num_data_rows: int
) -> None:
    """Add charts to the Category Breakdown tab.

    Charts:
    - Pie chart showing proportion of total spending by category
    - Grouped bar chart comparing category spending month-over-month

    Data layout: row 0 = header, row 1 = budget, rows 2+ = monthly data.
    Columns: 0 = Month, 1..N = categories.
    """
    total_rows = 2 + num_data_rows  # header + budget + data rows

    # Pie chart: use first data row as a representative snapshot (or aggregate)
    # We'll use the header row for labels and the last data row for values
    if num_data_rows > 0 and num_categories > 0:
        # Pie chart: header row for labels, last data row for values
        # Each range must be a single row spanning the category columns
        add_chart(service, spreadsheet_id, sheet_id, {
            "title": "Spending by Category",
            "chart_type": "PIE",
            "domains": [_source_range(sheet_id, 0, 1, 1, 1 + num_categories)],
            "series": [
                {"range": _source_range(sheet_id, total_rows - 1, total_rows, 1, 1 + num_categories)},
            ],
            "anchor_cell": {"rowIndex": total_rows + 1, "columnIndex": 0},
        })

        # Bar chart: use full range from header (row 0) through data rows
        # headerCount=1 uses row 0 as series labels
        # Row 1 (budget) will appear as a data point — acceptable tradeoff
        add_chart(service, spreadsheet_id, sheet_id, {
            "title": "Category Spending Month-over-Month",
            "chart_type": "COLUMN",
            "stacked": True,
            "domains": [_source_range(sheet_id, 0, total_rows, 0, 1)],
            "series": [
                {"range": _source_range(sheet_id, 0, total_rows, col, col + 1)}
                for col in range(1, 1 + num_categories)
            ],
            "anchor_cell": {"rowIndex": total_rows + 1, "columnIndex": 8},
        })


def add_kpis_charts(
    service, spreadsheet_id: str, sheet_id: int, kpis: KPIs
) -> None:
    """Add charts to the KPIs tab.

    Charts:
    - Spending trend line (month-over-month % change)
    - Top 5 categories horizontal bar chart

    Data layout (from write_kpis_tab):
    Row 0: Overall Savings Rate
    Row 1: Total Budget
    Row 2: Total Actual Spending
    Row 3: Budget Health
    Row 4: (blank)
    Row 5: "Top Spending Categories" header
    Rows 6..6+N: top categories
    Row 6+N+1: (blank)  -- but could be absent if no categories
    Row 6+N+1 or +2: "Month" header for MoM trends
    """
    num_top = len(kpis.top_categories)
    num_trends = len(kpis.mom_trends)

    # Top categories start at row 5 (header) with data at rows 6..6+num_top-1
    top_start = 5  # header row
    top_data_start = 6
    top_data_end = top_data_start + num_top

    if num_top > 0:
        # Horizontal bar chart for top 5 categories
        add_chart(service, spreadsheet_id, sheet_id, {
            "title": "Top Spending Categories",
            "chart_type": "BAR",
            "domains": [_source_range(sheet_id, top_start, top_data_end, 0, 1)],
            "series": [
                {"range": _source_range(sheet_id, top_start, top_data_end, 1, 2)},
            ],
            "anchor_cell": {"rowIndex": 0, "columnIndex": 3},
        })

    # MoM trends: after top categories + blank row
    trend_header_row = top_data_end + 1  # blank row then header
    trend_data_start = trend_header_row + 1
    trend_data_end = trend_data_start + num_trends

    if num_trends > 0:
        add_chart(service, spreadsheet_id, sheet_id, {
            "title": "Month-over-Month Spending Trend",
            "chart_type": "LINE",
            "domains": [_source_range(sheet_id, trend_header_row, trend_data_end, 0, 1)],
            "series": [
                {"range": _source_range(sheet_id, trend_header_row, trend_data_end, 1, 2)},
            ],
            "anchor_cell": {"rowIndex": 0, "columnIndex": 8},
        })


def add_budget_status_chart(
    service, spreadsheet_id: str, sheet_id: int, num_categories: int
) -> None:
    """Add a budget vs actual bar chart to the Budget Status tab.

    Data layout: row 0 = header, rows 1..N = categories.
    Columns: 0=Category, 1=Total Budget, 2=Total Actual, 3=Difference, 4=Status.
    """
    if num_categories == 0:
        return

    total_rows = num_categories + 1  # +1 for header

    add_chart(service, spreadsheet_id, sheet_id, {
        "title": "Budget vs Actual Spending",
        "chart_type": "BAR",
        "domains": [_source_range(sheet_id, 0, total_rows, 0, 1)],
        "series": [
            {
                "range": _source_range(sheet_id, 0, total_rows, 1, 2),
                "color": {"red": 0.4, "green": 0.8, "blue": 0.4},
            },
            {
                "range": _source_range(sheet_id, 0, total_rows, 2, 3),
                "color": {"red": 0.9, "green": 0.3, "blue": 0.3},
            },
        ],
        "anchor_cell": {"rowIndex": total_rows + 1, "columnIndex": 0},
    })


ALL_CATEGORIES = [
    "Groceries", "Dining", "Utilities", "Transportation", "Entertainment",
    "Healthcare", "Amazon", "Shopping", "Subscriptions", "Housing", "Travel",
    "Income", "Taxes", "Transfer", "Investment", "Retirement", "Insurance",
    "Education", "Childcare", "Fees", "Other",
]


def write_transactions_tab(
    service, spreadsheet_id: str, transactions: list[Transaction]
) -> None:
    """Write the Transactions tab with headers, data, and category dropdown."""
    _clear_sheet(service, spreadsheet_id, TAB_TRANSACTIONS)

    headers = ["Date", "Description", "Amount", "Category", "Person", "Source File", "Type"]
    rows = [headers]
    for t in transactions:
        rows.append([
            t.date.isoformat(),
            t.description,
            t.amount,
            t.category,
            t.person,
            t.source_file,
            t.transaction_type,
        ])

    _write_rows(service, spreadsheet_id, TAB_TRANSACTIONS, rows)

    # Add category dropdown on column D (Category)
    if len(rows) > 1:
        meta = retry_api_call(
            lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        )
        tab_id = None
        for sheet in meta.get("sheets", []):
            if sheet["properties"]["title"] == TAB_TRANSACTIONS:
                tab_id = sheet["properties"]["sheetId"]
                break

        if tab_id is not None:
            request = {
                "setDataValidation": {
                    "range": {
                        "sheetId": tab_id,
                        "startRowIndex": 1,
                        "endRowIndex": len(rows),
                        "startColumnIndex": 3,  # column D
                        "endColumnIndex": 4,
                    },
                    "rule": {
                        "condition": {
                            "type": "ONE_OF_LIST",
                            "values": [{"userEnteredValue": c} for c in ALL_CATEGORIES],
                        },
                        "showCustomUi": True,
                        "strict": False,
                    },
                }
            }
            retry_api_call(
                lambda: service.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id,
                    body={"requests": [request]},
                ).execute()
            )

    logger.info("Wrote %d transactions to '%s' tab", len(transactions), TAB_TRANSACTIONS)


def write_monthly_summary_tab(
    service, spreadsheet_id: str, metrics: list[MonthlyMetrics]
) -> None:
    """Write the Monthly Summary tab with combined family monthly totals."""
    _clear_sheet(service, spreadsheet_id, TAB_MONTHLY_SUMMARY)

    headers = [
        "Month", "Total Income", "Total Expenses",
        "Net Savings", "Savings Rate (%)", "Avg Daily Spending",
    ]
    rows = [headers]
    for m in metrics:
        rows.append([
            m.month,
            m.total_income,
            m.total_expenses,
            m.net_savings,
            round(m.savings_rate, 2),
            round(m.avg_daily_spending, 2),
        ])

    _write_rows(service, spreadsheet_id, TAB_MONTHLY_SUMMARY, rows)
    logger.info("Wrote %d rows to '%s' tab", len(rows) - 1, TAB_MONTHLY_SUMMARY)


def write_annual_summary_tab(
    service, spreadsheet_id: str, annual_metrics: list[AnnualMetrics]
) -> None:
    """Write the Annual Summary tab with combined family yearly totals."""
    _clear_sheet(service, spreadsheet_id, TAB_ANNUAL_SUMMARY)

    headers = [
        "Year", "Total Income", "Total Expenses",
        "Net Savings", "Savings Rate (%)", "Avg Monthly Spending",
    ]
    rows = [headers]
    for m in annual_metrics:
        rows.append([
            m.year,
            round(m.total_income, 2),
            round(m.total_expenses, 2),
            round(m.net_savings, 2),
            round(m.savings_rate, 2),
            round(m.avg_monthly_spending, 2),
        ])

    _write_rows(service, spreadsheet_id, TAB_ANNUAL_SUMMARY, rows)
    logger.info("Wrote %d rows to '%s' tab", len(rows) - 1, TAB_ANNUAL_SUMMARY)


def write_category_breakdown_tab(
    service,
    spreadsheet_id: str,
    metrics: list[MonthlyMetrics],
    budgets: dict[str, float],
) -> None:
    """Write the Category Breakdown tab with per-category per-month spending and budget comparison."""
    _clear_sheet(service, spreadsheet_id, TAB_CATEGORY_BREAKDOWN)

    # Collect all categories across all months (combined only), excluding non-spending categories
    combined = [m for m in metrics if m.person == "Combined"]
    all_categories = sorted(
        {cat for m in combined for cat in m.category_spending}
        - BUDGET_EXCLUDED_CATEGORIES
    )

    headers = ["Month"] + all_categories
    budget_row = ["Budget"] + [budgets.get(cat, "") for cat in all_categories]

    rows = [headers, budget_row]
    for m in combined:
        row = [m.month] + [m.category_spending.get(cat, 0.0) for cat in all_categories]
        rows.append(row)

    _write_rows(service, spreadsheet_id, TAB_CATEGORY_BREAKDOWN, rows)
    logger.info("Wrote category breakdown for %d months", len(combined))


def write_kpis_tab(
    service, spreadsheet_id: str, kpis: KPIs, metrics: list[MonthlyMetrics]
) -> None:
    """Write the KPIs tab with key indicators."""
    _clear_sheet(service, spreadsheet_id, TAB_KPIS)

    rows: list[list] = []

    # Overall KPIs section
    rows.append(["Overall Savings Rate (%)", round(kpis.overall_savings_rate, 2)])
    rows.append(["Total Budget", kpis.total_budget])
    rows.append(["Total Actual Spending", kpis.total_actual])
    rows.append(["Budget Health (%)", round(kpis.budget_health_pct, 2)])
    rows.append([])  # blank separator

    # Top 5 spending categories
    rows.append(["Top Spending Categories", "Amount"])
    for cat, amount in kpis.top_categories:
        rows.append([cat, round(amount, 2)])
    rows.append([])

    # Month-over-month trends
    rows.append(["Month", "Spending Change (%)"])
    for month in sorted(kpis.mom_trends):
        rows.append([month, round(kpis.mom_trends[month], 2)])
    rows.append([])

    # Insights
    if kpis.insights:
        rows.append(["📊 Insights & Recommendations", ""])
        for insight in kpis.insights:
            rows.append([insight, ""])

    _write_rows(service, spreadsheet_id, TAB_KPIS, rows)
    logger.info("Wrote KPIs to '%s' tab", TAB_KPIS)


# Categories excluded from budget tracking (income/payroll deductions, not discretionary spending)
BUDGET_EXCLUDED_CATEGORIES = {"Income", "Taxes", "Retirement", "Investment", "Transfer"}


def write_budget_status_tab(
    service,
    spreadsheet_id: str,
    metrics: list[MonthlyMetrics],
    budgets: dict[str, float],
) -> None:
    """Write the Budget Status tab with budget vs actual per category.

    Excludes non-spending categories like Income, Taxes, Retirement, etc.
    """
    _clear_sheet(service, spreadsheet_id, TAB_BUDGET_STATUS)

    # Aggregate spending across all combined months
    combined = [m for m in metrics if m.person == "Combined"]
    num_months = len(combined) if combined else 1

    total_spending: dict[str, float] = {}
    for m in combined:
        for cat, amt in m.category_spending.items():
            total_spending[cat] = total_spending.get(cat, 0.0) + amt

    all_categories = sorted(set(list(budgets.keys()) + list(total_spending.keys())))
    # Filter out non-spending categories
    all_categories = [c for c in all_categories if c not in BUDGET_EXCLUDED_CATEGORIES]

    headers = ["Category", "Total Budget", "Total Actual", "Difference", "Status"]
    rows = [headers]
    for cat in all_categories:
        budget_total = budgets.get(cat, 0.0) * num_months
        actual = total_spending.get(cat, 0.0)
        diff = budget_total - actual
        status = "Under Budget" if diff >= 0 else "Over Budget"
        rows.append([cat, round(budget_total, 2), round(actual, 2), round(diff, 2), status])

    _write_rows(service, spreadsheet_id, TAB_BUDGET_STATUS, rows)
    logger.info("Wrote budget status for %d categories", len(all_categories))


def _bold_header_request(sheet_id: int) -> dict:
    """Return a repeatCell request that bolds the first row."""
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 0,
                "endRowIndex": 1,
            },
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"bold": True},
                },
            },
            "fields": "userEnteredFormat.textFormat.bold",
        },
    }


def _number_format_request(sheet_id: int, start_col: int, end_col: int, pattern: str) -> dict:
    """Return a repeatCell request that applies a number format to a column range."""
    return {
        "repeatCell": {
            "range": {
                "sheetId": sheet_id,
                "startRowIndex": 1,
                "startColumnIndex": start_col,
                "endColumnIndex": end_col,
            },
            "cell": {
                "userEnteredFormat": {
                    "numberFormat": {"type": "NUMBER", "pattern": pattern},
                },
            },
            "fields": "userEnteredFormat.numberFormat",
        },
    }


def _column_width_request(sheet_id: int, start_col: int, end_col: int, width: int) -> dict:
    """Return an updateDimensionProperties request for column width."""
    return {
        "updateDimensionProperties": {
            "range": {
                "sheetId": sheet_id,
                "dimension": "COLUMNS",
                "startIndex": start_col,
                "endIndex": end_col,
            },
            "properties": {"pixelSize": width},
            "fields": "pixelSize",
        },
    }


def _over_budget_conditional_format(sheet_id: int, row_count: int) -> dict:
    """Return a conditional format rule: red background when Difference < 0 (column D)."""
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [
                    {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": row_count,
                        "startColumnIndex": 3,
                        "endColumnIndex": 4,
                    }
                ],
                "booleanRule": {
                    "condition": {
                        "type": "NUMBER_LESS",
                        "values": [{"userEnteredValue": "0"}],
                    },
                    "format": {
                        "backgroundColor": {"red": 1.0, "green": 0.8, "blue": 0.8},
                    },
                },
            },
            "index": 0,
        },
    }


def _under_budget_conditional_format(sheet_id: int, row_count: int) -> dict:
    """Return a conditional format rule: green background when Difference >= 0 (column D)."""
    return {
        "addConditionalFormatRule": {
            "rule": {
                "ranges": [
                    {
                        "sheetId": sheet_id,
                        "startRowIndex": 1,
                        "endRowIndex": row_count,
                        "startColumnIndex": 3,
                        "endColumnIndex": 4,
                    }
                ],
                "booleanRule": {
                    "condition": {
                        "type": "NUMBER_GREATER_THAN_EQ",
                        "values": [{"userEnteredValue": "0"}],
                    },
                    "format": {
                        "backgroundColor": {"red": 0.8, "green": 1.0, "blue": 0.8},
                    },
                },
            },
            "index": 1,
        },
    }


def apply_formatting(
    service,
    spreadsheet_id: str,
    budget_status_row_count: int = 0,
) -> None:
    """Apply formatting to all tabs: bold headers, number formats, column widths, conditional colors.

    Args:
        service: Google Sheets API service.
        spreadsheet_id: The spreadsheet to format.
        budget_status_row_count: Total rows (including header) in the Budget Status tab,
            used for conditional formatting range.
    """
    meta = retry_api_call(
        lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    )
    tab_ids = {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in meta.get("sheets", [])
    }

    requests: list[dict] = []

    # Bold headers on every tab
    for tab_name in ALL_TABS:
        if tab_name in tab_ids:
            requests.append(_bold_header_request(tab_ids[tab_name]))

    # Transactions tab: widen Description col, number format on Amount
    if TAB_TRANSACTIONS in tab_ids:
        tid = tab_ids[TAB_TRANSACTIONS]
        requests.append(_column_width_request(tid, 1, 2, 300))  # Description
        requests.append(_number_format_request(tid, 2, 3, "#,##0.00"))  # Amount

    # Monthly Summary tab: number formats for monetary columns (C-G)
    if TAB_MONTHLY_SUMMARY in tab_ids:
        mid = tab_ids[TAB_MONTHLY_SUMMARY]
        requests.append(_number_format_request(mid, 2, 7, "#,##0.00"))

    # Budget Status tab: number formats and conditional formatting
    if TAB_BUDGET_STATUS in tab_ids:
        bid = tab_ids[TAB_BUDGET_STATUS]
        requests.append(_number_format_request(bid, 1, 4, "#,##0.00"))
        requests.append(_column_width_request(bid, 0, 1, 180))  # Category
        requests.append(_column_width_request(bid, 4, 5, 120))  # Status
        if budget_status_row_count > 1:
            requests.append(_over_budget_conditional_format(bid, budget_status_row_count))
            requests.append(_under_budget_conditional_format(bid, budget_status_row_count))

    if requests:
        retry_api_call(
            lambda: service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": requests},
            ).execute()
        )
        logger.info("Applied formatting to spreadsheet %s", spreadsheet_id)


MANUAL_ENTRY_CATEGORIES = [
    "Gross Pay", "Taxes", "Retirement", "Benefits",
]


def initialize_manual_entry_tab(service, spreadsheet_id: str) -> None:
    """Set up the Manual Entry tab with headers and category dropdown.

    Does NOT clear existing data — this tab is user-managed.
    Headers: Date, Description, Amount, Category, Person
    Always applies the category dropdown validation.
    """
    range_str = f"{TAB_MANUAL_ENTRY}!A1:A1"
    result = retry_api_call(
        lambda: service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_str,
        ).execute()
    )

    # Only write headers if the tab is empty
    if not result.get("values"):
        headers = [["Date", "Description", "Amount", "Category", "Person"]]
        example = [
            ["2025-01-15", "Gross Pay", "5000", "Gross Pay", "Raman"],
            ["2025-01-15", "Federal Tax", "-800", "Taxes", "Raman"],
            ["2025-01-15", "State Tax", "-300", "Taxes", "Raman"],
            ["2025-01-15", "401k", "-500", "Retirement", "Raman"],
            ["2025-01-15", "Health Insurance", "-200", "Benefits", "Raman"],
        ]
        hint = [["", "(delete example rows above and enter your data)", "", "", ""]]
        _write_rows(service, spreadsheet_id, TAB_MANUAL_ENTRY, headers + example + hint)
        logger.info("Initialized Manual Entry tab with headers and examples")

    # Apply category dropdown validation on column D (index 3)
    meta = retry_api_call(
        lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    )
    tab_id = None
    for sheet in meta.get("sheets", []):
        if sheet["properties"]["title"] == TAB_MANUAL_ENTRY:
            tab_id = sheet["properties"]["sheetId"]
            break

    if tab_id is not None:
        request = {
            "setDataValidation": {
                "range": {
                    "sheetId": tab_id,
                    "startRowIndex": 1,  # skip header
                    "startColumnIndex": 3,  # column D (Category)
                    "endColumnIndex": 4,
                },
                "rule": {
                    "condition": {
                        "type": "ONE_OF_LIST",
                        "values": [{"userEnteredValue": c} for c in MANUAL_ENTRY_CATEGORIES],
                    },
                    "showCustomUi": True,
                    "strict": False,  # allow custom values too
                },
            }
        }
        retry_api_call(
            lambda: service.spreadsheets().batchUpdate(
                spreadsheetId=spreadsheet_id,
                body={"requests": [request]},
            ).execute()
        )
        logger.info("Applied category dropdown to Manual Entry tab")


def read_manual_entries(service, spreadsheet_id: str) -> list[Transaction]:
    """Read transactions from the Manual Entry tab.

    Skips rows with missing date or description. Returns Transaction objects.
    """
    from datetime import datetime

    range_str = f"{TAB_MANUAL_ENTRY}!A:E"
    result = retry_api_call(
        lambda: service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=range_str,
        ).execute()
    )

    rows = result.get("values", [])
    if len(rows) < 2:
        return []

    # Skip header row
    transactions = []
    for i, row in enumerate(rows[1:], start=2):
        if len(row) < 3:
            continue

        date_str = row[0].strip() if row[0] else ""
        description = row[1].strip() if len(row) > 1 else ""
        amount_str = row[2].strip() if len(row) > 2 else ""
        category = row[3].strip() if len(row) > 3 else "Other"
        # Map user-friendly dropdown labels to internal categories
        category_map = {"Gross Pay": "Income", "Benefits": "Healthcare"}
        category = category_map.get(category, category)
        person = row[4].strip() if len(row) > 4 else "Unknown"

        if not date_str or not description:
            continue

        # Skip hint/example rows
        if "delete this" in description.lower():
            continue

        # Parse date
        txn_date = None
        for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y"]:
            try:
                txn_date = datetime.strptime(date_str, fmt).date()
                break
            except ValueError:
                continue

        if txn_date is None:
            logger.warning("Manual Entry row %d: invalid date '%s', skipping", i, date_str)
            continue

        try:
            amount = float(amount_str.replace(",", "").replace("$", ""))
        except ValueError:
            logger.warning("Manual Entry row %d: invalid amount '%s', skipping", i, amount_str)
            continue

        txn_type = "credit" if amount >= 0 else "debit"

        transactions.append(Transaction(
            date=txn_date,
            description=description,
            amount=amount,
            category=category,
            person=person,
            source_file="Manual Entry",
            transaction_type=txn_type,
        ))

    logger.info("Read %d transactions from Manual Entry tab", len(transactions))
    return transactions


def write_to_sheet(
    service,
    sheet_id: str | None,
    sheet_name: str,
    transactions: list[Transaction],
    monthly_metrics: list[MonthlyMetrics],
    annual_metrics: list[AnnualMetrics],
    kpis: KPIs,
    budgets: dict[str, float],
) -> str:
    """Write all data to Google Sheet.

    Creates the sheet if sheet_id is None. Clears existing data and
    rewrites all tabs.

    Returns the spreadsheet ID.
    """
    spreadsheet_id = create_or_get_sheet(service, sheet_id, sheet_name)

    write_transactions_tab(service, spreadsheet_id, transactions)
    write_monthly_summary_tab(service, spreadsheet_id, monthly_metrics)
    write_annual_summary_tab(service, spreadsheet_id, annual_metrics)
    write_category_breakdown_tab(service, spreadsheet_id, monthly_metrics, budgets)
    write_kpis_tab(service, spreadsheet_id, kpis, monthly_metrics)
    write_budget_status_tab(service, spreadsheet_id, monthly_metrics, budgets)

    # Initialize Manual Entry tab (doesn't clear existing user data)
    initialize_manual_entry_tab(service, spreadsheet_id)

    # Compute budget status row count for conditional formatting
    combined = [m for m in monthly_metrics if m.person == "Combined"]
    num_months = len(combined) if combined else 1
    total_spending_cats: set[str] = set()
    for m in combined:
        total_spending_cats.update(m.category_spending.keys())
    all_cats = set(budgets.keys()) | total_spending_cats
    budget_row_count = len(all_cats) + 1  # +1 for header

    apply_formatting(service, spreadsheet_id, budget_row_count)

    # Delete existing charts and recreate them
    delete_all_charts(service, spreadsheet_id)

    # Add embedded charts to each tab
    meta = retry_api_call(
        lambda: service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    )
    tab_ids = {
        s["properties"]["title"]: s["properties"]["sheetId"]
        for s in meta.get("sheets", [])
    }

    if TAB_MONTHLY_SUMMARY in tab_ids:
        add_monthly_summary_charts(
            service, spreadsheet_id, tab_ids[TAB_MONTHLY_SUMMARY], monthly_metrics
        )

    combined = [m for m in monthly_metrics if m.person == "Combined"]
    all_categories_list = sorted(
        {cat for m in combined for cat in m.category_spending}
    )
    if TAB_CATEGORY_BREAKDOWN in tab_ids:
        add_category_breakdown_charts(
            service, spreadsheet_id, tab_ids[TAB_CATEGORY_BREAKDOWN],
            num_categories=len(all_categories_list),
            num_data_rows=len(combined),
        )

    if TAB_KPIS in tab_ids:
        add_kpis_charts(service, spreadsheet_id, tab_ids[TAB_KPIS], kpis)

    if TAB_BUDGET_STATUS in tab_ids:
        add_budget_status_chart(
            service, spreadsheet_id, tab_ids[TAB_BUDGET_STATUS],
            num_categories=len(all_cats),
        )

    logger.info("Finished writing all tabs to spreadsheet %s", spreadsheet_id)
    return spreadsheet_id
