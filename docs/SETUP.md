# Finance Tracker Dashboard — Setup Guide

## How It Works

```
Your laptop (CLI)  →  Google Sheets  →  GitHub Pages (dashboard)
```

- The Python CLI runs on your laptop to parse statements and write to Google Sheets
- The GitHub Pages dashboard reads from that same Sheet via Google's API
- Access is restricted to Google accounts you whitelist

## Step-by-Step Setup

### 1. Create a Web OAuth Client

You need a **separate** OAuth client for the web dashboard (your existing `credentials.json` is for the desktop CLI).

1. Go to [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
2. Use the same project as your CLI (`extreme-core-492201-e6`)
3. Click **Create Credentials → OAuth client ID**
4. Application type: **Web application**
5. Name: `Finance Dashboard`
6. Authorized JavaScript origins: `https://YOUR_USERNAME.github.io`
7. Authorized redirect URIs: `https://YOUR_USERNAME.github.io`
8. Click **Create**
9. Copy the **Client ID** (you do NOT need the client secret)

### 2. Restrict Access to Your Accounts

1. Go to [OAuth consent screen](https://console.cloud.google.com/apis/credentials/consent)
2. Keep the app in **Testing** mode (do NOT publish it)
3. Under **Test users**, add:
   - Your Google email
   - Your wife's Google email
4. Only these accounts can sign in. Anyone else gets blocked by Google.

### 3. Enable the Sheets API

1. Go to [APIs & Services → Library](https://console.cloud.google.com/apis/library)
2. Search for **Google Sheets API**
3. Make sure it's **Enabled** (it probably already is from the CLI setup)

### 4. Share the Google Sheet

1. Open your Google Sheet
2. Click **Share**
3. Add your wife's Google email as a **Viewer**

### 5. Configure the Dashboard

Edit `docs/config.js`:

```js
const CONFIG = {
  GOOGLE_CLIENT_ID: 'paste-your-web-client-id-here.apps.googleusercontent.com',
  SPREADSHEET_ID: 'your-sheet-id-from-the-url',
  // ... rest stays the same
};
```

### 6. Deploy to GitHub Pages

1. Push this repo to GitHub
2. Go to **Settings → Pages**
3. Source: **Deploy from a branch**
4. Branch: `main`, folder: `/docs`
5. Save — your site will be at `https://YOUR_USERNAME.github.io/REPO_NAME/`

### 7. Update Authorized Origins

If your GitHub Pages URL includes the repo name (e.g., `https://user.github.io/finance-tracker/`), go back to the OAuth client settings and make sure the origin matches:
- `https://user.github.io` (the origin, without the path)

## Security Checklist

- [ ] `credentials.json` and `token.json` are in `.gitignore` (never committed)
- [ ] Web OAuth client has NO client secret in frontend code
- [ ] OAuth consent screen is in **Testing** mode with only your emails
- [ ] Google Sheet is shared only with your accounts
- [ ] The dashboard requests **read-only** scope (`spreadsheets.readonly`)

## Daily Usage

1. Upload new bank statements to your Google Drive folder
2. Run the CLI on your laptop: `cd finance-tracker && python main.py`
3. Open the dashboard on any device — data is already there
