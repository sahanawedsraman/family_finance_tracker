"""Tests for the metrics engine."""

from datetime import date

import pytest

from src.metrics import MonthlyMetrics, KPIs, compute_monthly_metrics, compute_kpis
from src.models import Transaction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _txn(day, desc, amount, person="John", category="Other"):
    return Transaction(
        date=date(2024, 1, day) if isinstance(day, int) else day,
        description=desc,
        amount=amount,
        category=category,
        person=person,
    )


# ---------------------------------------------------------------------------
# compute_monthly_metrics — single month, single person
# ---------------------------------------------------------------------------

class TestSingleMonthSinglePerson:
    def test_basic_totals(self):
        txns = [
            _txn(5, "Salary", 5000.0, person="John"),
            _txn(10, "Groceries", -200.0, person="John", category="Groceries"),
            _txn(15, "Dining", -100.0, person="John", category="Dining"),
        ]
        results = compute_monthly_metrics(txns, budgets={}, persons=["John"])

        # Should have only Combined
        assert len(results) == 1
        combined = results[0]

        assert combined.month == "2024-01"
        assert combined.person == "Combined"
        assert combined.total_income == 5000.0
        assert combined.total_expenses == -300.0
        assert combined.net_savings == 4700.0
        assert combined.savings_rate == pytest.approx(94.0)
        assert combined.avg_daily_spending == pytest.approx(300.0 / 31)

    def test_category_spending(self):
        txns = [
            _txn(1, "Salary", 3000.0, category="Income"),
            _txn(5, "Walmart", -150.0, category="Groceries"),
            _txn(10, "Netflix", -15.0, category="Entertainment"),
        ]
        results = compute_monthly_metrics(txns, budgets={}, persons=["John"])
        combined = results[0]

        assert combined.category_spending == {"Groceries": 150.0, "Entertainment": 15.0}

    def test_budget_utilization(self):
        txns = [
            _txn(5, "Walmart", -400.0, category="Groceries"),
            _txn(10, "Target", -600.0, category="Shopping"),
        ]
        budgets = {"Groceries": 800, "Shopping": 500, "Dining": 400}
        results = compute_monthly_metrics(txns, budgets=budgets, persons=["John"])
        combined = results[0]

        assert combined.category_budget_utilization["Groceries"] == pytest.approx(50.0)
        assert combined.category_budget_utilization["Shopping"] == pytest.approx(120.0)
        assert combined.category_budget_utilization["Dining"] == pytest.approx(0.0)

    def test_no_budget_for_category(self):
        """Categories without a budget should not appear in utilization."""
        txns = [_txn(1, "Random", -50.0, category="Misc")]
        results = compute_monthly_metrics(txns, budgets={}, persons=["John"])
        combined = results[0]

        assert "Misc" not in combined.category_budget_utilization
        assert combined.category_spending == {"Misc": 50.0}


# ---------------------------------------------------------------------------
# compute_monthly_metrics — multiple months
# ---------------------------------------------------------------------------

class TestMultipleMonths:
    def test_two_months(self):
        txns = [
            _txn(date(2024, 1, 5), "Salary", 5000.0),
            _txn(date(2024, 1, 10), "Groceries", -200.0, category="Groceries"),
            _txn(date(2024, 2, 5), "Salary", 5000.0),
            _txn(date(2024, 2, 10), "Groceries", -300.0, category="Groceries"),
        ]
        results = compute_monthly_metrics(txns, budgets={}, persons=["John"])

        months = sorted({m.month for m in results})
        assert months == ["2024-01", "2024-02"]

        jan_combined = [m for m in results if m.month == "2024-01" and m.person == "Combined"][0]
        feb_combined = [m for m in results if m.month == "2024-02" and m.person == "Combined"][0]

        assert jan_combined.total_expenses == -200.0
        assert feb_combined.total_expenses == -300.0


# ---------------------------------------------------------------------------
# compute_monthly_metrics — multiple persons
# ---------------------------------------------------------------------------

