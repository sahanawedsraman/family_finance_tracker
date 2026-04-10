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

    // Restore cached token
    const cached = sessionStorage.getItem('finance_token');
    if (cached) {
      accessToken = cached;
      showDashboard();
      loadAllData();
    }
  };
  document.head.appendChild(script);

  document.getElementById('btn-signin').addEventListener('click', () => {
    tokenClient.requestAccessToken();
  });
  document.getElementById('btn-refresh').addEventListener('click', () => {
    if (accessToken) loadAllData();
  });
  document.getElementById('btn-privacy').addEventListener('click', togglePrivacy);
  initMenu();
}

function onTokenResponse(resp) {
  if (resp.error) { console.error('Auth error:', resp); return; }
  accessToken = resp.access_token;
  sessionStorage.setItem('finance_token', accessToken);
  showDashboard();
  loadAllData();
}

function signOut() {
  if (accessToken) { google.accounts.oauth2.revoke(accessToken, () => {}); accessToken = null; }
  sessionStorage.removeItem('finance_token');
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
  const cacheBust = Date.now();
  const url = `https://sheets.googleapis.com/v4/spreadsheets/${CONFIG.SPREADSHEET_ID}/values/${range}?t=${cacheBust}`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${accessToken}` }, cache: 'no-store' });
  if (!res.ok) {
    if (res.status === 401) {
      // Token expired — clear cache and show login
      sessionStorage.removeItem('finance_token');
      accessToken = null;
      showAuthScreen();
      throw new Error('Session expired. Please sign in again.');
    }
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
    const [kpiRows, monthlyRows, annualRows, categoryRows, budgetRows, txnRows, metaRows] = await Promise.all([
      fetchSheet(CONFIG.TABS.KPIS),
      fetchSheet(CONFIG.TABS.MONTHLY_SUMMARY),
      fetchSheet(CONFIG.TABS.ANNUAL_SUMMARY),
      fetchSheet(CONFIG.TABS.CATEGORY_BREAKDOWN),
      fetchSheet(CONFIG.TABS.BUDGET_STATUS),
      fetchSheet(CONFIG.TABS.TRANSACTIONS),
      fetchSheet(CONFIG.TABS.METADATA),
    ]);

    rawKpiRows = kpiRows;
    rawMonthlyData = parseRows(monthlyRows);
    rawAnnualData = parseRows(annualRows);
    rawCategoryRows = categoryRows;
    rawBudgetData = parseRows(budgetRows);
    rawTransactionData = parseRows(txnRows);

    // Display last updated
    const metaData = parseRows(metaRows);
    const lastUpdated = metaData.find(r => r.Key === 'Last Updated');
    document.getElementById('last-updated').textContent = lastUpdated ? lastUpdated.Value : 'Unknown';

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
  renderSparklines();
  renderSpendingPace();
  renderCategoryComparison();
  renderMonthlySummary();
  renderAnnualSummary();
  renderCategoryBreakdown();
  renderIncomeBreakdown();
  renderBudgetStatus();
  renderBudgetProgress();
  renderTransactions();
  renderRecurring();
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

  const totalIncome = filtered.reduce((s, r) => s + (parseNum(r['Total Income']) || 0), 0);
  const totalExpenses = filtered.reduce((s, r) => s + Math.abs(parseNum(r['Total Expenses']) || 0), 0);
  const netSavings = totalIncome - totalExpenses;
  const savingsRate = (totalIncome > 0 && totalExpenses > 0) ? (netSavings / totalIncome * 100) : 0;

  const numMonths = filtered.length;
  const totalMonths = rawMonthlyData.length || 1;
  const overallBudget = parseNum(rawKpiRows[1]?.[1]) || 0;
  const monthlyBudget = overallBudget / totalMonths;
  const periodBudget = monthlyBudget * numMonths;
  const budgetHealth = periodBudget > 0 ? (totalExpenses / periodBudget * 100) : 0;

  document.getElementById('kpi-savings-rate').textContent = savingsRate.toFixed(1) + '%';
  document.getElementById('kpi-total-budget').innerHTML = '<span class="money">$' + periodBudget.toLocaleString(undefined, {maximumFractionDigits: 0}) + '</span>';
  document.getElementById('kpi-total-spending').innerHTML = '<span class="money">$' + totalExpenses.toLocaleString(undefined, {maximumFractionDigits: 0}) + '</span>';

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
  const income = data.map(r => parseNum(r['Total Income']) || 0);
  const expenses = data.map(r => Math.abs(parseNum(r['Total Expenses']) || 0));
  const savings = data.map(r => parseNum(r['Net Savings']) || 0);

  createOrUpdateChart('chart-income-expenses', 'bar', {
    labels: months,
    datasets: [
      { label: 'Income', data: income, backgroundColor: 'rgba(91, 168, 140, 0.7)' },
      { label: 'Expenses', data: expenses, backgroundColor: 'rgba(212, 114, 106, 0.7)' },
    ],
  }, { plugins: { title: { display: true, text: 'Income vs Expenses', color: '#e4e6f0' } } });

  createOrUpdateChart('chart-savings-trend', 'line', {
    labels: months,
    datasets: [{
      label: 'Net Savings', data: savings,
      borderColor: '#7c6fae', backgroundColor: 'rgba(124, 111, 174, 0.1)',
      fill: true, tension: 0.3,
    }],
  }, { plugins: { title: { display: true, text: 'Net Savings Trend', color: '#e4e6f0' } } });

  const container = document.getElementById('monthly-table-container');
  let html = '<div class="table-scroll"><table><thead><tr>';
  html += '<th>Month</th><th>Income</th><th>Expenses</th><th>Net Savings</th><th>Savings Rate</th>';
  html += '</tr></thead><tbody>';
  data.forEach(r => {
    const amt = parseNum(r['Net Savings']) || 0;
    const cls = amt >= 0 ? 'amount-positive' : 'amount-negative';
    html += `<tr>
      <td>${r.Month}</td>
      <td>${fmtNum(r['Total Income'])}</td>
      <td>${fmtNum(Math.abs(parseNum(r['Total Expenses']) || 0))}</td>
      <td class="${cls}">${fmtNum(r['Net Savings'])}</td>
      <td>${r['Savings Rate (%)'] || '0'}%</td>
    </tr>`;
  });
  html += '</tbody></table></div>';
  container.innerHTML = html;
}

// ── Annual Summary ──

function renderAnnualSummary() {
  // Compute annual data from monthly summary (respects all filters)
  const filtered = filterByPeriod(rawMonthlyData, 'Month');

  // Group by year
  const byYear = {};
  filtered.forEach(r => {
    const year = (r.Month || '').substring(0, 4);
    if (!year) return;
    if (!byYear[year]) byYear[year] = { income: 0, expenses: 0, months: 0 };
    byYear[year].income += parseNum(r['Total Income']);
    byYear[year].expenses += Math.abs(parseNum(r['Total Expenses']));
    byYear[year].months++;
  });

  const years = Object.keys(byYear).sort();
  const income = years.map(y => byYear[y].income);
  const expenses = years.map(y => byYear[y].expenses);
  const savings = years.map(y => byYear[y].income - byYear[y].expenses);

  createOrUpdateChart('chart-annual-income-expenses', 'bar', {
    labels: years,
    datasets: [
      { label: 'Income', data: income, backgroundColor: 'rgba(91, 168, 140, 0.7)' },
      { label: 'Expenses', data: expenses, backgroundColor: 'rgba(212, 114, 106, 0.7)' },
    ],
  }, { plugins: { title: { display: true, text: 'Annual Income vs Expenses', color: '#e4e6f0' } } });

  createOrUpdateChart('chart-annual-savings', 'bar', {
    labels: years,
    datasets: [{
      label: 'Net Savings', data: savings,
      backgroundColor: savings.map(v => v >= 0 ? 'rgba(91, 168, 140, 0.7)' : 'rgba(212, 114, 106, 0.7)'),
    }],
  }, { plugins: { title: { display: true, text: 'Annual Net Savings', color: '#e4e6f0' } } });

  const container = document.getElementById('annual-table-container');
  let html = '<div class="table-scroll"><table><thead><tr>';
  html += '<th>Year</th><th>Income</th><th>Expenses</th><th>Net Savings</th><th>Savings Rate</th><th>Avg Monthly Spending</th>';
  html += '</tr></thead><tbody>';
  years.forEach(y => {
    const d = byYear[y];
    const net = d.income - d.expenses;
    const rate = d.income > 0 ? (net / d.income * 100) : 0;
    const avgMonthly = d.months > 0 ? d.expenses / d.months : 0;
    const cls = net >= 0 ? 'amount-positive' : 'amount-negative';
    html += `<tr>
      <td>${y}</td>
      <td>${fmtNum(d.income)}</td>
      <td>${fmtNum(d.expenses)}</td>
      <td class="${cls}">${fmtNum(net)}</td>
      <td>${rate.toFixed(1)}%</td>
      <td>${fmtNum(avgMonthly)}</td>
    </tr>`;
  });
  html += '</tbody></table></div>';
  container.innerHTML = html;
}

// ── Category Breakdown ──

function renderCategoryBreakdown() {
  const excludeCats = new Set(['Income', 'Taxes', 'Retirement', 'Investment', 'Transfer', 'Healthcare', 'Utilities']);

  // Build category data from transactions
  const prefix = getFilteredMonth();
  const txns = prefix
    ? rawTransactionData.filter(r => (r.Date || '').startsWith(prefix))
    : rawTransactionData;

  const catTotals = {};
  const catByMonth = {};
  txns.forEach(r => {
    const amt = parseNum(r.Amount);
    const cat = r.Category || 'Other';
    if (amt >= 0 || excludeCats.has(cat)) return;
    const absAmt = Math.abs(amt);
    catTotals[cat] = (catTotals[cat] || 0) + absAmt;
    const month = (r.Date || '').substring(0, 7);
    if (month) {
      if (!catByMonth[month]) catByMonth[month] = {};
      catByMonth[month][cat] = (catByMonth[month][cat] || 0) + absAmt;
    }
  });

  const categories = Object.keys(catTotals).sort((a, b) => catTotals[b] - catTotals[a]);

  // Render category chips
  const chipsContainer = document.getElementById('category-chips-container');
  if (!categories.length) {
    chipsContainer.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:2rem">No spending data found.</p>';
    return;
  }

  const colors = generateColors(categories.length);
  const totalSpending = Object.values(catTotals).reduce((a, b) => a + b, 0);

  let chipsHtml = '<div class="top-cats-row" style="margin-bottom:1.5rem">';
  categories.forEach((cat, i) => {
    const pct = totalSpending > 0 ? (catTotals[cat] / totalSpending * 100).toFixed(1) : 0;
    chipsHtml += `<div class="top-cat-chip" style="border-left: 3px solid ${colors[i]}">
      <span class="top-cat-name">${cat}</span>
      <span class="top-cat-amt">${fmtNum(catTotals[cat])}</span>
      <span style="font-size:0.7rem;color:var(--text-muted)">${pct}%</span>
    </div>`;
  });
  chipsHtml += '</div>';
  chipsContainer.innerHTML = chipsHtml;

  // Pie chart
  const totals = categories.map(c => catTotals[c]);
  createOrUpdateChart('chart-category-pie', 'pie', {
    labels: categories,
    datasets: [{ data: totals, backgroundColor: colors }],
  }, { plugins: { title: { display: true, text: 'Spending by Category', color: '#e4e6f0' } } });

  // Bar chart by month
  const months = Object.keys(catByMonth).sort();
  const datasets = categories.map((cat, ci) => ({
    label: cat,
    data: months.map(m => catByMonth[m]?.[cat] || 0),
    backgroundColor: colors[ci],
  }));

  createOrUpdateChart('chart-category-bar', 'bar', {
    labels: months,
    datasets,
  }, {
    plugins: { title: { display: true, text: 'Category Spending by Month', color: '#e4e6f0' } },
    scales: { x: { stacked: true }, y: { stacked: true } },
  });
}

// ── Budget Status ──

function renderBudgetStatus() {
  if (!rawBudgetData.length) return;

  const excludeCats = new Set(['Income', 'Taxes', 'Retirement', 'Investment', 'Transfer', 'Healthcare', 'Utilities']);
  const prefix = getFilteredMonth();

  // Get filtered transactions
  const txns = prefix
    ? rawTransactionData.filter(r => (r.Date || '').startsWith(prefix))
    : rawTransactionData;

  // Count months in the filtered period
  const months = new Set();
  txns.forEach(r => { const m = (r.Date || '').substring(0, 7); if (m) months.add(m); });
  const numMonths = months.size || 1;

  // Compute actual spending per category from transactions
  const actualSpending = {};
  txns.forEach(r => {
    const amt = parseNum(r.Amount);
    const cat = r.Category || 'Other';
    if (amt < 0 && !excludeCats.has(cat)) {
      actualSpending[cat] = (actualSpending[cat] || 0) + Math.abs(amt);
    }
  });

  // Build budget data from the Sheet's budget data (for monthly budget amounts)
  // and scale by number of months in the filter
  const budgetPerMonth = {};
  rawBudgetData.forEach(r => {
    const totalMonthsInSheet = rawMonthlyData.length || 1;
    budgetPerMonth[r.Category] = parseNum(r['Total Budget']) / totalMonthsInSheet;
  });

  const allCats = [...new Set([...Object.keys(budgetPerMonth), ...Object.keys(actualSpending)])]
    .filter(c => !excludeCats.has(c))
    .sort();

  const categories = [];
  const budgets = [];
  const actuals = [];
  const diffs = [];
  const statuses = [];

  allCats.forEach(cat => {
    const budget = (budgetPerMonth[cat] || 0) * numMonths;
    const actual = actualSpending[cat] || 0;
    if (budget === 0 && actual === 0) return;
    categories.push(cat);
    budgets.push(budget);
    actuals.push(actual);
    diffs.push(budget - actual);
    statuses.push(budget - actual >= 0 ? 'Under Budget' : 'Over Budget');
  });

  if (!categories.length) return;

  createOrUpdateChart('chart-budget', 'bar', {
    labels: categories,
    datasets: [
      { label: 'Budget', data: budgets, backgroundColor: 'rgba(91, 168, 140, 0.7)' },
      { label: 'Actual', data: actuals, backgroundColor: 'rgba(212, 114, 106, 0.7)' },
    ],
  }, {
    indexAxis: 'y',
    plugins: { title: { display: true, text: 'Budget vs Actual', color: '#e4e6f0' } },
  });

  const container = document.getElementById('budget-table-container');
  let html = '<div class="table-scroll"><table><thead><tr>';
  html += '<th>Category</th><th>Budget</th><th>Actual</th><th>Difference</th><th>Status</th>';
  html += '</tr></thead><tbody>';
  categories.forEach((cat, i) => {
    const statusCls = statuses[i] === 'Under Budget' ? 'status-under' : 'status-over';
    html += `<tr>
      <td>${cat}</td>
      <td>${fmtNum(budgets[i])}</td>
      <td>${fmtNum(actuals[i])}</td>
      <td class="${diffs[i] >= 0 ? 'amount-positive' : 'amount-negative'}">${fmtNum(diffs[i])}</td>
      <td class="${statusCls}">${statuses[i]}</td>
    </tr>`;
  });
  html += '</tbody></table></div>';
  container.innerHTML = html;
}

// ── Transactions ──

let txnFiltersInitialized = false;
let txnSortCol = 'Date';
let txnSortAsc = false; // default: newest first
let currentFilteredTxns = [];

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
    document.getElementById('txn-type-filter').addEventListener('change', renderTransactions);
    document.getElementById('txn-min-amount').addEventListener('input', renderTransactions);
    document.getElementById('txn-max-amount').addEventListener('input', renderTransactions);
    txnFiltersInitialized = true;
  }

  const search = document.getElementById('txn-search').value.toLowerCase();
  const person = document.getElementById('txn-person-filter').value;
  const category = document.getElementById('txn-category-filter').value;
  const txnType = document.getElementById('txn-type-filter').value;
  const minAmt = parseFloat(document.getElementById('txn-min-amount').value);
  const maxAmt = parseFloat(document.getElementById('txn-max-amount').value);

  const filtered = periodFiltered.filter(r => {
    if (person && r.Person !== person) return false;
    if (category && r.Category !== category) return false;
    if (txnType && r.Type !== txnType) return false;
    if (search && !r.Description?.toLowerCase().includes(search)) return false;
    const amt = Math.abs(parseNum(r.Amount));
    if (!isNaN(minAmt) && amt < minAmt) return false;
    if (!isNaN(maxAmt) && amt > maxAmt) return false;
    return true;
  });

  currentFilteredTxns = filtered;
  renderTopCategories(filtered);
  renderTransactionTable(sortTransactions(filtered));
}

function sortTransactions(data) {
  const sorted = [...data];
  sorted.sort((a, b) => {
    let va = a[txnSortCol] || '';
    let vb = b[txnSortCol] || '';
    if (txnSortCol === 'Amount') {
      va = parseFloat(va) || 0;
      vb = parseFloat(vb) || 0;
    }
    if (va < vb) return txnSortAsc ? -1 : 1;
    if (va > vb) return txnSortAsc ? 1 : -1;
    return 0;
  });
  return sorted;
}

function onSortClick(col) {
  if (txnSortCol === col) {
    txnSortAsc = !txnSortAsc;
  } else {
    txnSortCol = col;
    txnSortAsc = col === 'Date' ? false : true;
  }
  renderTransactionTable(sortTransactions(currentFilteredTxns));
}

function renderTopCategories(data) {
  const spending = {};
  data.forEach(r => {
    const amt = parseNum(r.Amount) || 0;
    if (amt < 0) {
      const cat = r.Category || 'Other';
      spending[cat] = (spending[cat] || 0) + Math.abs(amt);
    }
  });

  const top5 = Object.entries(spending)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 5);

  let container = document.getElementById('top-categories-container');
  if (!container) {
    const parent = document.getElementById('transactions-table-container').parentElement;
    container = document.createElement('div');
    container.id = 'top-categories-container';
    container.className = 'top-categories';
    parent.insertBefore(container, document.getElementById('transactions-table-container'));
  }

  if (!top5.length) { container.innerHTML = ''; return; }

  const colors = generateColors(5);
  let html = '<div class="top-cats-row">';
  top5.forEach(([cat, amt], i) => {
    html += `<div class="top-cat-chip" style="border-left: 3px solid ${colors[i]}">
      <span class="top-cat-name">${cat}</span>
      <span class="top-cat-amt">${fmtNum(amt)}</span>
    </div>`;
  });
  html += '</div>';
  container.innerHTML = html;
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
  const cols = ['Date', 'Description', 'Amount', 'Category', 'Person', 'Type'];

  let html = '<div class="table-scroll"><table><thead><tr>';
  cols.forEach(col => {
    const arrow = txnSortCol === col ? (txnSortAsc ? ' ▲' : ' ▼') : '';
    html += `<th class="sortable" data-col="${col}">${col}${arrow}</th>`;
  });
  html += '</tr></thead><tbody>';

  data.forEach(r => {
    const amt = parseNum(r.Amount) || 0;
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

  // Attach sort listeners
  container.querySelectorAll('th.sortable').forEach(th => {
    th.addEventListener('click', () => onSortClick(th.dataset.col));
  });
}

// ── Chart Helpers ──

Chart.defaults.color = '#8a857e';
Chart.defaults.borderColor = '#e8e4de';

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
    '#7c6fae', '#5ba88c', '#d4726a', '#c9a84c', '#6a9ec0',
    '#b08dcc', '#e8918a', '#8bb5a2', '#d4a76a', '#7eb5d6',
    '#a3c47a', '#c08daa', '#e0b86a', '#6aafb5', '#c47a8d',
  ];
  return Array.from({ length: count }, (_, i) => palette[i % palette.length]);
}

function parseNum(val) {
  if (typeof val === 'number') return val;
  if (!val) return 0;
  const cleaned = String(val).replace(/[$,\s]/g, '');
  const n = parseFloat(cleaned);
  return isNaN(n) ? 0 : n;
}

function fmtNum(val) {
  const n = parseNum(val);
  const formatted = '$' + n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return `<span class="money">${formatted}</span>`;
}

// ── Theme Toggle ──

function toggleTheme() {
  const html = document.documentElement;
  const isDark = html.getAttribute('data-theme') === 'dark';
  if (isDark) {
    html.removeAttribute('data-theme');
    localStorage.setItem('theme', 'light');
  } else {
    html.setAttribute('data-theme', 'dark');
    localStorage.setItem('theme', 'dark');
  }
  if (accessToken) applyFilters();
}

// Restore saved theme (default is light)
(function() {
  const saved = localStorage.getItem('theme');
  if (saved === 'dark') {
    document.documentElement.setAttribute('data-theme', 'dark');
  }
})();

// ── Sparklines ──

function renderSparkline(canvasId, values, color) {
  const canvas = document.getElementById(canvasId);
  if (!canvas || !values.length) return;
  const ctx = canvas.getContext('2d');
  const w = canvas.width;
  const h = canvas.height;
  ctx.clearRect(0, 0, w, h);

  const max = Math.max(...values);
  const min = Math.min(...values);
  const range = max - min || 1;
  const step = w / (values.length - 1 || 1);

  ctx.beginPath();
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  values.forEach((v, i) => {
    const x = i * step;
    const y = h - ((v - min) / range) * (h - 4) - 2;
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function renderSparklines() {
  // Savings rate sparkline from monthly data
  const months = rawMonthlyData.map(r => {
    const inc = parseNum(r['Total Income']) || 0;
    const exp = Math.abs(parseNum(r['Total Expenses']) || 0);
    return inc > 0 ? ((inc - exp) / inc * 100) : 0;
  });
  renderSparkline('spark-savings', months.slice(-6), '#7c6fae');

  // Spending sparkline
  const spending = rawMonthlyData.map(r => Math.abs(parseNum(r['Total Expenses']) || 0));
  renderSparkline('spark-spending', spending.slice(-6), '#d4726a');
}

// ── Category vs Last Month ──

function renderCategoryComparison() {
  const container = document.getElementById('category-comparison');
  if (!container) return;

  const sorted = [...rawMonthlyData].sort((a, b) => (a.Month || '').localeCompare(b.Month || ''));
  if (sorted.length < 1) { container.innerHTML = ''; return; }

  // Get last two months from category breakdown
  if (!rawCategoryRows || rawCategoryRows.length < 3) { container.innerHTML = ''; return; }

  const headers = rawCategoryRows[0];
  const categories = headers.slice(1);
  const dataRows = rawCategoryRows.slice(2);

  if (dataRows.length < 1) { container.innerHTML = ''; return; }

  const latest = dataRows[dataRows.length - 1];
  const prev = dataRows.length >= 2 ? dataRows[dataRows.length - 2] : null;

  let html = '';
  categories.forEach((cat, ci) => {
    const curr = parseNum(latest[ci + 1]) || 0;
    if (curr === 0) return;
    const prevAmt = prev ? (parseNum(prev[ci + 1]) || 0) : 0;
    const change = prevAmt > 0 ? ((curr - prevAmt) / prevAmt * 100) : 0;
    const changeStr = prevAmt > 0
      ? `<span style="color:${change > 0 ? 'var(--red)' : 'var(--green)'}">${change > 0 ? '↑' : '↓'} ${Math.abs(change).toFixed(0)}% vs last month</span>`
      : '<span style="color:var(--text-muted)">new</span>';

    html += `<div class="cat-compare-card">
      <div class="cat-compare-name">${cat}</div>
      <div class="cat-compare-amount">${fmtNum(curr)}</div>
      <div class="cat-compare-change">${changeStr}</div>
    </div>`;
  });

  container.innerHTML = html;
}

// ── Spending Pace ──

function renderSpendingPace() {
  const container = document.getElementById('spending-pace');
  if (!container) return;

  const now = new Date();
  const currentMonth = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  const dayOfMonth = now.getDate();
  const daysInMonth = new Date(now.getFullYear(), now.getMonth() + 1, 0).getDate();

  const monthTxns = rawTransactionData.filter(r => (r.Date || '').startsWith(currentMonth));
  const excludeCats = new Set(['Income', 'Taxes', 'Retirement', 'Investment', 'Transfer', 'Healthcare', 'Utilities']);
  const spent = monthTxns.reduce((s, r) => {
    const amt = parseNum(r.Amount);
    const cat = r.Category || 'Other';
    return (amt < 0 && !excludeCats.has(cat)) ? s + Math.abs(amt) : s;
  }, 0);

  if (spent === 0) { container.innerHTML = ''; return; }

  const projected = (spent / dayOfMonth) * daysInMonth;
  const dailyAvg = spent / dayOfMonth;
  const emoji = projected > spent * 1.5 ? '🔴' : projected > spent * 1.2 ? '⚠️' : '✅';

  container.innerHTML = `
    <span class="pace-icon">${emoji}</span>
    <div class="pace-text">
      <strong>This month:</strong> ${fmtNum(spent)} spent in ${dayOfMonth} days
      (${fmtNum(dailyAvg)}/day).
      <span class="pace-projected">Projected: ${fmtNum(projected)} by month end.</span>
    </div>
  `;
}

// ── Budget Progress Bars ──

function renderBudgetProgress() {
  const container = document.getElementById('budget-progress-container');
  if (!container || !rawBudgetData.length) return;

  const excludeCats = new Set(['Income', 'Taxes', 'Retirement', 'Investment', 'Transfer', 'Healthcare', 'Utilities']);
  const prefix = getFilteredMonth();
  const txns = prefix
    ? rawTransactionData.filter(r => (r.Date || '').startsWith(prefix))
    : rawTransactionData;

  const months = new Set();
  txns.forEach(r => { const m = (r.Date || '').substring(0, 7); if (m) months.add(m); });
  const numMonths = months.size || 1;

  const actualSpending = {};
  txns.forEach(r => {
    const amt = parseNum(r.Amount);
    const cat = r.Category || 'Other';
    if (amt < 0 && !excludeCats.has(cat)) {
      actualSpending[cat] = (actualSpending[cat] || 0) + Math.abs(amt);
    }
  });

  const totalMonthsInSheet = rawMonthlyData.length || 1;

  let html = '';
  rawBudgetData.forEach(r => {
    if (excludeCats.has(r.Category)) return;
    const monthlyBudget = parseNum(r['Total Budget']) / totalMonthsInSheet;
    const budget = monthlyBudget * numMonths;
    const actual = actualSpending[r.Category] || 0;
    if (budget <= 0) return;
    const pct = Math.min((actual / budget) * 100, 150);
    const color = pct > 100 ? 'var(--red)' : pct > 80 ? 'var(--yellow)' : 'var(--green)';

    html += `<div class="budget-bar-card">
      <div class="budget-bar-header">
        <span class="budget-bar-name">${r.Category}</span>
        <span class="budget-bar-pct" style="color:${color}">${pct.toFixed(0)}%</span>
      </div>
      <div class="budget-bar-track">
        <div class="budget-bar-fill" style="width:${Math.min(pct, 100)}%;background:${color}"></div>
      </div>
      <div class="budget-bar-amounts">
        <span>${fmtNum(actual)} spent</span>
        <span>${fmtNum(budget)} budget</span>
      </div>
    </div>`;
  });

  container.innerHTML = html;
}

// ── Income Breakdown Donut ──

function renderIncomeBreakdown() {
  const prefix = getFilteredMonth();
  const txns = prefix
    ? rawTransactionData.filter(r => (r.Date || '').startsWith(prefix))
    : rawTransactionData;

  const buckets = { 'Gross Pay': 0, 'Taxes': 0, 'Retirement': 0, 'Benefits': 0, 'Net Take-Home': 0 };
  const incomeCats = new Set(['Income']);
  const taxCats = new Set(['Taxes']);
  const retireCats = new Set(['Retirement']);
  const benefitCats = new Set(['Healthcare', 'Insurance']);

  let totalIncome = 0;
  let totalDeductions = 0;

  txns.forEach(r => {
    const amt = parseNum(r.Amount);
    const cat = r.Category || 'Other';
    if (incomeCats.has(cat) && amt > 0) { buckets['Gross Pay'] += amt; totalIncome += amt; }
    else if (taxCats.has(cat) && amt < 0) { buckets['Taxes'] += Math.abs(amt); totalDeductions += Math.abs(amt); }
    else if (retireCats.has(cat) && amt < 0) { buckets['Retirement'] += Math.abs(amt); totalDeductions += Math.abs(amt); }
    else if (benefitCats.has(cat) && amt < 0 && r['Source File'] === 'Manual Entry') {
      buckets['Benefits'] += Math.abs(amt); totalDeductions += Math.abs(amt);
    }
  });

  buckets['Net Take-Home'] = Math.max(0, totalIncome - totalDeductions);

  const labels = Object.keys(buckets).filter(k => buckets[k] > 0);
  const data = labels.map(k => buckets[k]);
  if (!data.length) return;

  const colors = ['#34d399', '#d4726a', '#7c6fae', '#fbbf24', '#60a5fa'];
  createOrUpdateChart('chart-income-breakdown', 'doughnut', {
    labels,
    datasets: [{ data, backgroundColor: colors.slice(0, labels.length) }],
  }, {
    plugins: { title: { display: true, text: 'Income Breakdown', color: '#e4e6f0' } },
    cutout: '55%',
  });
}

// ── Recurring Transactions ──

function renderRecurring() {
  const container = document.getElementById('recurring-container');
  if (!container) return;

  // Find transactions that appear in 2+ months with similar descriptions
  const byDesc = {};
  rawTransactionData.forEach(r => {
    const amt = parseNum(r.Amount);
    if (amt >= 0) return;
    const desc = (r.Description || '').toLowerCase().replace(/[^a-z ]/g, '').trim();
    const month = (r.Date || '').substring(0, 7);
    if (!desc || !month) return;
    if (!byDesc[desc]) byDesc[desc] = { months: new Set(), amounts: [], display: r.Description };
    byDesc[desc].months.add(month);
    byDesc[desc].amounts.push(Math.abs(amt));
  });

  const recurring = Object.entries(byDesc)
    .filter(([_, v]) => v.months.size >= 2)
    .map(([_, v]) => ({
      name: v.display,
      avgAmt: v.amounts.reduce((a, b) => a + b, 0) / v.amounts.length,
      count: v.months.size,
    }))
    .sort((a, b) => b.avgAmt - a.avgAmt)
    .slice(0, 10);

  if (!recurring.length) { container.innerHTML = ''; return; }

  let html = '<div class="recurring-header">🔄 Recurring Transactions (appears in 2+ months)</div>';
  html += '<div class="recurring-list">';
  recurring.forEach(r => {
    html += `<div class="recurring-chip">
      <span class="recurring-name">${r.name}</span>
      <span class="recurring-amt">~${fmtNum(r.avgAmt)}/mo</span>
    </div>`;
  });
  html += '</div>';
  container.innerHTML = html;
}

// ── Hamburger Menu ──

function initMenu() {
  const menu = document.getElementById('side-menu');
  const overlay = document.getElementById('menu-overlay');

  document.getElementById('btn-menu').addEventListener('click', () => {
    menu.classList.remove('hidden');
    overlay.classList.remove('hidden');
  });

  function closeMenu() {
    menu.classList.add('hidden');
    overlay.classList.add('hidden');
  }

  document.getElementById('btn-menu-close').addEventListener('click', closeMenu);
  overlay.addEventListener('click', closeMenu);

  // Tab items in menu
  document.querySelectorAll('.menu-item[data-tab]').forEach(item => {
    item.addEventListener('click', () => {
      switchTab(item.dataset.tab);
      // Update active state in menu
      document.querySelectorAll('.menu-item[data-tab]').forEach(i => i.classList.remove('active'));
      item.classList.add('active');
      closeMenu();
    });
  });

  // Theme toggle in menu
  document.getElementById('menu-theme').addEventListener('click', () => {
    toggleTheme();
    const isDark = document.documentElement.getAttribute('data-theme') === 'dark';
    document.getElementById('menu-theme').textContent = isDark ? '☀️ Light Mode' : '🌙 Dark Mode';
  });

  // Sign out in menu
  document.getElementById('menu-signout').addEventListener('click', () => {
    closeMenu();
    signOut();
  });

  // Set initial active (home is set in HTML already)
}

function switchTab(tabName) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + tabName).classList.add('active');

  setTimeout(() => {
    Object.values(charts).forEach(c => c.resize());
  }, 50);
}

// ── Privacy Toggle ──

let privacyMode = false;

function togglePrivacy() {
  privacyMode = !privacyMode;
  document.getElementById('dashboard').classList.toggle('privacy-mode', privacyMode);
  document.getElementById('btn-privacy').textContent = privacyMode ? '🔒' : '👁️';
}

// ── Init ──
initAuth();
