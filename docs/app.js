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
let overviewPerson = '';  // '' = all, 'Joint', 'Raman', 'Sahana'

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
  document.getElementById('btn-privacy').addEventListener('click', togglePrivacy);
  document.getElementById('theme-switch').addEventListener('change', (e) => {
    toggleTheme();
    document.querySelector('.toggle-icon').textContent = e.target.checked ? '☀️' : '🌙';
  });
  initMenu();
  document.getElementById('btn-home').addEventListener('click', (e) => {
    e.preventDefault();
    switchTab('home');
    document.querySelectorAll('.menu-item[data-tab]').forEach(i => i.classList.toggle('active', i.dataset.tab === 'home'));
  });
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
  showSkeletons();
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
    rawTransactionData = parseRows(txnRows).filter(r => (r.Date || '') >= '2026-01-01');
    rawMonthlyData = rawMonthlyData.filter(r => (r.Month || '') >= '2026-01');

    // Display last updated
    const metaData = parseRows(metaRows);
    const lastUpdated = metaData.find(r => r.Key === 'Last Updated');
    document.getElementById('last-updated').textContent = lastUpdated ? lastUpdated.Value : 'Unknown';

    populateYearFilter();
    setupFilterListeners();
    applyFilters();

    // Restore tab from URL hash
    const hash = window.location.hash.replace('#', '');
    if (hash && document.getElementById('tab-' + hash)) {
      switchTab(hash);
      document.querySelectorAll('.menu-item[data-tab]').forEach(i => i.classList.toggle('active', i.dataset.tab === hash));
    }
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
  document.getElementById('filter-year').addEventListener('change', () => {
    updateMonthOptions();
    applyFilters();
  });
  document.getElementById('filter-month').addEventListener('change', () => {
    const monthVal = document.getElementById('filter-month').value;
    const yearSelect = document.getElementById('filter-year');
    // Auto-select current year when a month is picked and no year is selected
    if (monthVal && !yearSelect.value) {
      const currentYear = String(new Date().getFullYear());
      if ([...yearSelect.options].some(o => o.value === currentYear)) {
        yearSelect.value = currentYear;
      }
    }
    applyFilters();
  });
}

const MONTH_NAMES = ['', 'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December'];

function updateMonthOptions() {
  const yearVal = document.getElementById('filter-year').value;
  const monthSelect = document.getElementById('filter-month');
  const currentVal = monthSelect.value;

  if (!yearVal) {
    // No year selected — show all 12 months
    monthSelect.innerHTML = '<option value="">All Months</option>';
    for (let i = 1; i <= 12; i++) {
      const val = String(i).padStart(2, '0');
      monthSelect.innerHTML += `<option value="${val}">${MONTH_NAMES[i]}</option>`;
    }
  } else {
    // Find which months have data for this year
    const monthsWithData = new Set();
    rawTransactionData.forEach(r => {
      const d = r.Date || '';
      if (d.startsWith(yearVal)) monthsWithData.add(d.substring(5, 7));
    });
    rawMonthlyData.forEach(r => {
      const m = r.Month || '';
      if (m.startsWith(yearVal)) monthsWithData.add(m.substring(5, 7));
    });

    monthSelect.innerHTML = '<option value="">All Months</option>';
    [...monthsWithData].sort().forEach(mm => {
      const idx = parseInt(mm, 10);
      monthSelect.innerHTML += `<option value="${mm}">${MONTH_NAMES[idx]}</option>`;
    });
  }

  // Restore selection if still valid
  if ([...monthSelect.options].some(o => o.value === currentVal)) {
    monthSelect.value = currentVal;
  } else {
    monthSelect.value = '';
  }
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
  renderCategoryBreakdown();
  renderTrips();
  renderTransactions();
}

// ── KPIs ──

