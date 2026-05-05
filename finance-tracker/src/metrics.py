"""Metrics and KPI computation for the Finance Tracker."""

import logging
from calendar import monthrange
from collections import defaultdict
from dataclasses import dataclass, field

from src.models import Transaction

logger = logging.getLogger(__name__)


@dataclass
class MonthlyMetrics:
    """Aggregated financial metrics for a single month and person (or combined)."""

    month: str  # "YYYY-MM"
    person: str  # person name or "Combined"
    total_income: float = 0.0
    total_expenses: float = 0.0
    net_savings: float = 0.0
    savings_rate: float = 0.0  # percentage
    avg_daily_spending: float = 0.0
    category_spending: dict[str, float] = field(default_factory=dict)
    category_budget_utilization: dict[str, float] = field(default_factory=dict)


@dataclass
class AnnualMetrics:
    """Aggregated financial metrics for a single year and person (or combined)."""

    year: str  # "YYYY"
    person: str
    total_income: float = 0.0
    total_expenses: float = 0.0
    net_savings: float = 0.0
    savings_rate: float = 0.0
    avg_monthly_spending: float = 0.0
    category_spending: dict[str, float] = field(default_factory=dict)


@dataclass
class KPIs:
    """Aggregate KPIs across all months."""

    top_categories: list[tuple[str, float]] = field(default_factory=list)
    mom_trends: dict[str, float] = field(default_factory=dict)  # month -> % change
    overall_savings_rate: float = 0.0
    total_budget: float = 0.0
    total_actual: float = 0.0
    budget_health_pct: float = 0.0
    insights: list[str] = field(default_factory=list)


def _build_metrics(
    month: str,
    person: str,
    txns: list[Transaction],
    budgets: dict[str, float],
) -> MonthlyMetrics:
    """Build a MonthlyMetrics object from a list of transactions for one month/person."""
    total_income = sum(t.amount for t in txns if t.amount > 0 and t.category != "Transfer")
    total_expenses = sum(t.amount for t in txns if t.amount < 0 and t.category != "Transfer")

    net_savings = total_income + total_expenses  # expenses are negative
    savings_rate = (net_savings / total_income * 100) if total_income > 0 else 0.0

    # Average daily spending: total absolute expenses / days in month
    year, mon = int(month[:4]), int(month[5:7])
    days_in_month = monthrange(year, mon)[1]
    avg_daily_spending = abs(total_expenses) / days_in_month if days_in_month > 0 else 0.0

    # Per-category spending (absolute values of negative amounts)
    category_spending: dict[str, float] = defaultdict(float)
    for t in txns:
        if t.amount < 0:
            category_spending[t.category] += abs(t.amount)

    # Budget utilization
    category_budget_utilization: dict[str, float] = {}
    for cat, spent in category_spending.items():
        if cat in budgets and budgets[cat] > 0:
            category_budget_utilization[cat] = spent / budgets[cat] * 100
    # Categories with budget but no spending still get 0%
    for cat in budgets:
        if cat not in category_budget_utilization:
            category_budget_utilization[cat] = 0.0

    return MonthlyMetrics(
        month=month,
        person=person,
        total_income=total_income,
        total_expenses=total_expenses,
        net_savings=net_savings,
        savings_rate=savings_rate,
        avg_daily_spending=avg_daily_spending,
        category_spending=dict(category_spending),
        category_budget_utilization=category_budget_utilization,
    )


def compute_monthly_metrics(
    transactions: list[Transaction],
    budgets: dict[str, float],
    persons: list[str],
) -> list[MonthlyMetrics]:
    """Compute per-month combined family metrics.

    Returns a list of MonthlyMetrics with one "Combined" entry per month.
    """
    if not transactions:
        return []

    # Group all transactions by month
    grouped: dict[str, list[Transaction]] = defaultdict(list)
    for t in transactions:
        month_key = t.date.strftime("%Y-%m")
        grouped[month_key].append(t)

    all_months = sorted(grouped.keys())
    results: list[MonthlyMetrics] = []

    for month in all_months:
        month_txns = grouped[month]
        if month_txns:
            results.append(_build_metrics(month, "Combined", month_txns, budgets))

    logger.info(
        "Computed monthly metrics: %d months, %d metric rows",
        len(all_months),
        len(results),
    )
    return results


