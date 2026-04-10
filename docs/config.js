/**
 * Dashboard Configuration
 *
 * SETUP INSTRUCTIONS:
 * 1. Go to https://console.cloud.google.com/apis/credentials
 * 2. Create a NEW OAuth 2.0 Client ID with type "Web application"
 *    - Do NOT reuse your desktop credentials.json client
 * 3. Add your GitHub Pages URL to "Authorized JavaScript origins":
 *    e.g. https://yourusername.github.io
 * 4. Add it to "Authorized redirect URIs" as well
 * 5. Copy the Client ID below (NOT the client secret — you don't need it)
 * 6. In Google Cloud Console > OAuth consent screen, keep the app in
 *    "Testing" mode and add your and your wife's emails as test users.
 *    This restricts sign-in to only those accounts.
 * 7. Make sure the Google Sheets API is enabled in your project.
 * 8. Share your Google Sheet with your wife's Google account (Viewer).
 *
 * SECURITY NOTES:
 * - The Client ID below is NOT a secret. It's safe in frontend code.
 * - Access is controlled by Google's OAuth consent screen (test users).
 * - The Sheet must be shared with the signed-in user's account.
 * - No tokens or secrets are stored in this code.
 */

const CONFIG = {
  // Replace with your Web application OAuth Client ID
  GOOGLE_CLIENT_ID: '482681209964-3mkqgfdkdn8fnvqhnskkiet3cshlu58o.apps.googleusercontent.com',

  // Your Google Sheet ID (from the URL: docs.google.com/spreadsheets/d/THIS_PART/edit)
  SPREADSHEET_ID: '1b2tOJBzgu9xpd7Dvy38NDqAyKKi9cyEl1b0cJdpkz1Q',

  // Scopes: read-only access to Sheets
  SCOPES: 'https://www.googleapis.com/auth/spreadsheets.readonly',

  // Tab names (must match what the Python CLI writes)
  TABS: {
    TRANSACTIONS: 'Transactions',
    MONTHLY_SUMMARY: 'Monthly Summary',
    ANNUAL_SUMMARY: 'Annual Summary',
    CATEGORY_BREAKDOWN: 'Category Breakdown',
    KPIS: 'KPIs',
    BUDGET_STATUS: 'Budget Status',
    METADATA: 'Metadata',
  },
};
