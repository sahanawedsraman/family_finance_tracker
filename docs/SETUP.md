# Dashboard Setup Guide

## Architecture

```
Your laptop (CLI)  →  Google Sheets (database)  →  GitHub Pages (dashboard)
```

- CLI runs locally, parses CSVs, writes to Google Sheets
- Dashboard is a static site that reads from the Sheet via Google's API
- Access controlled by Google OAuth (only whitelisted accounts can sign in)

## One-Time Setup

### 1. Google Cloud Project

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
2. Create a new project (or use existing)
3. Enable **Google Drive API** and **Google Sheets API**

### 2. CLI Credentials (Desktop OAuth)

1. Go to Credentials → Create Credentials → OAuth client ID
2. Type: **Desktop application**
3. Download JSON → save as `finance-tracker/credentials.json`

### 3. Dashboard Credentials (Web OAuth)

1. Create another OAuth client ID
2. Type: **Web application**
3. Authorized JavaScript origins: `https://YOUR_USERNAME.github.io`
4. Authorized redirect URIs: `https://YOUR_USERNAME.github.io`
5. Copy the Client ID into `docs/config.js`

### 4. Restrict Access

1. Go to OAuth consent screen
2. Keep in **Testing** mode
3. Add your email and your partner's email as test users

### 5. Google Drive Setup

1. Create a folder for statements
2. Create subfolders for each person (e.g., Raman/, Sahana/, Joint/)
3. Copy the folder ID from the URL into `config.yaml`

### 6. Configuration

Copy `config.yaml.example` to `config.yaml`:
- Set `drive_folder_id` to your Drive folder
- Set `sheet_id` to `null` (auto-creates on first run)
- Configure persons, categories, and budgets

### 7. First Run

```bash
cd finance-tracker
pip install -r requirements.txt
python main.py
```

This will open a browser for Google sign-in, create the Sheet, and process any files.

### 8. Deploy Dashboard

1. Push to GitHub
2. Settings → Pages → Branch: your branch, folder: `/docs`
3. Share the Google Sheet with your partner (Viewer)

## Config Files

- `finance-tracker/config.yaml` — CLI config (categories, budgets, persons)
- `finance-tracker/credentials.json` — Desktop OAuth (never commit)
- `docs/config.js` — Dashboard config (Web OAuth client ID, Sheet ID)

## Security

- `credentials.json` and `token.json` are in `.gitignore`
- Web OAuth client ID is safe in frontend code (not a secret)
- OAuth consent screen in Testing mode = only whitelisted emails
- Dashboard uses read-only Sheets scope
- Google Sheet shared only with your accounts
