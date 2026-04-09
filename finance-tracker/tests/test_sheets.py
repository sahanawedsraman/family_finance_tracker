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
        txns = _sample_transactions()

        write_transactions_tab(service, "sheet-1", txns)

        # Verify clear was called
        values.clear.assert_called_once()
        clear_args = values.clear.call_args
        assert clear_args[1]["range"] == TAB_TRANSACTIONS

        # Verify update was called with correct data
        update_args = values.update.call_args
        written_rows = update_args[1]["body"]["values"]
        assert written_rows[0] == ["Date", "Description", "Amount", "Category", "Person", "Source File", "Type"]
        assert len(written_rows) == 3  # header + 2 transactions
        assert written_rows[1][0] == "2024-01-15"
        assert written_rows[1][2] == -50.0
        assert written_rows[2][2] == 3000.0

    def test_writes_empty_transactions(self):
        service, _, values = _make_service()

        write_transactions_tab(service, "sheet-1", [])

        update_args = values.update.call_args
        written_rows = update_args[1]["body"]["values"]
        assert len(written_rows) == 1  # header only


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
        # Should have cleared 6 data tabs (Manual Entry is not cleared)
        assert values.clear.call_count == 6

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


# ---------------------------------------------------------------------------
# apply_formatting
# ---------------------------------------------------------------------------

from src.sheets import apply_formatting


def _make_service_with_tab_ids(tab_ids: dict[str, int]):
    """Build a mock service that returns specific tab IDs from get()."""
    service, spreadsheets, values = _make_service()
    spreadsheets.get.return_value.execute.return_value = {
        "sheets": [
            {"properties": {"title": name, "sheetId": sid}}
            for name, sid in tab_ids.items()
        ]
    }
    spreadsheets.batchUpdate.return_value.execute.return_value = {}
    return service, spreadsheets


class TestApplyFormatting:
    def test_bolds_headers_on_all_tabs(self):
        tab_ids = {t: i for i, t in enumerate(ALL_TABS)}
        service, spreadsheets = _make_service_with_tab_ids(tab_ids)

        apply_formatting(service, "sheet-1", budget_status_row_count=0)

        batch_call = spreadsheets.batchUpdate.call_args
        requests = batch_call[1]["body"]["requests"]
        bold_requests = [
            r for r in requests if "repeatCell" in r
            and r["repeatCell"].get("fields") == "userEnteredFormat.textFormat.bold"
        ]
        assert len(bold_requests) == len(ALL_TABS)

    def test_applies_number_format_to_transactions(self):
        tab_ids = {TAB_TRANSACTIONS: 0}
        service, spreadsheets = _make_service_with_tab_ids(tab_ids)

        apply_formatting(service, "sheet-1")

        batch_call = spreadsheets.batchUpdate.call_args
        requests = batch_call[1]["body"]["requests"]
        num_fmt_requests = [
            r for r in requests if "repeatCell" in r
            and r["repeatCell"].get("fields") == "userEnteredFormat.numberFormat"
        ]
        # Amount column format
        assert len(num_fmt_requests) >= 1
        amt_req = num_fmt_requests[0]["repeatCell"]
        assert amt_req["range"]["startColumnIndex"] == 2
        assert amt_req["range"]["endColumnIndex"] == 3

    def test_applies_conditional_formatting_to_budget_status(self):
        tab_ids = {TAB_BUDGET_STATUS: 4}
        service, spreadsheets = _make_service_with_tab_ids(tab_ids)

        apply_formatting(service, "sheet-1", budget_status_row_count=5)

        batch_call = spreadsheets.batchUpdate.call_args
        requests = batch_call[1]["body"]["requests"]
        cond_requests = [r for r in requests if "addConditionalFormatRule" in r]
        assert len(cond_requests) == 2

        # Over-budget rule: red background
        over_rule = cond_requests[0]["addConditionalFormatRule"]["rule"]
        assert over_rule["booleanRule"]["condition"]["type"] == "NUMBER_LESS"
        bg = over_rule["booleanRule"]["format"]["backgroundColor"]
        assert bg["red"] == 1.0
        assert bg["green"] == 0.8

        # Under-budget rule: green background
        under_rule = cond_requests[1]["addConditionalFormatRule"]["rule"]
        assert under_rule["booleanRule"]["condition"]["type"] == "NUMBER_GREATER_THAN_EQ"
        bg = under_rule["booleanRule"]["format"]["backgroundColor"]
        assert bg["green"] == 1.0
        assert bg["red"] == 0.8

    def test_skips_conditional_formatting_when_no_budget_rows(self):
        tab_ids = {TAB_BUDGET_STATUS: 4}
        service, spreadsheets = _make_service_with_tab_ids(tab_ids)

        apply_formatting(service, "sheet-1", budget_status_row_count=0)

        batch_call = spreadsheets.batchUpdate.call_args
        requests = batch_call[1]["body"]["requests"]
        cond_requests = [r for r in requests if "addConditionalFormatRule" in r]
        assert len(cond_requests) == 0

    def test_column_width_requests(self):
        tab_ids = {TAB_TRANSACTIONS: 0, TAB_BUDGET_STATUS: 4}
        service, spreadsheets = _make_service_with_tab_ids(tab_ids)

        apply_formatting(service, "sheet-1", budget_status_row_count=3)

        batch_call = spreadsheets.batchUpdate.call_args
        requests = batch_call[1]["body"]["requests"]
        width_requests = [r for r in requests if "updateDimensionProperties" in r]
        # Transactions Description + Budget Status Category + Budget Status Status
        assert len(width_requests) == 3


