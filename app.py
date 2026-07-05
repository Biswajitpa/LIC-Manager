"""
LIC Policy Manager
A small web app for an LIC (insurance) agent to store customer & policy
details, view a dashboard of policy stats, and export everything to CSV.

Run locally:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000

Default login (change this immediately, see bottom of this file):
    username: admin
    password: changeme123
"""

import csv
import io
import os
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, flash, Response, g, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "lic_manager.db")

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
# Database helpers
# --------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL
        );

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
        );
        """
    )
    # Seed a default admin user if none exists yet.
    cur = db.execute("SELECT COUNT(*) FROM users")
    if cur.fetchone()[0] == 0:
        db.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            ("admin", generate_password_hash("changeme123")),
        )
    db.commit()
    db.close()


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
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
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
        db = get_db()
        user = db.execute(
            "SELECT * FROM users WHERE id = ?", (session["user_id"],)
        ).fetchone()
        if not check_password_hash(user["password_hash"], current):
            flash("Current password is incorrect.", "error")
        elif len(new) < 6:
            flash("New password must be at least 6 characters.", "error")
        elif new != confirm:
            flash("New passwords do not match.", "error")
        else:
            db.execute(
                "UPDATE users SET password_hash = ? WHERE id = ?",
                (generate_password_hash(new), user["id"]),
            )
            db.commit()
            flash("Password updated.", "success")
    return render_template("account.html")


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

@app.route("/")
@login_required
def dashboard():
    db = get_db()
    total_customers = db.execute("SELECT COUNT(*) c FROM customers").fetchone()["c"]
    active_policies = db.execute(
        "SELECT COUNT(*) c FROM customers WHERE status = 'Active'"
    ).fetchone()["c"]
    total_premium = db.execute(
        "SELECT COALESCE(SUM(premium_amount), 0) s FROM customers"
    ).fetchone()["s"]
    total_sum_assured = db.execute(
        "SELECT COALESCE(SUM(sum_assured), 0) s FROM customers"
    ).fetchone()["s"]

    # Pie chart: policy type distribution
    type_rows = db.execute(
        "SELECT COALESCE(policy_type, 'Other') t, COUNT(*) c "
        "FROM customers GROUP BY t ORDER BY c DESC"
    ).fetchall()
    policy_type_labels = [r["t"] for r in type_rows]
    policy_type_counts = [r["c"] for r in type_rows]

    # Bar chart: month-wise new policies (based on start_date), last 12 months
    month_rows = db.execute(
        "SELECT strftime('%Y-%m', start_date) ym, COUNT(*) c "
        "FROM customers WHERE start_date IS NOT NULL AND start_date != '' "
        "GROUP BY ym ORDER BY ym"
    ).fetchall()
    month_labels = [r["ym"] for r in month_rows]
    month_counts = [r["c"] for r in month_rows]

    # Status breakdown (used for a small legend / second pie if wanted)
    status_rows = db.execute(
        "SELECT COALESCE(status,'Active') s, COUNT(*) c FROM customers GROUP BY s"
    ).fetchall()
    status_labels = [r["s"] for r in status_rows]
    status_counts = [r["c"] for r in status_rows]

    recent = db.execute(
        "SELECT * FROM customers ORDER BY created_at DESC LIMIT 5"
    ).fetchall()

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
    db = get_db()
    q = request.args.get("q", "").strip()
    if q:
        like = f"%{q}%"
        rows = db.execute(
            "SELECT * FROM customers WHERE full_name LIKE ? OR policy_number LIKE ? "
            "OR policy_name LIKE ? ORDER BY created_at DESC",
            (like, like, like),
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM customers ORDER BY created_at DESC"
        ).fetchall()
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
        db = get_db()
        try:
            db.execute(
                f"""INSERT INTO customers
                    ({', '.join(FORM_FIELDS)}, created_at)
                    VALUES ({', '.join('?' for _ in FORM_FIELDS)}, ?)""",
                [*(data[f] for f in FORM_FIELDS), datetime.now(timezone.utc).isoformat()],
            )
            db.commit()
            flash("Customer & policy saved.", "success")
            return redirect(url_for("customers"))
        except sqlite3.IntegrityError:
            flash("That policy number already exists.", "error")
    return render_template(
        "customer_form.html", customer={}, mode="new",
        policy_types=POLICY_TYPES, frequencies=PREMIUM_FREQUENCIES,
        statuses=STATUSES,
    )


@app.route("/customers/<int:cid>/edit", methods=["GET", "POST"])
@login_required
def edit_customer(cid):
    db = get_db()
    existing = db.execute("SELECT * FROM customers WHERE id = ?", (cid,)).fetchone()
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
            db.execute(
                f"""UPDATE customers SET {', '.join(f'{f} = ?' for f in FORM_FIELDS)}
                    WHERE id = ?""",
                [*(data[f] for f in FORM_FIELDS), cid],
            )
            db.commit()
            flash("Changes saved.", "success")
            return redirect(url_for("customers"))
        except sqlite3.IntegrityError:
            flash("That policy number already exists.", "error")

    return render_template(
        "customer_form.html", customer=existing, mode="edit", cid=cid,
        policy_types=POLICY_TYPES, frequencies=PREMIUM_FREQUENCIES,
        statuses=STATUSES,
    )


@app.route("/customers/<int:cid>/delete", methods=["POST"])
@login_required
def delete_customer(cid):
    db = get_db()
    db.execute("DELETE FROM customers WHERE id = ?", (cid,))
    db.commit()
    flash("Customer deleted.", "success")
    return redirect(url_for("customers"))


# --------------------------------------------------------------------------
# CSV export
# --------------------------------------------------------------------------

@app.route("/export/csv")
@login_required
def export_csv():
    db = get_db()
    rows = db.execute("SELECT * FROM customers ORDER BY created_at DESC").fetchall()

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