class TestMultiplePersons:
    def test_two_persons_same_month(self):
        txns = [
            _txn(5, "Salary", 5000.0, person="John"),
            _txn(5, "Salary", 4000.0, person="Jane"),
            _txn(10, "Groceries", -200.0, person="John", category="Groceries"),
            _txn(12, "Dining", -150.0, person="Jane", category="Dining"),
        ]
        results = compute_monthly_metrics(txns, budgets={}, persons=["John", "Jane"])

        # Only Combined now
        assert len(results) == 1
        combined = results[0]

        assert combined.total_income == 9000.0
        assert combined.total_expenses == -350.0
        assert combined.net_savings == 8650.0

    def test_person_with_no_transactions(self):
        """A configured person with no transactions doesn't affect combined."""
        txns = [_txn(5, "Salary", 3000.0, person="John")]
        results = compute_monthly_metrics(txns, budgets={}, persons=["John", "Jane"])

        assert len(results) == 1
        assert results[0].person == "Combined"
        assert results[0].total_income == 3000.0


# ---------------------------------------------------------------------------
# compute_monthly_metrics — edge cases
# ---------------------------------------------------------------------------

class TestMetricsEdgeCases:
    def test_empty_transactions(self):
        assert compute_monthly_metrics([], budgets={}, persons=["John"]) == []

    def test_zero_income(self):
        txns = [_txn(5, "Groceries", -200.0, category="Groceries")]
        results = compute_monthly_metrics(txns, budgets={}, persons=["John"])
        combined = results[0]

        assert combined.total_income == 0.0
        assert combined.savings_rate == 0.0

    def test_unknown_person_included_in_combined(self):
        """Transactions from unknown persons should still appear in Combined."""
        txns = [
            _txn(5, "Salary", 3000.0, person="Unknown"),
        ]
        results = compute_monthly_metrics(txns, budgets={}, persons=["John"])

        assert len(results) == 1
        combined = results[0]
        assert combined.total_income == 3000.0


# ---------------------------------------------------------------------------
# compute_kpis
# ---------------------------------------------------------------------------

class TestComputeKPIs:
    def _make_combined_metrics(self):
        """Helper: two months of combined metrics."""
        return [
            MonthlyMetrics(
                month="2024-01",
                person="Combined",
                total_income=9000.0,
                total_expenses=-3500.0,
                net_savings=5500.0,
                savings_rate=61.11,
                avg_daily_spending=112.9,
                category_spending={
                    "Groceries": 800.0,
                    "Dining": 500.0,
                    "Shopping": 700.0,
                    "Utilities": 300.0,
                    "Entertainment": 200.0,
                    "Healthcare": 100.0,
                    "Transportation": 900.0,
                },
                category_budget_utilization={"Groceries": 100.0, "Dining": 125.0},
            ),
            MonthlyMetrics(
                month="2024-02",
                person="Combined",
                total_income=9000.0,
                total_expenses=-4000.0,
                net_savings=5000.0,
                savings_rate=55.56,
                avg_daily_spending=137.93,
                category_spending={
                    "Groceries": 900.0,
                    "Dining": 600.0,
                    "Shopping": 800.0,
                    "Utilities": 300.0,
                    "Entertainment": 250.0,
                    "Healthcare": 150.0,
                    "Transportation": 1000.0,
                },
                category_budget_utilization={"Groceries": 112.5, "Dining": 150.0},
            ),
        ]

    def test_top_categories(self):
        metrics = self._make_combined_metrics()
        kpis = compute_kpis(metrics)

        # Top 5 by total spend across both months
        names = [name for name, _ in kpis.top_categories]
        assert len(kpis.top_categories) == 5
        # Transportation: 900+1000=1900 should be #1
        assert names[0] == "Transportation"

    def test_mom_trends(self):
        metrics = self._make_combined_metrics()
        kpis = compute_kpis(metrics)

        # Jan expenses=3500, Feb expenses=4000 → +14.29%
        assert "2024-02" in kpis.mom_trends
        assert kpis.mom_trends["2024-02"] == pytest.approx(14.2857, rel=1e-2)

    def test_overall_savings_rate(self):
        metrics = self._make_combined_metrics()
        kpis = compute_kpis(metrics)

        # total income=18000, total expenses=-7500, net=10500
        expected = 10500.0 / 18000.0 * 100
        assert kpis.overall_savings_rate == pytest.approx(expected)

    def test_budget_health(self):
        metrics = self._make_combined_metrics()
        budgets = {"Groceries": 800, "Dining": 400}
        kpis = compute_kpis(metrics, budgets=budgets)

        # total budget = (800+400) * 2 months = 2400
        assert kpis.total_budget == pytest.approx(2400.0)
        # total actual = sum of all category spending across both combined months
        total_actual = sum(sum(m.category_spending.values()) for m in metrics)
        assert kpis.total_actual == pytest.approx(total_actual)
        assert kpis.budget_health_pct == pytest.approx(total_actual / 2400.0 * 100)

    def test_no_budgets(self):
        metrics = self._make_combined_metrics()
        kpis = compute_kpis(metrics)

        assert kpis.total_budget == 0.0
        assert kpis.budget_health_pct == 0.0


