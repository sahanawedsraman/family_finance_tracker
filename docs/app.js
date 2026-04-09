/* global google, Chart, CONFIG */

let tokenClient;
let accessToken = null;
let charts = {};

// ── Raw data stores (loaded once from Sheets) ──
let rawKpiRows = [];
let rawMonthlyData = [];
let rawAnnualData = [];
let rawCategoryRows = [];
let rawBudgetData = [];
let rawTransactionData = [];

// ── Google Identity Services ──

function initAuth() {
  const script = document.createElement('script');
  script.src = 'https://accounts.google.com/gsi/client';
  script.onload = () => {
    tokenClient = google.accounts.oauth2.initTokenClient({
      client_id: CONFIG.GOOGLE_CLIENT_ID,
      scope: CONFIG.SCOPES,
      callback: onTokenResponse,
    });
  };
  document.head.appendChild(script);

  document.getElementById('btn-signin').addEventListener('click', () => {
    tokenClient.requestAccessToken();
  });
  document.getElementById('btn-signout').addEventListener('click', signOut);
  document.getElementById('btn-refresh').addEventListener('click', () => {
    if (accessToken) loadAllData();
  });
}

function onTokenResponse(resp) {
  if (resp.error) { console.error('Auth error:', resp); return; }
  accessToken = resp.access_token;
  showDashboard();
  loadAllData();
}

function signOut() {
  if (accessToken) { google.accounts.oauth2.revoke(accessToken, () => {}); accessToken = null; }
  showAuthScreen();
}

function showDashboard() {
  document.getElementById('auth-screen').classList.add('hidden');
  document.getElementById('dashboard').classList.remove('hidden');
}

function showAuthScreen() {
  document.getElementById('dashboard').classList.add('hidden');
  document.getElementById('auth-screen').classList.remove('hidden');
}

// ── Sheets API ──

