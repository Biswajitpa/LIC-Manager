"""
LIC Policy Manager
A small web app for an LIC (insurance) agent to store customer & policy
details, view a dashboard of policy stats, and export everything to CSV.

Database: Turso (hosted, libSQL-compatible with SQLite) instead of a local
SQLite file, because serverless platforms like Vercel have a read-only /
ephemeral filesystem and can't persist a local .db file between requests.

Environment variables required (set these in Vercel -> Project -> Settings
-> Environment Variables, and in a local .env file for local dev):

    TURSO_DATABASE_URL   e.g. libsql://lic-biswajit.aws-ap-south-1.turso.io
    TURSO_AUTH_TOKEN     the auth token from your Turso dashboard
    SECRET_KEY           any long random string, for Flask sessions

Run locally:
    pip install -r requirements.txt
    export TURSO_DATABASE_URL="libsql://lic-biswajit.aws-ap-south-1.turso.io"
    export TURSO_AUTH_TOKEN="..."
    python app.py
Then open http://127.0.0.1:5000

Default login (change this immediately via the Account page):
    username: admin
    password: changeme123
"""

import csv
import io
import os
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, Response, g
)
from werkzeug.security import generate_password_hash, check_password_hash
import libsql_client

TURSO_DATABASE_URL = os.environ.get("TURSO_DATABASE_URL", "")
TURSO_AUTH_TOKEN = os.environ.get("TURSO_AUTH_TOKEN", "")

app = Flask(__name__)
# In production, set a real secret via environment variable.
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")

POLICY_TYPES = [
    "Endowment", "Term", "Money Back", "ULIP",
    "Pension", "Whole Life", "Child Plan", "Other"
]
PREMIUM_FREQUENCIES = ["Monthly", "Quarterly", "Half-Yearly", "Yearly", "Single"]
STATUSES = ["Active", "Lapsed", "Matured", "Surrendered"]


# --------------------------------------------------------------------------
# Database helpers (Turso / libSQL over HTTP -- stateless, safe for
# serverless functions where nothing persists on local disk)
# --------------------------------------------------------------------------

def _new_client():
    if not TURSO_DATABASE_URL or not TURSO_AUTH_TOKEN:
        raise RuntimeError(
            "TURSO_DATABASE_URL and TURSO_AUTH_TOKEN environment variables "
            "must be set. Add them in Vercel: Project -> Settings -> "
            "Environment Variables."
        )
    return libsql_client.create_client_sync(
        url=TURSO_DATABASE_URL,
        auth_token=TURSO_AUTH_TOKEN,
    )


def get_db():
    """Returns a request-scoped libSQL client, created once per request."""
    if "db" not in g:
        g.db = _new_client()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query_all(sql, params=()):
    """Run a SELECT and return all rows (list of Row, index/name accessible)."""
    result = get_db().execute(sql, params)
    return list(result)


def query_one(sql, params=()):
    """Run a SELECT and return the first row, or None."""
    rows = query_all(sql, params)
    return rows[0] if rows else None


def execute(sql, params=()):
    """Run an INSERT/UPDATE/DELETE. Returns the ResultSet (rows_affected etc.)."""
    return get_db().execute(sql, params)