def _generate_insights(
    combined: list[MonthlyMetrics],
    total_cat_spending: dict[str, float],
    budgets: dict[str, float],
    num_months: int,
    savings_rate: float,
    budget_health_pct: float,
    mom_trends: dict[str, float],
) -> list[str]:
    """Generate gentle, actionable financial insights from the data."""
    insights: list[str] = []

    # Savings rate insights
    if savings_rate >= 30:
        insights.append(f"You're saving {savings_rate:.1f}% of your income — that's really strong.")
    elif savings_rate >= 20:
        insights.append(f"Savings rate is {savings_rate:.1f}%, right on track with the 20% guideline.")
    elif savings_rate >= 10:
        insights.append(f"Savings rate is {savings_rate:.1f}%. A little more could go a long way toward the 20% goal.")
    elif savings_rate > 0:
        insights.append(f"Savings rate is {savings_rate:.1f}%. It might help to look for a few areas to trim.")
    else:
        insights.append("Spending is currently higher than income. Worth reviewing together where to adjust.")

    # Budget overruns
    over_budget = []
    under_budget = []
    for cat, budget_amt in budgets.items():
        if budget_amt <= 0:
            continue
        actual = total_cat_spending.get(cat, 0)
        monthly_budget = budget_amt * num_months
        if monthly_budget > 0:
            pct = actual / monthly_budget * 100
            if pct > 120:
                over_budget.append((cat, pct, actual - monthly_budget))
            elif pct < 50 and actual > 0:
                under_budget.append((cat, pct, monthly_budget - actual))

    over_budget.sort(key=lambda x: x[1], reverse=True)
    for cat, pct, overage in over_budget[:3]:
        insights.append(f"{cat} is at {pct:.0f}% of budget (${overage:,.0f} over) — might be worth a look.")

    under_budget.sort(key=lambda x: x[2], reverse=True)
    for cat, pct, savings in under_budget[:2]:
        insights.append(f"Nice — {cat} is only at {pct:.0f}% of budget. ${savings:,.0f} under.")

    # Spending trend insights
    if mom_trends:
        sorted_months = sorted(mom_trends.keys())
        recent_trends = [mom_trends[m] for m in sorted_months[-3:]]
        if all(t > 5 for t in recent_trends):
            insights.append("Spending has been gradually increasing — just something to keep an eye on.")
        elif all(t < -5 for t in recent_trends):
            insights.append("Spending has been coming down lately — nice trend.")

        latest = sorted_months[-1]
        latest_pct = mom_trends[latest]
        if latest_pct > 15:
            insights.append(f"Spending was up {latest_pct:.1f}% in {latest} — could be a one-time thing or worth checking.")
        elif latest_pct < -15:
            insights.append(f"Spending was down {abs(latest_pct):.1f}% in {latest} — good progress.")

    # Top spending category
    if total_cat_spending:
        top_cat, top_amt = max(total_cat_spending.items(), key=lambda x: x[1])
        total_spending = sum(total_cat_spending.values())
        if total_spending > 0:
            pct_of_total = top_amt / total_spending * 100
            if pct_of_total > 30:
                insights.append(f"{top_cat} makes up {pct_of_total:.0f}% of spending (${top_amt:,.0f}). Just something to be aware of.")

    # Overall budget health
    if budget_health_pct > 100:
        insights.append(f"Overall spending is at {budget_health_pct:.0f}% of budget — a bit over, but adjustable.")
    elif budget_health_pct > 90:
        insights.append(f"Spending is at {budget_health_pct:.0f}% of budget — getting close but still on track.")

    return insights