function renderKPIs() {
  const ids = ['kpi-income', 'kpi-spending', 'kpi-saved', 'kpi-savings-rate',
               'kpi-taxes', 'kpi-retirement', 'kpi-healthcare', 'kpi-invested'];

  // Get filtered transactions
  const prefix = getFilteredMonth();
  let txns = prefix
    ? rawTransactionData.filter(r => (r.Date || '').startsWith(prefix))
    : rawTransactionData;

  // Apply person filter
  if (overviewPerson) {
    txns = txns.filter(r => r.Person === overviewPerson);
  }

  if (!txns.length) {
    ids.forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = '—'; });
    return;
  }

  const totalIncome = txns.reduce((s, r) => { const a = parseNum(r.Amount); return a > 0 ? s + a : s; }, 0);

  // Spending = all negative amounts (this is the total outflow)
  const totalOutflow = txns.reduce((s, r) => { const a = parseNum(r.Amount); return a < 0 ? s + Math.abs(a) : s; }, 0);

  // Discretionary spending = outflow minus taxes, retirement, investment (shown separately in breakdown cards)
  const nonSpendingCats = new Set(['Taxes', 'Retirement', 'Investment']);
  const discretionarySpending = txns.reduce((s, r) => {
    const a = parseNum(r.Amount);
    const cat = r.Category || 'Other';
    return (a < 0 && !nonSpendingCats.has(cat)) ? s + Math.abs(a) : s;
  }, 0);

  const netSavings = totalIncome - totalOutflow;
  const savingsRate = (totalIncome > 0 && totalOutflow > 0) ? (netSavings / totalIncome * 100) : 0;

  document.getElementById('kpi-income').innerHTML = `<span class="money">$${totalIncome.toLocaleString(undefined, {maximumFractionDigits: 0})}</span>`;
  document.getElementById('kpi-spending').innerHTML = `<span class="money">$${discretionarySpending.toLocaleString(undefined, {maximumFractionDigits: 0})}</span>`;

  const savedColor = netSavings >= 0 ? 'var(--green)' : 'var(--red)';
  document.getElementById('kpi-saved').innerHTML = `<span class="money" style="color:${savedColor}">$${netSavings.toLocaleString(undefined, {maximumFractionDigits: 0})}</span>`;
  document.getElementById('kpi-savings-rate').textContent = savingsRate.toFixed(1) + '%';

  // Income breakdown
  const breakdownCats = { Taxes: 0, Retirement: 0, Healthcare: 0, Investment: 0 };
  txns.forEach(r => {
    const amt = parseNum(r.Amount);
    const cat = r.Category || '';
    if (amt < 0 && cat in breakdownCats) {
      breakdownCats[cat] += Math.abs(amt);
    }
  });

  document.getElementById('kpi-taxes').innerHTML = `<span class="money">$${breakdownCats.Taxes.toLocaleString(undefined, {maximumFractionDigits: 0})}</span>`;
  document.getElementById('kpi-retirement').innerHTML = `<span class="money">$${breakdownCats.Retirement.toLocaleString(undefined, {maximumFractionDigits: 0})}</span>`;
  document.getElementById('kpi-healthcare').innerHTML = `<span class="money">$${breakdownCats.Healthcare.toLocaleString(undefined, {maximumFractionDigits: 0})}</span>`;
  document.getElementById('kpi-invested').innerHTML = `<span class="money">$${breakdownCats.Investment.toLocaleString(undefined, {maximumFractionDigits: 0})}</span>`;
}