# ---------------------------------------------------------------------------
# add_chart and chart generation functions
# ---------------------------------------------------------------------------

from src.sheets import (
    add_chart,
    add_budget_status_chart,
    add_category_breakdown_charts,
    add_kpis_charts,
    add_monthly_summary_charts,
    _source_range,
)


class TestAddChart:
    def test_basic_column_chart(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.batchUpdate.return_value.execute.return_value = {}

        add_chart(service, "sheet-1", 0, {
            "title": "Test Chart",
            "chart_type": "COLUMN",
            "domains": [_source_range(0, 0, 5, 0, 1)],
            "series": [{"range": _source_range(0, 0, 5, 1, 2)}],
            "anchor_cell": {"rowIndex": 6, "columnIndex": 0},
        })

        batch_call = spreadsheets.batchUpdate.call_args
        requests = batch_call[1]["body"]["requests"]
        assert len(requests) == 1
        chart = requests[0]["addChart"]["chart"]
        assert chart["spec"]["title"] == "Test Chart"
        assert chart["spec"]["basicChart"]["chartType"] == "COLUMN"
        assert chart["position"]["overlayPosition"]["anchorCell"]["rowIndex"] == 6

    def test_pie_chart(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.batchUpdate.return_value.execute.return_value = {}

        add_chart(service, "sheet-1", 0, {
            "title": "Pie Test",
            "chart_type": "PIE",
            "domains": [_source_range(0, 0, 1, 0, 3)],
            "series": [{"range": _source_range(0, 1, 2, 0, 3)}],
            "anchor_cell": {"rowIndex": 3, "columnIndex": 0},
        })

        batch_call = spreadsheets.batchUpdate.call_args
        requests = batch_call[1]["body"]["requests"]
        chart_spec = requests[0]["addChart"]["chart"]["spec"]
        assert "pieChart" in chart_spec
        assert "basicChart" not in chart_spec

    def test_stacked_chart(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.batchUpdate.return_value.execute.return_value = {}

        add_chart(service, "sheet-1", 0, {
            "title": "Stacked",
            "chart_type": "COLUMN",
            "stacked": True,
            "domains": [_source_range(0, 0, 5, 0, 1)],
            "series": [{"range": _source_range(0, 0, 5, 1, 2)}],
            "anchor_cell": {"rowIndex": 0, "columnIndex": 0},
        })

        batch_call = spreadsheets.batchUpdate.call_args
        basic = batch_call[1]["body"]["requests"][0]["addChart"]["chart"]["spec"]["basicChart"]
        assert basic["stackedType"] == "STACKED"

    def test_chart_with_color(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.batchUpdate.return_value.execute.return_value = {}

        add_chart(service, "sheet-1", 0, {
            "title": "Colored",
            "chart_type": "BAR",
            "domains": [_source_range(0, 0, 3, 0, 1)],
            "series": [
                {
                    "range": _source_range(0, 0, 3, 1, 2),
                    "color": {"red": 0.4, "green": 0.8, "blue": 0.4},
                },
            ],
            "anchor_cell": {"rowIndex": 0, "columnIndex": 0},
        })

        batch_call = spreadsheets.batchUpdate.call_args
        series = batch_call[1]["body"]["requests"][0]["addChart"]["chart"]["spec"]["basicChart"]["series"]
        assert series[0]["color"] == {"red": 0.4, "green": 0.8, "blue": 0.4}


class TestAddMonthlySummaryCharts:
    def test_creates_two_charts(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.batchUpdate.return_value.execute.return_value = {}
        metrics = _sample_metrics()

        add_monthly_summary_charts(service, "sheet-1", 1, metrics)

        assert spreadsheets.batchUpdate.call_count == 2

    def test_no_charts_when_no_combined(self):
        service, spreadsheets, _ = _make_service()
        metrics = [
            MonthlyMetrics(month="2024-01", person="John"),
        ]

        add_monthly_summary_charts(service, "sheet-1", 1, metrics)

        spreadsheets.batchUpdate.assert_not_called()


class TestAddCategoryBreakdownCharts:
    def test_creates_pie_and_bar_charts(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.batchUpdate.return_value.execute.return_value = {}

        add_category_breakdown_charts(service, "sheet-1", 2, num_categories=3, num_data_rows=2)

        assert spreadsheets.batchUpdate.call_count == 2
        # First call should be pie chart
        first_chart = spreadsheets.batchUpdate.call_args_list[0][1]["body"]["requests"][0]
        assert "pieChart" in first_chart["addChart"]["chart"]["spec"]
        # Second call should be column chart
        second_chart = spreadsheets.batchUpdate.call_args_list[1][1]["body"]["requests"][0]
        assert second_chart["addChart"]["chart"]["spec"]["basicChart"]["chartType"] == "COLUMN"

    def test_no_charts_when_no_data(self):
        service, spreadsheets, _ = _make_service()

        add_category_breakdown_charts(service, "sheet-1", 2, num_categories=0, num_data_rows=0)

        spreadsheets.batchUpdate.assert_not_called()


class TestAddKpisCharts:
    def test_creates_charts_for_top_categories_and_trends(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.batchUpdate.return_value.execute.return_value = {}
        kpis = KPIs(
            top_categories=[("Groceries", 500.0), ("Dining", 300.0)],
            mom_trends={"2024-02": 10.0, "2024-03": -5.0},
            overall_savings_rate=50.0,
            total_budget=1000.0,
            total_actual=500.0,
            budget_health_pct=50.0,
        )

        add_kpis_charts(service, "sheet-1", 3, kpis)

        # Should create 2 charts: top categories bar + trend line
        assert spreadsheets.batchUpdate.call_count == 2

    def test_only_top_categories_chart_when_no_trends(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.batchUpdate.return_value.execute.return_value = {}
        kpis = KPIs(
            top_categories=[("Groceries", 500.0)],
            mom_trends={},
        )

        add_kpis_charts(service, "sheet-1", 3, kpis)

        assert spreadsheets.batchUpdate.call_count == 1

    def test_no_charts_when_empty_kpis(self):
        service, spreadsheets, _ = _make_service()

        add_kpis_charts(service, "sheet-1", 3, KPIs())

        spreadsheets.batchUpdate.assert_not_called()


class TestAddBudgetStatusChart:
    def test_creates_bar_chart_with_colors(self):
        service, spreadsheets, _ = _make_service()
        spreadsheets.batchUpdate.return_value.execute.return_value = {}

        add_budget_status_chart(service, "sheet-1", 4, num_categories=3)

        assert spreadsheets.batchUpdate.call_count == 1
        chart = spreadsheets.batchUpdate.call_args[1]["body"]["requests"][0]["addChart"]["chart"]
        spec = chart["spec"]
        assert spec["basicChart"]["chartType"] == "BAR"
        # Should have 2 series (budget and actual) with colors
        series = spec["basicChart"]["series"]
        assert len(series) == 2
        assert "color" in series[0]
        assert "color" in series[1]

    def test_no_chart_when_no_categories(self):
        service, spreadsheets, _ = _make_service()

        add_budget_status_chart(service, "sheet-1", 4, num_categories=0)

        spreadsheets.batchUpdate.assert_not_called()