class TestKPIsEdgeCases:
    def test_empty_metrics(self):
        kpis = compute_kpis([])
        assert kpis.top_categories == []
        assert kpis.mom_trends == {}
        assert kpis.overall_savings_rate == 0.0

    def test_single_month(self):
        metrics = [
            MonthlyMetrics(
                month="2024-03",
                person="Combined",
                total_income=5000.0,
                total_expenses=-2000.0,
                net_savings=3000.0,
                savings_rate=60.0,
                avg_daily_spending=64.52,
                category_spending={"Groceries": 1000.0, "Dining": 1000.0},
                category_budget_utilization={},
            ),
        ]
        kpis = compute_kpis(metrics)

        assert len(kpis.top_categories) == 2
        # No MoM trend with a single month
        assert kpis.mom_trends == {}
        assert kpis.overall_savings_rate == pytest.approx(60.0)

    def test_zero_income(self):
        metrics = [
            MonthlyMetrics(
                month="2024-01",
                person="Combined",
                total_income=0.0,
                total_expenses=-500.0,
                net_savings=-500.0,
                savings_rate=0.0,
                avg_daily_spending=16.13,
                category_spending={"Groceries": 500.0},
                category_budget_utilization={},
            ),
        ]
        kpis = compute_kpis(metrics)
        assert kpis.overall_savings_rate == 0.0

    def test_no_combined_rows(self):
        """If there are no Combined rows, KPIs should be empty."""
        metrics = [
            MonthlyMetrics(
                month="2024-01",
                person="John",
                total_income=5000.0,
                total_expenses=-1000.0,
                net_savings=4000.0,
                savings_rate=80.0,
                avg_daily_spending=32.26,
                category_spending={"Groceries": 1000.0},
                category_budget_utilization={},
            ),
        ]
        kpis = compute_kpis(metrics)
        assert kpis.top_categories == []
        assert kpis.overall_savings_rate == 0.0

    def test_zero_previous_expenses_mom(self):
        """MoM trend should be 0% when previous month had zero expenses."""
        metrics = [
            MonthlyMetrics(
                month="2024-01", person="Combined",
                total_income=5000.0, total_expenses=0.0,
                net_savings=5000.0, savings_rate=100.0,
                avg_daily_spending=0.0,
                category_spending={}, category_budget_utilization={},
            ),
            MonthlyMetrics(
                month="2024-02", person="Combined",
                total_income=5000.0, total_expenses=-1000.0,
                net_savings=4000.0, savings_rate=80.0,
                avg_daily_spending=34.48,
                category_spending={"Groceries": 1000.0},
                category_budget_utilization={},
            ),
        ]
        kpis = compute_kpis(metrics)
        assert kpis.mom_trends["2024-02"] == 0.0