function renderInsights() {
  const container = document.getElementById('insights-list');
  if (!container) return;
  const insights = [];
  let inInsights = false;
  for (const row of rawKpiRows) {
    if (row && row[0] && row[0].includes('Insights')) { inInsights = true; continue; }
    if (inInsights && row && row[0] && row[0].trim()) insights.push(row[0]);
  }

  // Show only the top 3 most relevant insights
  container.innerHTML = insights.slice(0, 3)
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
  const prefix = getFilteredMonth();
  let txns = prefix
    ? rawTransactionData.filter(r => (r.Date || '').startsWith(prefix))
    : rawTransactionData;

  // Apply person filter
  if (overviewPerson) {
    txns = txns.filter(r => r.Person === overviewPerson);
  }

  // Include all spending categories (including taxes, retirement, healthcare)
  const excludeCats = new Set(['Income', 'Transfer', 'Retirement', 'Taxes']);
  const catTotals = {};
  txns.forEach(r => {
    const amt = parseNum(r.Amount);
    const cat = r.Category || 'Other';
    if (amt >= 0 || excludeCats.has(cat)) return;
    catTotals[cat] = (catTotals[cat] || 0) + Math.abs(amt);
  });

  const categories = Object.keys(catTotals).sort((a, b) => catTotals[b] - catTotals[a]);
  if (!categories.length) return;

  const colors = generateColors(categories.length);
  const totals = categories.map(c => catTotals[c]);

  createOrUpdateChart('chart-category-ranking', 'bar', {
    labels: categories,
    datasets: [{ label: 'Total Spent', data: totals, backgroundColor: colors, borderRadius: 4 }],
  }, {
    indexAxis: 'y',
    plugins: { title: { display: true, text: 'Spending by Category', color: '#e4e6f0' }, legend: { display: false } },
  });
}

// ── Budget Status ──