def compute_kpis(
    monthly_metrics: list[MonthlyMetrics],
    budgets: dict[str, float] | None = None,
) -> KPIs:
    """Compute aggregate KPIs across all months from combined metrics.

    Args:
        monthly_metrics: List of MonthlyMetrics (should include Combined rows).
        budgets: Optional per-category monthly budget amounts for budget health calc.
    """
    if not monthly_metrics:
        return KPIs()

    # Use only "Combined" rows for aggregate KPIs
    combined = [m for m in monthly_metrics if m.person == "Combined"]
    if not combined:
        return KPIs()

    # Top 5 spending categories (aggregate across all months)
    total_cat_spending: dict[str, float] = defaultdict(float)
    for m in combined:
        for cat, amt in m.category_spending.items():
            total_cat_spending[cat] += amt
    sorted_cats = sorted(total_cat_spending.items(), key=lambda x: x[1], reverse=True)
    top_categories = sorted_cats[:5]

    # Month-over-month spending trends (% change)
    sorted_combined = sorted(combined, key=lambda m: m.month)
    mom_trends: dict[str, float] = {}
    for i in range(1, len(sorted_combined)):
        prev_expenses = abs(sorted_combined[i - 1].total_expenses)
        curr_expenses = abs(sorted_combined[i].total_expenses)
        if prev_expenses > 0:
            pct_change = (curr_expenses - prev_expenses) / prev_expenses * 100
        else:
            pct_change = 0.0
        mom_trends[sorted_combined[i].month] = pct_change

    # Overall savings rate
    total_income = sum(m.total_income for m in combined)
    total_expenses = sum(m.total_expenses for m in combined)
    net_savings = total_income + total_expenses
    overall_savings_rate = (net_savings / total_income * 100) if total_income > 0 else 0.0

    # Budget health
    total_actual = sum(sum(m.category_spending.values()) for m in combined)
    num_months = len(combined)
    if budgets:
        total_budget = sum(budgets.values()) * num_months
    else:
        total_budget = 0.0
    budget_health_pct = (total_actual / total_budget * 100) if total_budget > 0 else 0.0

    # Generate insights
    insights = _generate_insights(
        combined, total_cat_spending, budgets or {}, num_months,
        overall_savings_rate, budget_health_pct, mom_trends,
    )

    kpis = KPIs(
        top_categories=top_categories,
        mom_trends=mom_trends,
        overall_savings_rate=overall_savings_rate,
        total_budget=total_budget,
        total_actual=total_actual,
        budget_health_pct=budget_health_pct,
        insights=insights,
    )
    logger.info(
        "Computed KPIs: savings rate=%.1f%%, budget health=%.1f%%, top categories=%d",
        overall_savings_rate,
        budget_health_pct,
        len(top_categories),
    )
    return kpis


def compute_annual_metrics(
    monthly_metrics: list[MonthlyMetrics],
    persons: list[str],
) -> list[AnnualMetrics]:
    """Aggregate monthly metrics into annual family summaries.

    Returns one combined entry per year.
    """
    if not monthly_metrics:
        return []

    # Group monthly metrics by year (all are already "Combined")
    grouped: dict[str, list[MonthlyMetrics]] = defaultdict(list)
    for m in monthly_metrics:
        year = m.month[:4]
        grouped[year].append(m)

    all_years = sorted(grouped.keys())
    results: list[AnnualMetrics] = []

    for year in all_years:
        months = grouped[year]

        total_income = sum(m.total_income for m in months)
        total_expenses = sum(m.total_expenses for m in months)
        net_savings = total_income + total_expenses
        savings_rate = (net_savings / total_income * 100) if total_income > 0 else 0.0
        num_months = len(months)
        avg_monthly_spending = abs(total_expenses) / num_months if num_months > 0 else 0.0

        cat_spending: dict[str, float] = defaultdict(float)
        for m in months:
            for cat, amt in m.category_spending.items():
                cat_spending[cat] += amt

        results.append(AnnualMetrics(
            year=year,
            person="Combined",
            total_income=total_income,
            total_expenses=total_expenses,
            net_savings=net_savings,
            savings_rate=savings_rate,
            avg_monthly_spending=avg_monthly_spending,
            category_spending=dict(cat_spending),
        ))

    logger.info("Computed annual metrics: %d years", len(all_years))
    return results
