# Finance Tracker

Automatically pulls bank and credit card statements from Google Drive, parses them, categorizes transactions, computes financial metrics, and writes everything to a Google Sheet with charts.

## How It Works

1. Scans a Google Drive folder (recursively) for statement files (CSV, Excel, PDF)
2. Skips files that were already processed in previous runs
3. Parses transactions from each file, detecting columns automatically
4. Assigns each file to a person based on filename/path pattern matching
5. Categorizes transactions using keyword matching from your config
6. Computes monthly metrics, KPIs, and budget utilization
7. Writes results to a Google Sheet with 5 tabs: Transactions, Monthly Summary, Category Breakdown, KPIs, and Budget Status

## Prerequisites

- Python 3.11+
- A Google Cloud project with the Drive and Sheets APIs enabled
- OAuth 2.0 credentials (`credentials.json`) downloaded from the Google Cloud Console

## Setup

```bash
cd finance-tracker
pip install -r requirements.txt
```

Place your `credentials.json` file in the `finance-tracker/` directory.

## Configuration

Create a `config.yaml` file:

```yaml
drive_folder_id: "your-google-drive-folder-id"
sheet_id: null          # null = create new sheet, or set an existing spreadsheet ID
sheet_name: "Finance Tracker"
log_level: "INFO"

persons:
  - name: Alice
    patterns: ["alice", "checking-alice"]
  - name: Bob
    patterns: ["bob", "visa-bob"]

categories:
  Groceries: ["walmart", "costco", "trader joe"]
  Dining: ["restaurant", "starbucks", "doordash"]
  Utilities: ["electric", "water", "internet"]
  Transport: ["uber", "lyft", "gas station"]

budgets:
  Groceries: 600
  Dining: 200
  Utilities: 150
  Transport: 100
```

### Config Fields

| Field | Required | Description |
|---|---|---|
| `drive_folder_id` | Yes | Google Drive folder ID containing your statements |
| `sheet_id` | No | Existing spreadsheet ID to update, or `null` to create a new one |
| `sheet_name` | No | Name for the spreadsheet (default: "Finance Tracker") |
| `persons` | Yes | List of people to track, each with name and filename patterns |
| `categories` | Yes | Mapping of category names to keyword lists for matching |
| `budgets` | No | Monthly budget amounts per category |
| `log_level` | No | Logging level: DEBUG, INFO, WARNING, ERROR (default: INFO) |

## Usage

```bash
python main.py
```

Or with a custom config path:

```bash
python main.py --config path/to/config.yaml
```

On first run with `sheet_id: null`, the app creates a new Google Sheet and logs the spreadsheet ID. Add that ID to your config so future runs update the same sheet instead of creating new ones.

## Sheet Behavior

- **`sheet_id: null`** — Creates a new spreadsheet every run
- **`sheet_id: "abc123..."`** — Clears and rewrites all tabs in the existing sheet each run

The data is fully rewritten on each run (not appended), so the sheet always reflects the complete current state.

## File Processing State

Processed file IDs are tracked in `processed_files.json`. On each run, only new files from Drive are downloaded and parsed. If you want to reprocess everything, delete this file.

State is only saved after a successful write to Google Sheets, so a failed run won't mark files as processed.

## Output Tabs

| Tab | Contents |
|---|---|
| Transactions | Every parsed transaction with date, description, amount, category, person |
| Monthly Summary | Per-person and combined monthly income, expenses, savings rate |
| Category Breakdown | Per-category spending by month with budget comparison |
| KPIs | Top spending categories, month-over-month trends, overall savings rate |
| Budget Status | Budget vs actual per category with over/under status |

Each tab includes embedded charts for visualization.

## Running Tests

```bash
python -m pytest tests/ -v
```