function renderBudgetStatus() {
  if (!rawBudgetData.length) return;

  const excludeCats = new Set(['Income', 'Taxes', 'Retirement', 'Investment', 'Transfer', 'Healthcare', 'Utilities']);

  // Get base monthly budget per category
  const totalMonthsInSheet = rawMonthlyData.length || 1;
  const baseBudgetPerMonth = {};
  rawBudgetData.forEach(r => {
    if (!excludeCats.has(r.Category)) {
      baseBudgetPerMonth[r.Category] = parseNum(r['Total Budget']) / totalMonthsInSheet;
    }
  });

  // Get all months with transactions, sorted
  const allMonths = new Set();
  rawTransactionData.forEach(r => {
    const m = (r.Date || '').substring(0, 7);
    if (m) allMonths.add(m);
  });
  const sortedMonths = [...allMonths].sort();

  // Compute spending per category per month
  const spendingByMonth = {};
  rawTransactionData.forEach(r => {
    const amt = parseNum(r.Amount);
    const cat = r.Category || 'Other';
    const month = (r.Date || '').substring(0, 7);
    if (amt < 0 && !excludeCats.has(cat) && month) {
      if (!spendingByMonth[month]) spendingByMonth[month] = {};
      spendingByMonth[month][cat] = (spendingByMonth[month][cat] || 0) + Math.abs(amt);
    }
  });

  // Compute rolling budget: carry over surplus/deficit from previous months
  const allCats = Object.keys(baseBudgetPerMonth);
  const rollingCarryover = {};  // cat -> accumulated carryover
  allCats.forEach(cat => { rollingCarryover[cat] = 0; });

  // Walk through months in order, accumulating carryover
  sortedMonths.forEach(month => {
    allCats.forEach(cat => {
      const base = baseBudgetPerMonth[cat] || 0;
      const effectiveBudget = base + rollingCarryover[cat];
      const spent = (spendingByMonth[month] || {})[cat] || 0;
      rollingCarryover[cat] = effectiveBudget - spent;  // positive = surplus, negative = deficit
    });
  });

  // Now compute display values based on the filter
  const prefix = getFilteredMonth();
  const filteredTxns = prefix
    ? rawTransactionData.filter(r => (r.Date || '').startsWith(prefix))
    : rawTransactionData;

  const filteredMonths = new Set();
  filteredTxns.forEach(r => { const m = (r.Date || '').substring(0, 7); if (m) filteredMonths.add(m); });
  const numFilteredMonths = filteredMonths.size || 1;

  const actualSpending = {};
  filteredTxns.forEach(r => {
    const amt = parseNum(r.Amount);
    const cat = r.Category || 'Other';
    if (amt < 0 && !excludeCats.has(cat)) {
      actualSpending[cat] = (actualSpending[cat] || 0) + Math.abs(amt);
    }
  });

  // For the selected period, compute rolling budget
  // Walk months up to the filter period to get the starting carryover
  const filteredSorted = [...filteredMonths].sort();
  const monthsBefore = sortedMonths.filter(m => filteredSorted.length > 0 && m < filteredSorted[0]);
  const periodCarryover = {};
  allCats.forEach(cat => { periodCarryover[cat] = 0; });
  monthsBefore.forEach(month => {
    allCats.forEach(cat => {
      const base = baseBudgetPerMonth[cat] || 0;
      const effective = base + periodCarryover[cat];
      const spent = (spendingByMonth[month] || {})[cat] || 0;
      periodCarryover[cat] = effective - spent;
    });
  });

  const categories = [];
  const budgets = [];
  const actuals = [];
  const diffs = [];
  const statuses = [];
  const carryovers = [];

  allCats.sort().forEach(cat => {
    const baseBudget = (baseBudgetPerMonth[cat] || 0) * numFilteredMonths;
    const carryover = periodCarryover[cat] || 0;
    const effectiveBudget = baseBudget + carryover;
    const actual = actualSpending[cat] || 0;
    if (effectiveBudget === 0 && actual === 0) return;

    categories.push(cat);
    budgets.push(effectiveBudget);
    actuals.push(actual);
    diffs.push(effectiveBudget - actual);
    statuses.push(effectiveBudget - actual >= 0 ? 'Under Budget' : 'Over Budget');
    carryovers.push(carryover);
  });

  if (!categories.length) return;

  const container = document.getElementById('budget-table-container');
  let html = '<div class="table-scroll"><table><thead><tr>';
  html += '<th>Category</th><th>Base Budget</th><th>Rollover</th><th>Effective Budget</th><th>Actual</th><th>Remaining</th><th>Status</th>';
  html += '</tr></thead><tbody>';
  categories.forEach((cat, i) => {
    const base = (baseBudgetPerMonth[cat] || 0) * numFilteredMonths;
    const co = carryovers[i];
    const statusCls = statuses[i] === 'Under Budget' ? 'status-under' : 'status-over';
    const coCls = co >= 0 ? 'amount-positive' : 'amount-negative';
    html += `<tr>
      <td>${cat}</td>
      <td>${fmtNum(base)}</td>
      <td class="${coCls}">${fmtNum(co)}</td>
      <td>${fmtNum(budgets[i])}</td>
      <td>${fmtNum(actuals[i])}</td>
      <td class="${diffs[i] >= 0 ? 'amount-positive' : 'amount-negative'}">${fmtNum(diffs[i])}</td>
      <td class="${statusCls}">${statuses[i]}</td>
    </tr>`;
  });
  html += '</tbody></table></div>';
  container.innerHTML = html;
}

// ── Trips ──

