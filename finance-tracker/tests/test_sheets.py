"""Unit tests for the Google Sheets writer module."""

from datetime import date
from unittest.mock import MagicMock, call, patch

import pytest

from src.metrics import KPIs, MonthlyMetrics
from src.models import Transaction
from src.sheets import (
    ALL_TABS,
    TAB_BUDGET_STATUS,
    TAB_CATEGORY_BREAKDOWN,
    TAB_KPIS,
    TAB_MONTHLY_SUMMARY,
    TAB_TRANSACTIONS,
    create_or_get_sheet,
    write_budget_status_tab,
    write_category_breakdown_tab,
    write_kpis_tab,
    write_monthly_summary_tab,
    write_to_sheet,
    write_transactions_tab,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service():
    """Build a mock Sheets API service with chained method support."""
    service = MagicMock()
    spreadsheets = MagicMock()
    service.spreadsheets.return_value = spreadsheets

    values = MagicMock()
    spreadsheets.values.return_value = values
    values.clear.return_value.execute.return_value = {}
    values.update.return_value.execute.return_value = {}

    return service, spreadsheets, values


def _sample_transactions():
    return [
        Transaction(
            date=date(2024, 1, 15),
            description="Walmart Grocery",
            amount=-50.0,
            category="Groceries",
            person="John",
            source_file="jan_stmt.csv",
            transaction_type="debit",
        ),
        Transaction(
            date=date(2024, 1, 20),
            description="Salary Deposit",
            amount=3000.0,
            category="Income",
            person="John",
            source_file="jan_stmt.csv",
            transaction_type="credit",
        ),
    ]


def _sample_metrics():
    return [
        MonthlyMetrics(
            month="2024-01",
            person="Combined",
            total_income=3000.0,
            total_expenses=-50.0,
            net_savings=2950.0,
            savings_rate=98.33,
            avg_daily_spending=1.61,
            category_spending={"Groceries": 50.0},
            category_budget_utilization={"Groceries": 6.25},
        ),
    ]


def _sample_kpis():
    return KPIs(
        top_categories=[("Groceries", 50.0)],
        mom_trends={},
        overall_savings_rate=98.33,
        total_budget=800.0,
        total_actual=50.0,
        budget_health_pct=6.25,
    )


SAMPLE_BUDGETS = {"Groceries": 800.0, "Dining": 400.0}


# ---------------------------------------------------------------------------
# create_or_get_sheet
# ---------------------------------------------------------------------------

class TestCreateOrGetSheet:
    def test_creates_new_sheet_when_id_is_none(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.create.return_value.execute.return_value = {
            "spreadsheetId": "new-id-123"
        }

        result = create_or_get_sheet(service, None, "My Finance Sheet")

        assert result == "new-id-123"
        create_call = spreadsheets.create.call_args
        body = create_call[1]["body"] if "body" in create_call[1] else create_call[0][0]
        assert body["properties"]["title"] == "My Finance Sheet"
        tab_titles = [s["properties"]["title"] for s in body["sheets"]]
        assert tab_titles == ALL_TABS

    def test_returns_existing_id_when_all_tabs_present(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.get.return_value.execute.return_value = {
            "sheets": [{"properties": {"title": t}} for t in ALL_TABS]
        }

        result = create_or_get_sheet(service, "existing-id", "Sheet")

        assert result == "existing-id"
        spreadsheets.create.assert_not_called()
        spreadsheets.batchUpdate.assert_not_called()

    def test_adds_missing_tabs_to_existing_sheet(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.get.return_value.execute.return_value = {
            "sheets": [{"properties": {"title": TAB_TRANSACTIONS}}]
        }
        spreadsheets.batchUpdate.return_value.execute.return_value = {}

        result = create_or_get_sheet(service, "existing-id", "Sheet")

        assert result == "existing-id"
        batch_call = spreadsheets.batchUpdate.call_args
        requests = batch_call[1]["body"]["requests"]
        added_tabs = {r["addSheet"]["properties"]["title"] for r in requests}
        expected_missing = set(ALL_TABS) - {TAB_TRANSACTIONS}
        assert added_tabs == expected_missing


# ---------------------------------------------------------------------------
# write_transactions_tab
# ---------------------------------------------------------------------------

class TestWriteTransactionsTab:
    def test_writes_headers_and_data(self):
        service, spreadsheets, values = _make_service()
        # Mock reading existing transactions (empty sheet)
        values.get.return_value.execute.return_value = {"values": []}
        txns = _sample_transactions()

        write_transactions_tab(service, "sheet-1", txns)

        # Verify clear was called
        values.clear.assert_called()

        # Verify update was called with correct data
        update_args = values.update.call_args
        written_rows = update_args[1]["body"]["values"]
        assert written_rows[0] == ["Date", "Description", "Amount", "Category", "Person", "Source File", "Type", "Trip"]
        assert len(written_rows) == 3  # header + 2 transactions

    def test_writes_empty_transactions(self):
        service, _, values = _make_service()
        values.get.return_value.execute.return_value = {"values": []}

        write_transactions_tab(service, "sheet-1", [])

        update_args = values.update.call_args
        written_rows = update_args[1]["body"]["values"]
        assert len(written_rows) == 1  # header only

    def test_deduplicates_existing_transactions(self):
        service, _, values = _make_service()
        # Existing transaction in sheet matches one of the new ones
        values.get.return_value.execute.return_value = {"values": [
            ["Date", "Description", "Amount", "Category", "Person", "Source File", "Type"],
            ["2024-01-15", "Walmart Grocery", "-50.0", "Shopping", "John", "jan_stmt.csv", "debit"],
        ]}
        txns = _sample_transactions()  # includes "Walmart Grocery" with same date/amount

        write_transactions_tab(service, "sheet-1", txns)

        update_args = values.update.call_args
        written_rows = update_args[1]["body"]["values"]
        # Should have header + 2 unique (existing Walmart preserved with "Shopping" category, + Salary)
        assert len(written_rows) == 3
        # The existing row should keep its manually-edited category "Shopping"
        walmart_rows = [r for r in written_rows[1:] if "Walmart" in str(r[1])]
        assert len(walmart_rows) == 1
        assert walmart_rows[0][3] == "Shopping"  # preserved manual edit


# ---------------------------------------------------------------------------
# write_monthly_summary_tab
# ---------------------------------------------------------------------------

class TestWriteMonthlySummaryTab:
    def test_writes_metrics_rows(self):
        service, _, values = _make_service()
        metrics = _sample_metrics()

        write_monthly_summary_tab(service, "sheet-1", metrics)

        update_args = values.update.call_args
        rows = update_args[1]["body"]["values"]
        assert rows[0] == [
            "Month", "Total Income", "Total Expenses",
            "Net Savings", "Savings Rate (%)", "Avg Daily Spending",
        ]
        assert len(rows) == 2  # header + 1 combined row
        assert rows[1][0] == "2024-01"


# ---------------------------------------------------------------------------
# write_category_breakdown_tab
# ---------------------------------------------------------------------------

class TestWriteCategoryBreakdownTab:
    def test_writes_category_columns_with_budget_row(self):
        service, _, values = _make_service()
        metrics = _sample_metrics()

        write_category_breakdown_tab(service, "sheet-1", metrics, SAMPLE_BUDGETS)

        update_args = values.update.call_args
        rows = update_args[1]["body"]["values"]
        # Header row should have Month + category names
        assert rows[0][0] == "Month"
        assert "Groceries" in rows[0]
        # Budget row
        assert rows[1][0] == "Budget"
        groceries_idx = rows[0].index("Groceries")
        assert rows[1][groceries_idx] == 800.0
        # Data row for 2024-01 Combined
        assert rows[2][0] == "2024-01"
        assert rows[2][groceries_idx] == 50.0

    def test_only_uses_combined_metrics(self):
        service, _, values = _make_service()
        # Include per-person metrics that should be excluded
        metrics = _sample_metrics()

        write_category_breakdown_tab(service, "sheet-1", metrics, {})

        update_args = values.update.call_args
        rows = update_args[1]["body"]["values"]
        # header + budget + 1 combined month = 3 rows
        assert len(rows) == 3


# ---------------------------------------------------------------------------
# write_kpis_tab
# ---------------------------------------------------------------------------

class TestWriteKpisTab:
    def test_writes_kpi_data(self):
        service, _, values = _make_service()
        kpis = _sample_kpis()
        metrics = _sample_metrics()

        write_kpis_tab(service, "sheet-1", kpis, metrics)

        update_args = values.update.call_args
        rows = update_args[1]["body"]["values"]
        # First row: overall savings rate
        assert rows[0][0] == "Overall Savings Rate (%)"
        assert rows[0][1] == 98.33
        # Top categories header
        top_cat_header_idx = next(
            i for i, r in enumerate(rows) if r and r[0] == "Top Spending Categories"
        )
        assert rows[top_cat_header_idx + 1][0] == "Groceries"
        assert rows[top_cat_header_idx + 1][1] == 50.0

    def test_writes_mom_trends(self):
        service, _, values = _make_service()
        kpis = KPIs(
            top_categories=[],
            mom_trends={"2024-02": 10.5, "2024-03": -5.2},
            overall_savings_rate=50.0,
            total_budget=1000.0,
            total_actual=500.0,
            budget_health_pct=50.0,
        )

        write_kpis_tab(service, "sheet-1", kpis, [])

        update_args = values.update.call_args
        rows = update_args[1]["body"]["values"]
        # Find the MoM trends section
        trend_header_idx = next(
            i for i, r in enumerate(rows) if r and r[0] == "Month"
        )
        assert rows[trend_header_idx + 1] == ["2024-02", 10.5]
        assert rows[trend_header_idx + 2] == ["2024-03", -5.2]


# ---------------------------------------------------------------------------
# write_budget_status_tab
# ---------------------------------------------------------------------------

class TestWriteBudgetStatusTab:
    def test_writes_budget_vs_actual(self):
        service, _, values = _make_service()
        metrics = _sample_metrics()

        write_budget_status_tab(service, "sheet-1", metrics, SAMPLE_BUDGETS)

        update_args = values.update.call_args
        rows = update_args[1]["body"]["values"]
        assert rows[0] == ["Category", "Total Budget", "Total Actual", "Difference", "Status"]

        # Find Groceries row
        groceries_row = next(r for r in rows[1:] if r[0] == "Groceries")
        assert groceries_row[1] == 800.0   # budget * 1 month
        assert groceries_row[2] == 50.0    # actual
        assert groceries_row[3] == 750.0   # difference
        assert groceries_row[4] == "Under Budget"

    def test_over_budget_status(self):
        service, _, values = _make_service()
        metrics = [
            MonthlyMetrics(
                month="2024-01",
                person="Combined",
                total_income=1000.0,
                total_expenses=-500.0,
                net_savings=500.0,
                savings_rate=50.0,
                avg_daily_spending=16.13,
                category_spending={"Dining": 500.0},
                category_budget_utilization={"Dining": 125.0},
            ),
        ]

        write_budget_status_tab(service, "sheet-1", metrics, {"Dining": 400.0})

        update_args = values.update.call_args
        rows = update_args[1]["body"]["values"]
        dining_row = next(r for r in rows[1:] if r[0] == "Dining")
        assert dining_row[4] == "Over Budget"
        assert dining_row[3] < 0  # negative difference

    def test_category_with_no_budget(self):
        service, _, values = _make_service()
        metrics = [
            MonthlyMetrics(
                month="2024-01",
                person="Combined",
                category_spending={"Misc": 100.0},
            ),
        ]

        write_budget_status_tab(service, "sheet-1", metrics, {})

        update_args = values.update.call_args
        rows = update_args[1]["body"]["values"]
        misc_row = next(r for r in rows[1:] if r[0] == "Misc")
        assert misc_row[1] == 0.0  # no budget
        assert misc_row[4] == "Over Budget"  # 0 - 100 < 0


# ---------------------------------------------------------------------------
# write_to_sheet (integration of all tabs)
# ---------------------------------------------------------------------------

class TestWriteToSheet:
    def test_orchestrates_all_tab_writes(self):
        service, spreadsheets, values = _make_service()
        spreadsheets.create.return_value.execute.return_value = {
            "spreadsheetId": "new-sheet-id"
        }
        # apply_formatting needs tab IDs from get()
        spreadsheets.get.return_value.execute.return_value = {
            "sheets": [
                {"properties": {"title": t, "sheetId": i}}
                for i, t in enumerate(ALL_TABS)
            ]
        }
        spreadsheets.batchUpdate.return_value.execute.return_value = {}

        txns = _sample_transactions()
        metrics = _sample_metrics()
        kpis = _sample_kpis()

        result = write_to_sheet(
            service, None, "Test Sheet", txns, metrics, [], kpis, SAMPLE_BUDGETS
        )

        assert result == "new-sheet-id"
        # Should have cleared 7 data tabs (Manual Entry is not cleared, Metadata is cleared)
        assert values.clear.call_count == 7

    def test_uses_existing_sheet_id(self):
        service, spreadsheets, values = _make_service()
        spreadsheets.get.return_value.execute.return_value = {
            "sheets": [
                {"properties": {"title": t, "sheetId": i}}
                for i, t in enumerate(ALL_TABS)
            ]
        }
        spreadsheets.batchUpdate.return_value.execute.return_value = {}

        result = write_to_sheet(
            service, "existing-id", "Sheet", [], [], [], KPIs(), {}
        )

        assert result == "existing-id"
        spreadsheets.create.assert_not_called()
