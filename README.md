# Family Finance Tracker

A personal finance dashboard for families. Upload bank and credit card statements to Google Drive, run a CLI to parse and categorize transactions, and view everything on a GitHub Pages dashboard from any device.

## How It Works

```
Upload CSVs to Google Drive
        ↓
Run CLI on your laptop (python main.py)
        ↓
Transactions parsed, categorized, deduplicated
        ↓
Data written to Google Sheets (database)
        ↓
GitHub Pages dashboard reads from Sheet
        ↓
View on any device — phone, laptop, tablet
```

## Features

- **CSV parsing** — auto-detects column formats from Chase, BofA, Discover, and other banks
- **Keyword categorization** — configurable keyword-to-category mapping in config.yaml
- **Smart deduplication** — re-running the CLI won't create duplicate transactions
- **Manual category editing** — fix categories directly in the Google Sheet, changes persist across runs
- **Manual entry** — enter pay stub details (salary, taxes, 401k, benefits) in the Sheet
- **Person filtering** — view All, Raman, or Sahana's transactions separately
- **Trip tracking** — tag transactions with trip names, see grouped spending with reimbursements
- **Privacy mode** — blur all dollar amounts with one click
- **Dark/light theme** — toggle switch in the header
- **Year/month filtering** — all views update dynamically
- **Discover sign flip** — handles Discover's inverted amount convention automatically
- **Google Drive shortcuts** — follows folder shortcuts during scanning

## Dashboard Views

- **Overview** — Income, Spending, Saved, Savings Rate + Taxes/Retirement/Healthcare/Invested breakdown + spending by category chart
- **Monthly** — Income vs Expenses chart, Savings Trend chart, monthly table
- **Trips** — Trip cards with net cost, reimbursements, and category breakdown
- **Transactions** — Searchable, sortable, filterable transaction list

## Project Structure

```
├── finance-tracker/           # Python CLI
│   ├── main.py                # Entry point (--dry-run, --learn-categories)
│   ├── config.yaml            # Configuration (budgets, categories, persons)
│   ├── src/
│   │   ├── auth.py            # Google OAuth
│   │   ├── drive.py           # Google Drive scanner (recursive, follows shortcuts)
│   │   ├── parser.py          # CSV/Excel/PDF parsing with header detection
│   │   ├── categorizer.py     # Keyword-based categorizer
│   │   ├── metrics.py         # Monthly/annual metrics & insights
│   │   ├── sheets.py          # Google Sheets data store
│   │   ├── config.py          # Config loader
│   │   └── models.py          # Data models
│   └── tests/                 # Test suite
├── docs/                      # GitHub Pages dashboard
│   ├── index.html
│   ├── app.js
│   ├── style.css
│   ├── config.js              # Dashboard configuration
│   └── logo.png
├── .gitignore
└── README.md
```

## Setup

See [docs/SETUP.md](docs/SETUP.md) for full setup instructions.

Quick start:
1. `pip install -r finance-tracker/requirements.txt`
2. Set up Google Cloud project with Drive + Sheets APIs
3. Create `credentials.json` (Desktop OAuth client)
4. Copy `config.yaml.example` to `config.yaml` and fill in your details
5. Run `python main.py`

## Monthly Workflow

1. Download statements from your banks (CSV format)
2. Upload to the correct person's folder in Google Drive
3. Add pay stub info in the Manual Entry tab (if needed)
4. Run `cd finance-tracker && python main.py`
5. Review transactions in the Sheet, fix any "Other" categories
6. Open the dashboard to see your finances

## CLI Options

- `python main.py` — full pipeline
- `python main.py --dry-run` — preview without writing to Sheets
- `python main.py --learn-categories` — suggest new keywords from recategorized transactions