function renderTrips() {
  const container = document.getElementById('trips-container');
  if (!container) return;

  // Group transactions by Trip column
  const trips = {};
  rawTransactionData.forEach(r => {
    const trip = (r.Trip || '').trim();
    if (!trip) return;
    if (!trips[trip]) trips[trip] = [];
    trips[trip].push(r);
  });

  const tripNames = Object.keys(trips).sort();
  if (!tripNames.length) {
    container.innerHTML = '<p style="color:var(--text-muted);text-align:center;padding:2rem">No trips tagged yet. Add a trip name in the "Trip" column of the Transactions tab in your Google Sheet.</p>';
    return;
  }

  let html = '';
  tripNames.forEach(name => {
    const txns = trips[name];
    const total = txns.reduce((s, r) => s + Math.abs(parseNum(r.Amount)), 0);
    const dates = txns.map(r => r.Date).filter(Boolean).sort();
    const dateRange = dates.length ? `${dates[0]} — ${dates[dates.length - 1]}` : '';

    // Category breakdown
    const cats = {};
    txns.forEach(r => {
      const amt = parseNum(r.Amount);
      if (amt < 0) {
        const cat = r.Category || 'Other';
        cats[cat] = (cats[cat] || 0) + Math.abs(amt);
      }
    });
    const sortedCats = Object.entries(cats).sort((a, b) => b[1] - a[1]);

    html += `<div class="trip-card">
      <div class="trip-header">
        <div>
          <div class="trip-name">${name}</div>
          <div class="trip-dates">${dateRange} · ${txns.length} transactions</div>
        </div>
        <div class="trip-total">${fmtNum(total)}</div>
      </div>
      <div class="trip-categories">
        ${sortedCats.map(([cat, amt]) => `<span class="trip-cat">${cat}: ${fmtNum(amt)}</span>`).join('')}
      </div>
    </div>`;
  });

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
    txnFiltersInitialized = true;
  }

  const search = document.getElementById('txn-search').value.toLowerCase();
  const person = document.getElementById('txn-person-filter').value;
  const category = document.getElementById('txn-category-filter').value;
  const txnType = document.getElementById('txn-type-filter').value;

  const filtered = periodFiltered.filter(r => {
    if (person && r.Person !== person) return false;
    if (category && r.Category !== category) return false;
    if (txnType && r.Type !== txnType) return false;
    if (search && !r.Description?.toLowerCase().includes(search)) return false;
    return true;
  });

  currentFilteredTxns = filtered;
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

// ── Animated Counter ──

function animateCounter(elementId, targetValue, suffix = '', isMoney = false) {
  const el = document.getElementById(elementId);
  if (!el) return;

  const duration = 600;
  const startTime = performance.now();
  const startValue = 0;

  function update(currentTime) {
    const elapsed = currentTime - startTime;
    const progress = Math.min(elapsed / duration, 1);
    // Ease out cubic
    const eased = 1 - Math.pow(1 - progress, 3);
    const current = startValue + (targetValue - startValue) * eased;

    if (isMoney) {
      el.innerHTML = `<span class="money">$${Math.round(current).toLocaleString()}</span>`;
    } else {
      el.textContent = current.toFixed(1) + suffix;
    }

    if (progress < 1) {
      requestAnimationFrame(update);
    }
  }

  requestAnimationFrame(update);
}

// ── Skeleton Loading ──