_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS customers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        full_name TEXT NOT NULL,
        dob TEXT,
        gender TEXT,
        phone TEXT,
        email TEXT,
        address TEXT,
        nominee_name TEXT,
        nominee_relation TEXT,
        policy_name TEXT NOT NULL,
        policy_number TEXT NOT NULL UNIQUE,
        policy_type TEXT,
        sum_assured REAL,
        premium_amount REAL,
        premium_frequency TEXT,
        start_date TEXT,
        maturity_date TEXT,
        status TEXT DEFAULT 'Active',
        notes TEXT,
        created_at TEXT NOT NULL
    )
    """,
]

_db_initialized = False


def init_db():
    """Create tables (if missing) and seed a default admin user.

    Called on every cold start (see ensure_db_initialized below), not just
    when running `python app.py` directly -- serverless platforms import
    this module and never hit `if __name__ == "__main__"`.
    """
    global _db_initialized
    if _db_initialized:
        return
    client = _new_client()
    try:
        client.batch(_SCHEMA_STATEMENTS)
        result = client.execute("SELECT COUNT(*) c FROM users")
        if result[0]["c"] == 0:
            client.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                ("admin", generate_password_hash("changeme123")),
            )
        _db_initialized = True
    finally:
        client.close()


@app.before_request
def ensure_db_initialized():
    # Cheap no-op after the first successful run (per warm container).
    init_db()


# --------------------------------------------------------------------------
# Auth
# --------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = query_one("SELECT * FROM users WHERE username = ?", (username,))
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("dashboard"))
        flash("Wrong username or password.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    """Let the logged-in agent change their password."""
    if request.method == "POST":
        current = request.form.get("current_password", "")
        new = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")
        user = query_one("SELECT * FROM users WHERE id = ?", (session["user_id"],))
        if not check_password_hash(user["password_hash"], current):
            flash("Current password is incorrect.", "error")
        elif len(new) < 6:
            flash("New password must be at least 6 characters.", "error")
        elif new != confirm:
            flash("New passwords do not match.", "error")
        else:
            execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new), user["id"]),
            )
            flash("Password updated.", "success")
    return render_template("account.html")


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    total_customers = query_one("SELECT COUNT(*) c FROM customers")["c"]
    active_policies = query_one(
        "SELECT COUNT(*) c FROM customers WHERE status = 'Active'"
    )["c"]
    total_premium = query_one(
        "SELECT COALESCE(SUM(premium_amount), 0) s FROM customers"
    )["s"]
    total_sum_assured = query_one(
        "SELECT COALESCE(SUM(sum_assured), 0) s FROM customers"
    )["s"]

    # Pie chart: policy type distribution
    type_rows = query_all(
        "SELECT COALESCE(policy_type, 'Other') t, COUNT(*) c "
        "FROM customers GROUP BY t ORDER BY c DESC"
    )
    policy_type_labels = [r["t"] for r in type_rows]
    policy_type_counts = [r["c"] for r in type_rows]

    # Bar chart: month-wise new policies (based on start_date), last 12 months
    month_rows = query_all(
        "SELECT strftime('%Y-%m', start_date) ym, COUNT(*) c "
        "FROM customers WHERE start_date IS NOT NULL AND start_date != '' "
        "GROUP BY ym ORDER BY ym"
    )
    month_labels = [r["ym"] for r in month_rows]
    month_counts = [r["c"] for r in month_rows]

    # Status breakdown (used for a small legend / second pie if wanted)
    status_rows = query_all(
        "SELECT COALESCE(status,'Active') s, COUNT(*) c FROM customers GROUP BY s"
    )
    status_labels = [r["s"] for r in status_rows]
    status_counts = [r["c"] for r in status_rows]

    recent = query_all(
        "SELECT * FROM customers ORDER BY created_at DESC LIMIT 5"
    )

    return render_template(
        "dashboard.html",
        total_customers=total_customers,
        active_policies=active_policies,
        total_premium=total_premium,
        total_sum_assured=total_sum_assured,
        policy_type_labels=policy_type_labels,
        policy_type_counts=policy_type_counts,
        month_labels=month_labels,
        month_counts=month_counts,
        status_labels=status_labels,
        status_counts=status_counts,
        recent=recent,
    )


# --------------------------------------------------------------------------
# Customers CRUD
# --------------------------------------------------------------------------

FORM_FIELDS = [
    "full_name", "dob", "gender", "phone", "email", "address",
    "nominee_name", "nominee_relation", "policy_name", "policy_number",
    "policy_type", "sum_assured", "premium_amount", "premium_frequency",
    "start_date", "maturity_date", "status", "notes",
]


@app.route("/customers")
@login_required
def customers():
    q = request.args.get("q", "").strip()
    if q:
        like = f"%{q}%"
        rows = query_all(
            "SELECT * FROM customers WHERE full_name LIKE ? OR policy_number LIKE ? "
            "OR policy_name LIKE ? ORDER BY created_at DESC",
            (like, like, like),
        )
    else:
        rows = query_all("SELECT * FROM customers ORDER BY created_at DESC")
    return render_template("customers.html", customers=rows, q=q)


@app.route("/customers/new", methods=["GET", "POST"])
@login_required
def new_customer():
    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in FORM_FIELDS}
        if not data["full_name"] or not data["policy_name"] or not data["policy_number"]:
            flash("Name, policy name and policy number are required.", "error")
            return render_template(
                "customer_form.html", customer=data, mode="new",
                policy_types=POLICY_TYPES, frequencies=PREMIUM_FREQUENCIES,
                statuses=STATUSES,
            )
        try:
            execute(
                f"""INSERT INTO customers
                    ({', '.join(FORM_FIELDS)}, created_at)
                    VALUES ({', '.join('?' for _ in FORM_FIELDS)}, ?)""",
                [*(data[f] for f in FORM_FIELDS), datetime.now(timezone.utc).isoformat()],
            )
            flash("Customer & policy saved.", "success")
            return redirect(url_for("customers"))
        except libsql_client.LibsqlError as e:
            if "UNIQUE constraint failed" in str(e):
                flash("That policy number already exists.", "error")
            else:
                raise
    return render_template(
        "customer_form.html", customer={}, mode="new",
        policy_types=POLICY_TYPES, frequencies=PREMIUM_FREQUENCIES,
        statuses=STATUSES,
    )


@app.route("/customers/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def edit_customer(cid):
    existing = query_one("SELECT * FROM customers WHERE id = ?", (cid,))
    if not existing:
        flash("Customer not found.", "error")
        return redirect(url_for("customers"))

    if request.method == "POST":
        data = {f: request.form.get(f, "").strip() for f in FORM_FIELDS}
        if not data["full_name"] or not data["policy_name"] or not data["policy_number"]:
            flash("Name, policy name and policy number are required.", "error")
            return render_template(
                "customer_form.html", customer=data, mode="edit", cid=cid,
                policy_types=POLICY_TYPES, frequencies=PREMIUM_FREQUENCIES,
                statuses=STATUSES,
            )
        try:
            execute(
                f"""UPDATE customers SET {', '.join(f'{f} = ?' for f in FORM_FIELDS)}
                    WHERE id = ?""",
                [*(data[f] for f in FORM_FIELDS), cid],
            )
            flash("Changes saved.", "success")
            return redirect(url_for("customers"))
        except libsql_client.LibsqlError as e:
            if "UNIQUE constraint failed" in str(e):
                flash("That policy number already exists.", "error")
            else:
                raise

    return render_template(
        "customer_form.html", customer=existing, mode="edit", cid=cid,
        policy_types=POLICY_TYPES, frequencies=PREMIUM_FREQUENCIES,
        statuses=STATUSES,
    )


@app.route("/customers/<int:cid>/delete", methods=["POST"])
@login_required
def delete_customer(cid):
    execute("DELETE FROM customers WHERE id = ?", (cid,))
    flash("Customer deleted.", "success")
    return redirect(url_for("customers"))


# --------------------------------------------------------------------------
# CSV export
# --------------------------------------------------------------------------

@app.route("/export/csv")
@login_required
def export_csv():
    rows = query_all("SELECT * FROM customers ORDER BY created_at DESC")

    output = io.StringIO()
    writer = csv.writer(output)
    headers = [
        "ID", "Full Name", "DOB", "Gender", "Phone", "Email", "Address",
        "Nominee Name", "Nominee Relation", "Policy Name", "Policy Number",
        "Policy Type", "Sum Assured", "Premium Amount", "Premium Frequency",
        "Start Date", "Maturity Date", "Status", "Notes", "Created At",
    ]
    writer.writerow(headers)
    for r in rows:
        writer.writerow([
            r["id"], r["full_name"], r["dob"], r["gender"], r["phone"],
            r["email"], r["address"], r["nominee_name"], r["nominee_relation"],
            r["policy_name"], r["policy_number"], r["policy_type"],
            r["sum_assured"], r["premium_amount"], r["premium_frequency"],
            r["start_date"], r["maturity_date"], r["status"], r["notes"],
            r["created_at"],
        ])

    csv_data = output.getvalue()
    filename = f"lic_customers_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


if __name__ == "__main__":
    init_db()
    app.run(debug=True)
