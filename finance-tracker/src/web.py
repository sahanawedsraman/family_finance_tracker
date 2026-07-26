"""Local web UI for the Finance Tracker monthly update workflow."""

import io
import logging
import os
import secrets
import socket
import subprocess
import sys
import threading
import webbrowser
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import Flask, jsonify, render_template, request, session

from src.auth import build_drive_service, build_sheets_service, get_credentials
from src.config import load_config
from src.drive import upload_file
from src.retry import retry_api_call
from src.sheets import (
    ALL_CATEGORIES,
    BUDGET_EXCLUDED_CATEGORIES,
    TAB_TRANSACTIONS,
    append_manual_entries,
    read_existing_transactions,
    update_transaction_category,
)

logger = logging.getLogger(__name__)

ALLOWED_MIME_TYPES = {
    "text/csv",
    "application/csv",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
}

ALLOWED_EXTENSIONS = {".csv", ".pdf", ".xlsx", ".xls"}

MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def create_app(config_path: str) -> Flask:
    """Create and configure the Flask app."""
    template_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
    app = Flask(__name__, template_folder=template_dir)
    app.secret_key = secrets.token_hex(32)
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

    config = load_config(config_path)
    creds = get_credentials()
    drive_service = build_drive_service(creds)
    sheets_service = build_sheets_service(creds)

    @app.before_request
    def csrf_check():
        if request.method in ("POST", "PUT", "DELETE"):
            # Reject cross-origin requests
            origin = request.headers.get("Origin", "")
            if origin and not origin.startswith("http://127.0.0.1"):
                return jsonify({"error": "Request validation failed"}), 403

            content_type = request.content_type or ""
            if "application/json" in content_type:
                return
            # Multipart/form uploads need a CSRF token
            token = request.form.get("_csrf") or request.headers.get("X-CSRF-Token")
            if token != session.get("csrf_token"):
                return jsonify({"error": "Request validation failed"}), 403

    @app.route("/api/csrf-token")
    def api_csrf_token():
        if "csrf_token" not in session:
            session["csrf_token"] = secrets.token_hex(16)
        return jsonify({"token": session["csrf_token"]})

    @app.after_request
    def security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:"
        )
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/config")
    def api_config():
        persons = [p.name for p in config.persons]
        return jsonify({"persons": persons, "categories": ALL_CATEGORIES})

    @app.route("/api/uncategorized")
    def api_uncategorized():
        txns = read_existing_transactions(sheets_service, config.sheet_id)
        uncategorized = []
        for i, t in enumerate(txns):
            if t.get("Category") == "Other":
                uncategorized.append({
                    "row": i + 2,  # 1-indexed, skip header
                    "date": t.get("Date", ""),
                    "description": t.get("Description", ""),
                    "amount": t.get("Amount", ""),
                    "person": t.get("Person", ""),
                })
        return jsonify({"transactions": uncategorized, "total": len(uncategorized)})

    @app.route("/api/paystub", methods=["POST"])
    def api_paystub():
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        person = data.get("person", "")
        pay_date = data.get("date", "")
        gross = data.get("gross", 0)
        federal = data.get("federal", 0)
        state = data.get("state", 0)
        fica = data.get("fica", 0)
        retirement = data.get("retirement", 0)
        health = data.get("health", 0)
        other = data.get("other", 0)

        valid_persons = [p.name for p in config.persons]
        if person not in valid_persons:
            return jsonify({"error": f"Invalid person. Must be one of: {valid_persons}"}), 400

        if not pay_date:
            return jsonify({"error": "Date is required"}), 400

        try:
            for val in [gross, federal, state, fica, retirement, health, other]:
                float(val)
        except (ValueError, TypeError):
            return jsonify({"error": "All amounts must be numbers"}), 400

        gross = float(gross)
        federal = float(federal)
        state = float(state)
        fica = float(fica)
        retirement = float(retirement)
        health = float(health)
        other = float(other)

        entries = [
            [pay_date, "Gross Pay", str(gross), "Gross Pay", person],
            [pay_date, "Federal Tax", str(-federal), "Taxes", person],
            [pay_date, "State Tax", str(-state), "Taxes", person],
            [pay_date, "FICA", str(-fica), "Taxes", person],
            [pay_date, "401k", str(-retirement), "Retirement", person],
            [pay_date, "Health Insurance", str(-health), "Benefits", person],
        ]
        if other > 0:
            entries.append([pay_date, "Other Deductions", str(-other), "Benefits", person])

        append_manual_entries(sheets_service, config.sheet_id, entries)
        net = gross - federal - state - fica - retirement - health - other
        return jsonify({"success": True, "entries": len(entries), "net_takehome": net})

    @app.route("/api/upload", methods=["POST"])
    def api_upload():
        if "file" not in request.files:
            return jsonify({"error": "No file provided"}), 400

        file = request.files["file"]
        person = request.form.get("person", "")

        if not file.filename:
            return jsonify({"error": "No file selected"}), 400

        valid_persons = [p.name for p in config.persons]
        if person not in valid_persons:
            return jsonify({"error": f"Invalid person. Must be one of: {valid_persons}"}), 400

        # Validate file extension
        _, ext = os.path.splitext(file.filename)
        if ext.lower() not in ALLOWED_EXTENSIONS:
            return jsonify({"error": f"Unsupported file type: {ext}. Allowed: {', '.join(ALLOWED_EXTENSIONS)}"}), 400

        # Read file content
        file_bytes = file.read()
        if len(file_bytes) == 0:
            return jsonify({"error": "File is empty"}), 400

        # Determine MIME type from extension
        mime_map = {".csv": "text/csv", ".pdf": "application/pdf", ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xls": "application/vnd.ms-excel"}
        mime_type = mime_map.get(ext.lower(), "application/octet-stream")

        # Sanitize filename — keep only safe characters
        safe_name = "".join(c for c in file.filename if c.isalnum() or c in "._- ").strip()
        if not safe_name:
            safe_name = f"statement{ext}"

        file_id = upload_file(drive_service, config.drive_folder_id, person, safe_name, file_bytes, mime_type)
        return jsonify({"success": True, "file_id": file_id, "filename": safe_name, "person": person})

    @app.route("/api/categorize", methods=["POST"])
    def api_categorize():
        data = request.get_json()
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        row = data.get("row")
        category = data.get("category", "")

        if not isinstance(row, int) or row < 2:
            return jsonify({"error": "Invalid row number"}), 400

        if category not in ALL_CATEGORIES:
            return jsonify({"error": f"Invalid category. Must be one of: {ALL_CATEGORIES}"}), 400

        update_transaction_category(sheets_service, config.sheet_id, row, category)
        return jsonify({"success": True, "row": row, "category": category})

    @app.route("/api/run-pipeline", methods=["POST"])
    def api_run_pipeline():
        from main import run_pipeline as _run_pipeline

        try:
            summary = _run_pipeline(config_path, dry_run=False, show_report=False)
            result = {
                "success": True,
                "files_processed": summary.files_processed,
                "files_skipped": summary.files_skipped,
                "transactions_found": summary.transactions_found,
                "errors": summary.errors or [],
            }
        except Exception as e:
            logger.error("Pipeline failed: %s", e, exc_info=True)
            result = {"success": False, "error": "Pipeline execution failed. Check server logs for details."}

        return jsonify(result)

    @app.route("/api/dashboard")
    def api_dashboard():
        """Return financial summary data for the dashboard view."""
        txns = read_existing_transactions(sheets_service, config.sheet_id)
        if not txns:
            return jsonify({"empty": True})

        today = date.today()
        current_month = today.strftime("%Y-%m")

        # Parse all transactions
        monthly_data = defaultdict(lambda: {"income": 0.0, "expenses": 0.0, "categories": defaultdict(float)})
        for t in txns:
            date_str = t.get("Date", "")
            if not date_str:
                continue
            month_key = date_str[:7]
            category = t.get("Category", "Other")
            if category == "Transfer":
                continue
            try:
                amount = float(str(t.get("Amount", 0)).replace(",", "").replace("$", ""))
            except (ValueError, TypeError):
                continue
            if amount > 0:
                monthly_data[month_key]["income"] += amount
            else:
                monthly_data[month_key]["expenses"] += abs(amount)
                if category not in BUDGET_EXCLUDED_CATEGORIES:
                    monthly_data[month_key]["categories"][category] += abs(amount)

        sorted_months = sorted(monthly_data.keys())
        if not sorted_months:
            return jsonify({"empty": True})

        # Current month data
        curr = monthly_data.get(current_month, {"income": 0, "expenses": 0, "categories": {}})
        curr_income = curr["income"]
        curr_expenses = curr["expenses"]
        curr_savings = curr_income - curr_expenses
        curr_savings_rate = (curr_savings / curr_income * 100) if curr_income > 0 else 0

        # Previous month for comparison
        prev_month_date = (today.replace(day=1) - timedelta(days=1))
        prev_month_key = prev_month_date.strftime("%Y-%m")
        prev = monthly_data.get(prev_month_key, {"income": 0, "expenses": 0, "categories": {}})
        prev_expenses = prev["expenses"]
        spending_mom = ((curr_expenses - prev_expenses) / prev_expenses * 100) if prev_expenses > 0 else 0

        # Budget status for current month
        budget_status = []
        for cat, budget_amt in config.budgets.items():
            if cat in BUDGET_EXCLUDED_CATEGORIES or budget_amt <= 0:
                continue
            actual = curr["categories"].get(cat, 0)
            budget_status.append({
                "category": cat,
                "budget": budget_amt,
                "actual": round(actual, 2),
                "pct": round(actual / budget_amt * 100, 1) if budget_amt > 0 else 0,
            })
        budget_status.sort(key=lambda x: x["pct"], reverse=True)

        # Monthly trend (last 6 months)
        recent_months = sorted_months[-6:]
        trend = []
        for m in recent_months:
            d = monthly_data[m]
            trend.append({
                "month": m,
                "income": round(d["income"], 2),
                "expenses": round(d["expenses"], 2),
                "savings": round(d["income"] - d["expenses"], 2),
            })

        # Top spending categories (current month)
        cat_spending = sorted(curr["categories"].items(), key=lambda x: x[1], reverse=True)[:8]

        return jsonify({
            "empty": False,
            "current_month": current_month,
            "income": round(curr_income, 2),
            "expenses": round(curr_expenses, 2),
            "savings": round(curr_savings, 2),
            "savings_rate": round(curr_savings_rate, 1),
            "spending_mom": round(spending_mom, 1),
            "budget_status": budget_status,
            "trend": trend,
            "top_categories": [{"name": c, "amount": round(a, 2)} for c, a in cat_spending],
        })

    return app


def launch_ui(config_path: str = "config.yaml") -> None:
    """Start the local web UI and open a browser."""
    port = _get_free_port()
    app = create_app(config_path)

    url = f"http://127.0.0.1:{port}"
    print(f"\nStarting Finance Tracker UI at {url}")
    print("Press Ctrl+C to stop.\n")

    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    app.run(host="127.0.0.1", port=port, debug=False)