function showSkeletons() {
  ['kpi-savings-rate', 'kpi-total-budget', 'kpi-total-spending', 'kpi-budget-health'].forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.innerHTML = '<span class="skeleton" style="display:inline-block;width:80px;height:1.4em">&nbsp;</span>'; }
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
    options: { responsive: true, maintainAspectRatio: false, ...extraOpts },
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
    const sw = document.getElementById('theme-switch');
    if (sw) sw.checked = true;
    const icon = document.querySelector('.toggle-icon');
    if (icon) icon.textContent = '☀️';
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

  container.innerHTML = `
    <div class="pace-text">
      <strong>This month so far:</strong> ${fmtNum(spent)} spent over ${dayOfMonth} days
      (about ${fmtNum(dailyAvg)}/day).
      On this pace, it'd be around ${fmtNum(projected)} by month end.
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

  let html = '<div class="recurring-header">Recurring charges (seen in 2+ months)</div>';
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

// ── Quick Month Navigation ──

function initQuickNav() {
  document.getElementById('btn-prev-month').addEventListener('click', () => navigateMonth(-1));
  document.getElementById('btn-next-month').addEventListener('click', () => navigateMonth(1));
}

function navigateMonth(delta) {
  const yearSelect = document.getElementById('filter-year');
  const monthSelect = document.getElementById('filter-month');

  let year = parseInt(yearSelect.value) || new Date().getFullYear();
  let month = parseInt(monthSelect.value) || new Date().getMonth() + 1;

  month += delta;
  if (month > 12) { month = 1; year++; }
  if (month < 1) { month = 12; year--; }

  const yearStr = String(year);
  const monthStr = String(month).padStart(2, '0');

  if ([...yearSelect.options].some(o => o.value === yearStr)) {
    yearSelect.value = yearStr;
  }
  updateMonthOptions();
  if ([...monthSelect.options].some(o => o.value === monthStr)) {
    monthSelect.value = monthStr;
  }
  applyFilters();
}

function renderPeriodLabel() {
  const year = document.getElementById('filter-year').value;
  const month = document.getElementById('filter-month').value;
  const label = document.getElementById('current-period-label');
  if (!label) return;

  if (year && month) {
    const monthName = MONTH_NAMES[parseInt(month, 10)] || month;
    label.textContent = `${monthName} ${year}`;
  } else if (year) {
    label.textContent = `Year ${year}`;
  } else {
    label.textContent = 'All Time';
  }
}

// ── Data Summary & Uncategorized Badge ──

function renderDataSummary() {
  const container = document.getElementById('data-summary');
  if (!container) return;

  const prefix = getFilteredMonth();
  const txns = prefix
    ? rawTransactionData.filter(r => (r.Date || '').startsWith(prefix))
    : rawTransactionData;

  const months = new Set();
  txns.forEach(r => { const m = (r.Date || '').substring(0, 7); if (m) months.add(m); });

  const uncategorized = txns.filter(r => r.Category === 'Other').length;

  let html = `${txns.length} transactions across ${months.size} month${months.size !== 1 ? 's' : ''}`;
  if (uncategorized > 0) {
    html += `<span class="badge" onclick="switchTab('transactions');document.getElementById('txn-category-filter').value='Other';renderTransactions();">${uncategorized} uncategorized</span>`;
  }
  container.innerHTML = html;
}

// ── Duplicate Detection ──

function renderDuplicateWarning() {
  const container = document.getElementById('duplicate-warning');
  if (!container) return;

  const prefix = getFilteredMonth();
  const txns = prefix
    ? rawTransactionData.filter(r => (r.Date || '').startsWith(prefix))
    : rawTransactionData;

  // Find potential duplicates: same date, same absolute amount, similar description
  const seen = {};
  const dupes = [];
  txns.forEach(r => {
    const date = r.Date || '';
    const amt = Math.abs(parseNum(r.Amount)).toFixed(2);
    const desc = (r.Description || '').toLowerCase().substring(0, 20);
    const key = `${date}|${amt}|${desc}`;
    if (seen[key]) {
      dupes.push({ a: seen[key], b: r });
    } else {
      seen[key] = r;
    }
  });

  if (!dupes.length) {
    container.innerHTML = '';
    return;
  }

  let html = `<div class="duplicate-warning">Possible duplicates found (${dupes.length}):`;
  dupes.slice(0, 5).forEach(d => {
    html += `<br>• ${d.a.Date} — ${d.a.Description} — ${fmtNum(d.a.Amount)} (${d.a['Source File']} vs ${d.b['Source File']})`;
  });
  if (dupes.length > 5) html += `<br>...and ${dupes.length - 5} more`;
  html += '</div>';
  container.innerHTML = html;
}

// ── Hamburger Menu ──

function initMenu() {
  const menu = document.getElementById('side-menu');
  const overlay = document.getElementById('menu-overlay');

  document.getElementById('btn-menu').addEventListener('click', () => {
    menu.classList.add('open');
    overlay.classList.add('open');
  });

  function closeMenu() {
    menu.classList.remove('open');
    overlay.classList.remove('open');
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

  // Sign out in menu
  document.getElementById('menu-signout').addEventListener('click', () => {
    closeMenu();
    signOut();
  });

  // Set initial active (home is set in HTML already)

  // Person filter buttons
  document.querySelectorAll('.person-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      document.querySelectorAll('.person-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      overviewPerson = btn.dataset.person;
      renderKPIs();
      renderCategoryBreakdown();
    });
  });
}

function switchTab(tabName) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.getElementById('tab-' + tabName).classList.add('active');
  window.location.hash = tabName;

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
