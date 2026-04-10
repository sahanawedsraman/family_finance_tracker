# Family Finance Tracker

A personal finance dashboard that pulls bank/credit card statements from Google Drive, parses transactions using Gemini AI, and displays everything on a GitHub Pages dashboard.

## How It Works

```
Upload statements to Google Drive
        ↓
Run CLI on your laptop (python main.py)
        ↓
Gemini AI parses & categorizes transactions
        ↓
Data written to Google Sheets (database)
        ↓
GitHub Pages dashboard reads from Sheet
        ↓
View on any device — phone, laptop, tablet
```

## Features

- **AI-powered parsing** — Gemini extracts transactions from PDFs and CSVs, categorizes them automatically
- **Smart deduplication** — re-running the CLI won't create duplicate transactions
- **Manual category editing** — fix categories directly in the Google Sheet, changes persist across runs
- **Manual entry** — enter pay stub details (salary, taxes, 401k, benefits) when PDFs can't be parsed
- **Privacy mode** — blur all dollar amounts with one click, keep percentages and insights visible
- **Dark/light theme** — toggle in the menu
- **Mobile friendly** — hamburger menu, responsive layout
- **Budget tracking** — set monthly budgets per category, see progress bars and over/under status
- **Financial insights** — automated recommendations based on spending patterns
- **Recurring detection** — flags subscriptions and recurring charges
- **Year/month filtering** — all views update dynamically when you change the time period

## Project Structure

```
├── finance-tracker/          # Python CLI
│   ├── main.py               # Entry point
│   ├── config.yaml            # Configuration (budgets, categories, API keys)
│   ├── src/
│   │   ├── auth.py            # Google OAuth
│   │   ├── drive.py           # Google Drive file scanner
│   │   ├── parser.py          # CSV/Excel/PDF parsing
│   │   ├── llm_parser.py      # Gemini AI parsing & categorization
│   │   ├── categorizer.py     # Keyword-based fallback categorizer
│   │   ├── metrics.py         # Monthly/annual metrics & KPIs
│   │   ├── sheets.py          # Google Sheets data writer
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