async function fetchSheet(tab) {
  if (!accessToken) throw new Error('Not authenticated');
  const range = encodeURIComponent(tab);
  const url = `https://sheets.googleapis.com/v4/spreadsheets/${CONFIG.SPREADSHEET_ID}/values/${range}`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${accessToken}` } });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.error?.message || `Failed to fetch ${tab}`);
  }
  const data = await res.json();
  return data.values || [];
}

function parseRows(rows) {
  if (!rows || rows.length < 2) return [];
  const headers = rows[0];
  return rows.slice(1).map(row => {
    const obj = {};
    headers.forEach((h, i) => { obj[h] = row[i] || ''; });
    return obj;
  });
}

// ── Data Loading ──

async function loadAllData() {
  try {
    const [kpiRows, monthlyRows, annualRows, categoryRows, budgetRows, txnRows] = await Promise.all([
      fetchSheet(CONFIG.TABS.KPIS),
      fetchSheet(CONFIG.TABS.MONTHLY_SUMMARY),
      fetchSheet(CONFIG.TABS.ANNUAL_SUMMARY),
      fetchSheet(CONFIG.TABS.CATEGORY_BREAKDOWN),
      fetchSheet(CONFIG.TABS.BUDGET_STATUS),
      fetchSheet(CONFIG.TABS.TRANSACTIONS),
    ]);

    rawKpiRows = kpiRows;
    rawMonthlyData = parseRows(monthlyRows);
    rawAnnualData = parseRows(annualRows);
    rawCategoryRows = categoryRows;
    rawBudgetData = parseRows(budgetRows);
    rawTransactionData = parseRows(txnRows);

    populateYearFilter();
    setupFilterListeners();
    applyFilters();
  } catch (err) {
    console.error('Failed to load data:', err);
    alert('Failed to load data. Make sure the Sheet is shared with your account.');
  }
}

// ── Time Filter ──

function populateYearFilter() {
  const years = new Set();
  rawMonthlyData.forEach(r => { if (r.Month) years.add(r.Month.substring(0, 4)); });
  rawTransactionData.forEach(r => { if (r.Date) years.add(r.Date.substring(0, 4)); });

  const select = document.getElementById('filter-year');
  // Clear existing options except "All Years"
  select.innerHTML = '<option value="">All Years</option>';
  [...years].sort().reverse().forEach(y => {
    const opt = document.createElement('option');
    opt.value = y; opt.textContent = y;
    select.appendChild(opt);
  });
}

function setupFilterListeners() {
  document.getElementById('filter-year').addEventListener('change', applyFilters);
  document.getElementById('filter-month').addEventListener('change', applyFilters);
}

function getFilteredMonth() {
  // Returns "YYYY-MM" prefix or "" for all
  const year = document.getElementById('filter-year').value;
  const month = document.getElementById('filter-month').value;
  if (year && month) return `${year}-${month}`;
  if (year) return year;
  return '';
}

function filterByPeriod(data, monthKey) {
  const prefix = getFilteredMonth();
  if (!prefix) return data;
  return data.filter(r => (r[monthKey] || '').startsWith(prefix));
}

function applyFilters() {
  renderKPIs();
  renderMonthlySummary();
  renderAnnualSummary();
  renderCategoryBreakdown();
  renderBudgetStatus();
  renderTransactions();
}

// ── KPIs ──

function renderKPIs() {
  const filtered = filterByPeriod(rawMonthlyData, 'Month');
  if (!filtered.length) {
    ['kpi-savings-rate', 'kpi-total-budget', 'kpi-total-spending', 'kpi-budget-health'].forEach(id => {
      document.getElementById(id).textContent = '—';
    });
    return;
  }

  const totalIncome = filtered.reduce((s, r) => s + (parseFloat(r['Total Income']) || 0), 0);
  const totalExpenses = filtered.reduce((s, r) => s + Math.abs(parseFloat(r['Total Expenses']) || 0), 0);
  const netSavings = totalIncome - totalExpenses;
  const savingsRate = totalIncome > 0 ? (netSavings / totalIncome * 100) : 0;

  const numMonths = filtered.length;
  const totalMonths = rawMonthlyData.length || 1;
  const overallBudget = parseFloat(rawKpiRows[1]?.[1]) || 0;
  const monthlyBudget = overallBudget / totalMonths;
  const periodBudget = monthlyBudget * numMonths;
  const budgetHealth = periodBudget > 0 ? (totalExpenses / periodBudget * 100) : 0;

  document.getElementById('kpi-savings-rate').textContent = savingsRate.toFixed(1) + '%';
  document.getElementById('kpi-total-budget').textContent = '$' + periodBudget.toLocaleString(undefined, {maximumFractionDigits: 0});
  document.getElementById('kpi-total-spending').textContent = '$' + totalExpenses.toLocaleString(undefined, {maximumFractionDigits: 0});

  const healthEl = document.getElementById('kpi-budget-health');
  healthEl.textContent = budgetHealth.toFixed(1) + '%';
  healthEl.style.color = budgetHealth <= 100 ? 'var(--green)' : 'var(--red)';

  // Render insights from KPIs tab
  renderInsights();
}

function renderInsights() {
  const container = document.getElementById('insights-list');
  // Find insights in KPI rows — they start after the "Insights & Recommendations" header
  const insights = [];
  let inInsights = false;
  for (const row of rawKpiRows) {
    if (row && row[0] && row[0].includes('Insights')) {
      inInsights = true;
      continue;
    }
    if (inInsights && row && row[0] && row[0].trim()) {
      insights.push(row[0]);
    }
  }

  if (!insights.length) {
    container.innerHTML = '';
    return;
  }

  container.innerHTML = insights
    .map(i => `<div class="insight-item">${i}</div>`)
    .join('');
}

// ── Monthly Summary ──

function renderMonthlySummary() {
  const data = filterByPeriod(rawMonthlyData, 'Month');

  const months = data.map(r => r.Month);
  const income = data.map(r => parseFloat(r['Total Income']) || 0);
  const expenses = data.map(r => Math.abs(parseFloat(r['Total Expenses']) || 0));
  const savings = data.map(r => parseFloat(r['Net Savings']) || 0);

  createOrUpdateChart('chart-income-expenses', 'bar', {
    labels: months,
    datasets: [
      { label: 'Income', data: income, backgroundColor: 'rgba(52, 211, 153, 0.7)' },
      { label: 'Expenses', data: expenses, backgroundColor: 'rgba(248, 113, 113, 0.7)' },
    ],
  }, { plugins: { title: { display: true, text: 'Income vs Expenses', color: '#e4e6f0' } } });

  createOrUpdateChart('chart-savings-trend', 'line', {
    labels: months,
    datasets: [{
      label: 'Net Savings', data: savings,
      borderColor: '#6c63ff', backgroundColor: 'rgba(108, 99, 255, 0.1)',
      fill: true, tension: 0.3,
    }],
  }, { plugins: { title: { display: true, text: 'Net Savings Trend', color: '#e4e6f0' } } });

  const container = document.getElementById('monthly-table-container');
  let html = '<div class="table-scroll"><table><thead><tr>';
  html += '<th>Month</th><th>Income</th><th>Expenses</th><th>Net Savings</th><th>Savings Rate</th>';
  html += '</tr></thead><tbody>';
  data.forEach(r => {
    const amt = parseFloat(r['Net Savings']) || 0;
    const cls = amt >= 0 ? 'amount-positive' : 'amount-negative';
    html += `<tr>
      <td>${r.Month}</td>
      <td>${fmtNum(r['Total Income'])}</td>
      <td>${fmtNum(r['Total Expenses'])}</td>
      <td class="${cls}">${fmtNum(r['Net Savings'])}</td>
      <td>${r['Savings Rate (%)'] || '0'}%</td>
    </tr>`;
  });
  html += '</tbody></table></div>';
  container.innerHTML = html;
}

// ── Annual Summary ──

function renderAnnualSummary() {
  const yearFilter = document.getElementById('filter-year').value;
  const data = yearFilter
    ? rawAnnualData.filter(r => r.Year === yearFilter)
    : rawAnnualData;

  const years = data.map(r => r.Year);
  const income = data.map(r => parseFloat(r['Total Income']) || 0);
  const expenses = data.map(r => Math.abs(parseFloat(r['Total Expenses']) || 0));
  const savings = data.map(r => parseFloat(r['Net Savings']) || 0);

  createOrUpdateChart('chart-annual-income-expenses', 'bar', {
    labels: years,
    datasets: [
      { label: 'Income', data: income, backgroundColor: 'rgba(52, 211, 153, 0.7)' },
      { label: 'Expenses', data: expenses, backgroundColor: 'rgba(248, 113, 113, 0.7)' },
    ],
  }, { plugins: { title: { display: true, text: 'Annual Income vs Expenses', color: '#e4e6f0' } } });

  createOrUpdateChart('chart-annual-savings', 'bar', {
    labels: years,
    datasets: [{
      label: 'Net Savings', data: savings,
      backgroundColor: savings.map(v => v >= 0 ? 'rgba(52, 211, 153, 0.7)' : 'rgba(248, 113, 113, 0.7)'),
    }],
  }, { plugins: { title: { display: true, text: 'Annual Net Savings', color: '#e4e6f0' } } });

  const container = document.getElementById('annual-table-container');
  let html = '<div class="table-scroll"><table><thead><tr>';
  html += '<th>Year</th><th>Income</th><th>Expenses</th><th>Net Savings</th><th>Savings Rate</th><th>Avg Monthly Spending</th>';
  html += '</tr></thead><tbody>';
  data.forEach(r => {
    const amt = parseFloat(r['Net Savings']) || 0;
    const cls = amt >= 0 ? 'amount-positive' : 'amount-negative';
    html += `<tr>
      <td>${r.Year}</td>
      <td>${fmtNum(r['Total Income'])}</td>
      <td>${fmtNum(r['Total Expenses'])}</td>
      <td class="${cls}">${fmtNum(r['Net Savings'])}</td>
      <td>${r['Savings Rate (%)'] || '0'}%</td>
      <td>${fmtNum(r['Avg Monthly Spending'])}</td>
    </tr>`;
  });
  html += '</tbody></table></div>';
  container.innerHTML = html;
}

// ── Category Breakdown ──

function renderCategoryBreakdown() {
  if (!rawCategoryRows || rawCategoryRows.length < 3) return;

  const headers = rawCategoryRows[0];
  const categories = headers.slice(1);
  // Row 1 is budget, rows 2+ are monthly data
  const allDataRows = rawCategoryRows.slice(2);

  // Filter by period
  const prefix = getFilteredMonth();
  const dataRows = prefix
    ? allDataRows.filter(row => (row[0] || '').startsWith(prefix))
    : allDataRows;

  const totals = categories.map((_, ci) =>
    dataRows.reduce((sum, row) => sum + (parseFloat(row[ci + 1]) || 0), 0)
  );

  const colors = generateColors(categories.length);

  createOrUpdateChart('chart-category-pie', 'pie', {
    labels: categories,
    datasets: [{ data: totals, backgroundColor: colors }],
  }, { plugins: { title: { display: true, text: 'Spending by Category', color: '#e4e6f0' } } });

  const datasets = categories.map((cat, ci) => ({
    label: cat,
    data: dataRows.map(row => parseFloat(row[ci + 1]) || 0),
    backgroundColor: colors[ci],
  }));

  createOrUpdateChart('chart-category-bar', 'bar', {
    labels: dataRows.map(r => r[0]),
    datasets,
  }, {
    plugins: { title: { display: true, text: 'Category Spending by Month', color: '#e4e6f0' } },
    scales: { x: { stacked: true }, y: { stacked: true } },
  });
}

// ── Budget Status ──

function renderBudgetStatus() {
  // Budget status is aggregate — we show it as-is but note the period
  const data = rawBudgetData;
  if (!data.length) return;

  const categories = data.map(r => r.Category);
  const budgets = data.map(r => parseFloat(r['Total Budget']) || 0);
  const actuals = data.map(r => parseFloat(r['Total Actual']) || 0);

  createOrUpdateChart('chart-budget', 'bar', {
    labels: categories,
    datasets: [
      { label: 'Budget', data: budgets, backgroundColor: 'rgba(52, 211, 153, 0.7)' },
      { label: 'Actual', data: actuals, backgroundColor: 'rgba(248, 113, 113, 0.7)' },
    ],
  }, {
    indexAxis: 'y',
    plugins: { title: { display: true, text: 'Budget vs Actual', color: '#e4e6f0' } },
  });

  const container = document.getElementById('budget-table-container');
  let html = '<div class="table-scroll"><table><thead><tr>';
  html += '<th>Category</th><th>Budget</th><th>Actual</th><th>Difference</th><th>Status</th>';
  html += '</tr></thead><tbody>';
  data.forEach(r => {
    const diff = parseFloat(r.Difference) || 0;
    const statusCls = r.Status === 'Under Budget' ? 'status-under' : 'status-over';
    html += `<tr>
      <td>${r.Category}</td>
      <td>${fmtNum(r['Total Budget'])}</td>
      <td>${fmtNum(r['Total Actual'])}</td>
      <td class="${diff >= 0 ? 'amount-positive' : 'amount-negative'}">${fmtNum(r.Difference)}</td>
      <td class="${statusCls}">${r.Status}</td>
    </tr>`;
  });
  html += '</tbody></table></div>';
  container.innerHTML = html;
}

// ── Transactions ──

let txnFiltersInitialized = false;

function renderTransactions() {
  const prefix = getFilteredMonth();
  const periodFiltered = prefix
    ? rawTransactionData.filter(r => (r.Date || '').startsWith(prefix))
    : rawTransactionData;

  if (!txnFiltersInitialized) {
    populateTransactionFilters(rawTransactionData);
    document.getElementById('txn-search').addEventListener('input', renderTransactions);
    document.getElementById('txn-person-filter').addEventListener('change', renderTransactions);
    document.getElementById('txn-category-filter').addEventListener('change', renderTransactions);
    txnFiltersInitialized = true;
  }

  const search = document.getElementById('txn-search').value.toLowerCase();
  const person = document.getElementById('txn-person-filter').value;
  const category = document.getElementById('txn-category-filter').value;

  const filtered = periodFiltered.filter(r => {
    if (person && r.Person !== person) return false;
    if (category && r.Category !== category) return false;
    if (search && !r.Description?.toLowerCase().includes(search)) return false;
    return true;
  });

  renderTransactionTable(filtered);
}

function populateTransactionFilters(data) {
  const persons = [...new Set(data.map(r => r.Person).filter(Boolean))];
  const categories = [...new Set(data.map(r => r.Category).filter(Boolean))];

  const personSelect = document.getElementById('txn-person-filter');
  personSelect.innerHTML = '<option value="">All People</option>';
  persons.forEach(p => {
    const opt = document.createElement('option');
    opt.value = p; opt.textContent = p;
    personSelect.appendChild(opt);
  });

  const catSelect = document.getElementById('txn-category-filter');
  catSelect.innerHTML = '<option value="">All Categories</option>';
  categories.sort().forEach(c => {
    const opt = document.createElement('option');
    opt.value = c; opt.textContent = c;
    catSelect.appendChild(opt);
  });
}

function renderTransactionTable(data) {
  const container = document.getElementById('transactions-table-container');
  let html = '<div class="table-scroll"><table><thead><tr>';
  html += '<th>Date</th><th>Description</th><th>Amount</th><th>Category</th><th>Person</th><th>Type</th>';
  html += '</tr></thead><tbody>';

  data.forEach(r => {
    const amt = parseFloat(r.Amount) || 0;
    const cls = amt >= 0 ? 'amount-positive' : 'amount-negative';
    html += `<tr>
      <td>${r.Date || ''}</td>
      <td>${r.Description || ''}</td>
      <td class="${cls}">${fmtNum(r.Amount)}</td>
      <td>${r.Category || ''}</td>
      <td>${r.Person || ''}</td>
      <td>${r.Type || ''}</td>
    </tr>`;
  });

  if (!data.length) {
    html += '<tr><td colspan="6" style="text-align:center;color:var(--text-muted)">No transactions found</td></tr>';
  }

  html += '</tbody></table></div>';
  container.innerHTML = html;
}

// ── Chart Helpers ──

Chart.defaults.color = '#8b8fa3';
Chart.defaults.borderColor = '#2e3345';

function createOrUpdateChart(canvasId, type, data, extraOpts = {}) {
  const ctx = document.getElementById(canvasId);
  if (charts[canvasId]) { charts[canvasId].destroy(); }
  charts[canvasId] = new Chart(ctx, {
    type, data,
    options: { responsive: true, maintainAspectRatio: true, ...extraOpts },
  });
}

function generateColors(count) {
  const palette = [
    '#6c63ff', '#34d399', '#f87171', '#fbbf24', '#60a5fa',
    '#a78bfa', '#f472b6', '#fb923c', '#2dd4bf', '#e879f9',
    '#84cc16', '#38bdf8', '#f43f5e', '#8b5cf6', '#14b8a6',
  ];
  return Array.from({ length: count }, (_, i) => palette[i % palette.length]);
}

function fmtNum(val) {
  const n = parseFloat(val) || 0;
  return '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

// ── Tab Navigation ──

document.querySelectorAll('.tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach(t => { t.classList.remove('active'); t.setAttribute('aria-selected', 'false'); });
    document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    tab.setAttribute('aria-selected', 'true');
    document.getElementById('tab-' + tab.dataset.tab).classList.add('active');
  });
});

// ── Init ──
initAuth();
