"""
Awadh Saw Mill — backend (Render or Vercel, with a Neon Postgres database)

Flask app exposing /api/* as a JSON API.
  * On Render (or when run locally) Flask also serves the frontend from /public.
  * On Vercel, Vercel serves /public itself and only runs this for /api/*.

Requires an environment variable DATABASE_URL pointing to a Postgres database
(Neon's *pooled* connection string — the host contains "-pooler" — is best).

Run locally:
    export DATABASE_URL="postgresql://user:pass@host/db?sslmode=require"
    pip install -r requirements.txt
    python app.py            # then open http://localhost:5000

Render start command:
    gunicorn app:app
"""
import os
from datetime import datetime, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, request, jsonify, g

# static_folder=public + empty url path => /css/style.css, /js/app.js etc. are served
# straight from the public/ folder (used on Render; Vercel serves these itself).
app = Flask(__name__, static_folder="public", static_url_path="")

WOOD_TYPES = ["teak", "sal", "bija", "khamhar", "others"]
DEFAULT_RATES = {"teak": 6000, "sal": 2200, "bija": 2500, "khamhar": 2800, "others": 1100}

# Ready-made items catalog. Takhat sizes have known fixed prices; frame sizes
# are offered as quick picks but the shop enters the price at sale time
# (plus a "Custom size" option for anything not listed).
READYMADE_CATALOG = {
    "takhat": {
        "label": "Takhat",
        "sizes": [
            {"label": "6ft x 3ft", "price": 2000},
            {"label": "6ft x 4ft", "price": 2500},
        ],
    },
    "frame": {
        "label": "Wooden Frame",
        "sizes": [
            {"label": "3ft x 2ft", "price": None},
            {"label": "4ft x 2.5ft", "price": None},
            {"label": "4ft x 3ft", "price": None},
            {"label": "5ft x 3ft", "price": None},
            {"label": "Custom size", "price": None},
        ],
    },
}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _database_url():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Add it in your host's Environment settings "
            "(use your Neon connection string, the one with -pooler in the host name)."
        )
    return url


class Db:
    """Thin wrapper so call sites can keep using db.execute(...).fetchone()/
    .fetchall(), the same convenience pattern sqlite3.Connection offered,
    on top of psycopg2 (which normally requires a separate cursor)."""

    def __init__(self, conn):
        self.conn = conn

    def execute(self, query, params=None):
        cur = self.conn.cursor(cursor_factory=RealDictCursor)
        cur.execute(query, params or [])
        return cur

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def get_db():
    if "db" not in g:
        conn = psycopg2.connect(_database_url())
        g.db = Db(conn)
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    conn = psycopg2.connect(_database_url())
    cur = conn.cursor()

    statements = [
        """CREATE TABLE IF NOT EXISTS rates (
            wood TEXT PRIMARY KEY,
            rate REAL NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS customers (
            id SERIAL PRIMARY KEY,
            name TEXT,
            phone TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS estimates (
            id SERIAL PRIMARY KEY,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            making_charges REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS estimate_items (
            id SERIAL PRIMARY KEY,
            estimate_id INTEGER NOT NULL REFERENCES estimates(id),
            item_type TEXT NOT NULL DEFAULT 'custom',
            name TEXT,
            size_label TEXT DEFAULT '',
            wood TEXT NOT NULL DEFAULT '',
            rate REAL NOT NULL,
            qty INTEGER NOT NULL,
            len_ft REAL DEFAULT 0, len_in REAL DEFAULT 0,
            wid_ft REAL DEFAULT 0, wid_in REAL DEFAULT 0,
            hgt_ft REAL DEFAULT 0, hgt_in REAL DEFAULT 0
        )""",
        """CREATE TABLE IF NOT EXISTS challans (
            id SERIAL PRIMARY KEY,
            estimate_id INTEGER NOT NULL REFERENCES estimates(id),
            challan_number TEXT UNIQUE NOT NULL,
            advance_paid REAL NOT NULL DEFAULT 0,
            delivery_date TEXT,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS challan_counters (
            date_key TEXT PRIMARY KEY,
            counter INTEGER NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS employees (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT,
            role TEXT,
            wage_type TEXT NOT NULL DEFAULT 'daily',
            daily_wage REAL NOT NULL DEFAULT 0,
            hourly_wage DOUBLE PRECISION NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        )""",
        """CREATE TABLE IF NOT EXISTS attendance (
            id SERIAL PRIMARY KEY,
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('present','absent','half')),
            hours DOUBLE PRECISION NOT NULL DEFAULT 0,
            UNIQUE(employee_id, date)
        )""",
        # NOTE: the table is called "payments" for historical reasons; each row
        # is an advance taken by an employee on `date`.
        """CREATE TABLE IF NOT EXISTS payments (
            id SERIAL PRIMARY KEY,
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            week_start TEXT NOT NULL,
            date TEXT,
            amount REAL NOT NULL,
            note TEXT,
            paid_on TEXT NOT NULL
        )""",
    ]
    # One transaction-scoped lock so two cold starts never run the DDL at the
    # same time (works with Neon's pooled connections, unlike session locks).
    cur.execute("SELECT pg_advisory_xact_lock(727274)")

    for stmt in statements:
        cur.execute(stmt)

    # --- migrations for databases created by earlier versions (safe to repeat) ---
    cur.execute("ALTER TABLE employees ADD COLUMN IF NOT EXISTS wage_type TEXT NOT NULL DEFAULT 'daily'")
    cur.execute("ALTER TABLE employees ADD COLUMN IF NOT EXISTS hourly_wage DOUBLE PRECISION NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE attendance ADD COLUMN IF NOT EXISTS hours DOUBLE PRECISION NOT NULL DEFAULT 0")
    cur.execute("ALTER TABLE payments ADD COLUMN IF NOT EXISTS date TEXT")
    cur.execute("UPDATE payments SET date = LEFT(paid_on, 10) WHERE date IS NULL")

    for wood, rate in DEFAULT_RATES.items():
        cur.execute(
            "INSERT INTO rates (wood, rate) VALUES (%s, %s) ON CONFLICT (wood) DO NOTHING",
            (wood, rate),
        )
    conn.commit()
    cur.close()
    conn.close()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Frontend (used on Render / local; Vercel serves public/ on its own)
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/healthz")
def healthz():
    """Lightweight check for uptime pings; does not touch the database."""
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# Rates
# ---------------------------------------------------------------------------
@app.route("/api/rates", methods=["GET"])
def get_rates():
    db = get_db()
    rows = db.execute("SELECT wood, rate FROM rates").fetchall()
    return jsonify({r["wood"]: r["rate"] for r in rows})


@app.route("/api/rates/<wood>", methods=["PUT"])
def set_rate(wood):
    if wood not in WOOD_TYPES:
        return jsonify({"error": "unknown wood type"}), 400
    data = request.get_json(force=True)
    rate = float(data.get("rate", 0))
    db = get_db()
    db.execute(
        "INSERT INTO rates (wood, rate) VALUES (%s, %s) "
        "ON CONFLICT (wood) DO UPDATE SET rate = excluded.rate",
        (wood, rate),
    )
    db.commit()
    return jsonify({"wood": wood, "rate": rate})


# ---------------------------------------------------------------------------
# Ready-made items catalog (takhat, frames, ...)
# ---------------------------------------------------------------------------
@app.route("/api/readymade-catalog", methods=["GET"])
def get_readymade_catalog():
    return jsonify(READYMADE_CATALOG)


# ---------------------------------------------------------------------------
# Estimates (create with items, in one call)
# ---------------------------------------------------------------------------
@app.route("/api/estimates", methods=["POST"])
def create_estimate():
    data = request.get_json(force=True)
    customer = data.get("customer", {})
    phone = (customer.get("phone") or "").strip()
    name = (customer.get("name") or "").strip()
    if not phone or not phone.isdigit() or len(phone) != 10:
        return jsonify({"error": "a valid 10-digit mobile number is required"}), 400

    items = data.get("items", [])
    making_charges = float(data.get("making_charges", 0))

    db = get_db()
    cur = db.execute(
        "INSERT INTO customers (name, phone, created_at) VALUES (%s, %s, %s) RETURNING id",
        (name, phone, now_iso()),
    )
    customer_id = cur.fetchone()["id"]

    cur = db.execute(
        "INSERT INTO estimates (customer_id, making_charges, created_at) VALUES (%s, %s, %s) RETURNING id",
        (customer_id, making_charges, now_iso()),
    )
    estimate_id = cur.fetchone()["id"]

    for it in items:
        item_type = it.get("item_type", "custom")
        if item_type not in ("custom", "readymade"):
            item_type = "custom"
        db.execute(
            """INSERT INTO estimate_items
               (estimate_id, item_type, name, size_label, wood, rate, qty, len_ft, len_in, wid_ft, wid_in, hgt_ft, hgt_in)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                estimate_id,
                item_type,
                it.get("name", ""),
                it.get("size_label", ""),
                it.get("wood", "others"),
                float(it.get("rate", 0)),
                int(it.get("qty", 1)),
                float(it.get("len_ft", 0)), float(it.get("len_in", 0)),
                float(it.get("wid_ft", 0)), float(it.get("wid_in", 0)),
                float(it.get("hgt_ft", 0)), float(it.get("hgt_in", 0)),
            ),
        )
    db.commit()
    return jsonify(_estimate_payload(db, estimate_id)), 201


def _cft(item):
    def feet(ft, inch):
        return (ft or 0) + (inch or 0) / 12
    return feet(item["len_ft"], item["len_in"]) * feet(item["wid_ft"], item["wid_in"]) * feet(item["hgt_ft"], item["hgt_in"])


def _estimate_payload(db, estimate_id):
    est = db.execute("SELECT * FROM estimates WHERE id=%s", (estimate_id,)).fetchone()
    if not est:
        return None
    customer = db.execute("SELECT * FROM customers WHERE id=%s", (est["customer_id"],)).fetchone()
    items = db.execute("SELECT * FROM estimate_items WHERE estimate_id=%s", (estimate_id,)).fetchall()

    item_list = []
    subtotal = 0
    total_cft = 0
    for it in items:
        item_type = it["item_type"] or "custom"
        if item_type == "readymade":
            # Fixed-price item: cost is simply qty x price, no volume involved.
            cft = 0
            cost = it["qty"] * it["rate"]
        else:
            cft = _cft(it) * it["qty"]
            cost = cft * it["rate"]
            total_cft += cft
        subtotal += cost
        item_list.append({
            "item_type": item_type,
            "name": it["name"], "size_label": it["size_label"] or "",
            "wood": it["wood"], "rate": it["rate"], "qty": it["qty"],
            "len_ft": it["len_ft"], "len_in": it["len_in"],
            "wid_ft": it["wid_ft"], "wid_in": it["wid_in"],
            "hgt_ft": it["hgt_ft"], "hgt_in": it["hgt_in"],
            "cft": round(cft, 3), "cost": round(cost, 2),
        })

    challan = db.execute("SELECT * FROM challans WHERE estimate_id=%s", (estimate_id,)).fetchone()

    return {
        "id": est["id"],
        "customer": {"name": customer["name"], "phone": customer["phone"]},
        "making_charges": est["making_charges"],
        "items": item_list,
        "subtotal": round(subtotal, 2),
        "total_cft": round(total_cft, 3),
        "grand_total": round(subtotal + est["making_charges"], 2),
        "created_at": est["created_at"],
        "challan": dict(challan) if challan else None,
    }


@app.route("/api/estimates/<int:estimate_id>", methods=["GET"])
def get_estimate(estimate_id):
    db = get_db()
    payload = _estimate_payload(db, estimate_id)
    if not payload:
        return jsonify({"error": "not found"}), 404
    return jsonify(payload)


@app.route("/api/estimates", methods=["GET"])
def list_estimates():
    db = get_db()
    rows = db.execute(
        """SELECT e.id, e.created_at, c.name, c.phone
           FROM estimates e JOIN customers c ON c.id = e.customer_id
           ORDER BY e.id DESC LIMIT 100"""
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Challans (sequential CH-YYYYMMDD-001 numbering)
# ---------------------------------------------------------------------------
@app.route("/api/estimates/<int:estimate_id>/challan", methods=["POST"])
def create_challan(estimate_id):
    data = request.get_json(force=True)
    advance_paid = float(data.get("advance_paid", 0))
    delivery_date = data.get("delivery_date")

    db = get_db()
    existing = db.execute("SELECT * FROM challans WHERE estimate_id=%s", (estimate_id,)).fetchone()
    if existing:
        db.execute(
            "UPDATE challans SET advance_paid=%s, delivery_date=%s WHERE id=%s",
            (advance_paid, delivery_date, existing["id"]),
        )
        db.commit()
        return jsonify(_estimate_payload(db, estimate_id))

    date_key = datetime.now().strftime("%Y%m%d")
    row = db.execute("SELECT counter FROM challan_counters WHERE date_key=%s", (date_key,)).fetchone()
    next_no = (row["counter"] + 1) if row else 1
    db.execute(
        "INSERT INTO challan_counters (date_key, counter) VALUES (%s, %s) "
        "ON CONFLICT (date_key) DO UPDATE SET counter = excluded.counter",
        (date_key, next_no),
    )
    challan_number = f"CH-{date_key}-{next_no:03d}"
    db.execute(
        "INSERT INTO challans (estimate_id, challan_number, advance_paid, delivery_date, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (estimate_id, challan_number, advance_paid, delivery_date, now_iso()),
    )
    db.commit()
    return jsonify(_estimate_payload(db, estimate_id)), 201


# ---------------------------------------------------------------------------
# Payroll — employees (daily-wage or hourly-wage)
# ---------------------------------------------------------------------------
def _parse_wage(data):
    """Returns (wage_type, daily_wage, hourly_wage) from a request body.
    Accepts {wage_type, wage}; the single `wage` value goes to the matching rate."""
    wage_type = data.get("wage_type", "daily")
    if wage_type not in ("daily", "hourly"):
        raise ValueError("wage_type must be 'daily' or 'hourly'")
    wage = float(data.get("wage", data.get("daily_wage", 0)) or 0)
    if wage < 0:
        raise ValueError("wage cannot be negative")
    return wage_type, (wage if wage_type == "daily" else 0.0), (wage if wage_type == "hourly" else 0.0)


@app.route("/api/employees", methods=["GET"])
def list_employees():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM employees WHERE active=1 ORDER BY LOWER(name)"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/employees", methods=["POST"])
def add_employee():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "employee name is required"}), 400
    try:
        wage_type, daily_wage, hourly_wage = _parse_wage(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO employees (name, phone, role, wage_type, daily_wage, hourly_wage, created_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (name, data.get("phone", ""), data.get("role", ""), wage_type, daily_wage, hourly_wage, now_iso()),
    )
    emp = cur.fetchone()
    db.commit()
    return jsonify(dict(emp)), 201


@app.route("/api/employees/<int:emp_id>", methods=["PUT"])
def update_employee(emp_id):
    data = request.get_json(force=True)
    try:
        wage_type, daily_wage, hourly_wage = _parse_wage(data)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    db = get_db()
    cur = db.execute(
        "UPDATE employees SET name=%s, phone=%s, role=%s, wage_type=%s, daily_wage=%s, hourly_wage=%s "
        "WHERE id=%s RETURNING *",
        (data.get("name"), data.get("phone", ""), data.get("role", ""),
         wage_type, daily_wage, hourly_wage, emp_id),
    )
    emp = cur.fetchone()
    db.commit()
    if not emp:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(emp))


@app.route("/api/employees/<int:emp_id>", methods=["DELETE"])
def remove_employee(emp_id):
    db = get_db()
    db.execute("UPDATE employees SET active=0 WHERE id=%s", (emp_id,))
    db.commit()
    return jsonify({"deleted": True})


# ---------------------------------------------------------------------------
# Payroll — attendance
#   daily employees : status = present / half / absent
#   hourly employees: send `hours` (0 - 24); status is derived from it
# ---------------------------------------------------------------------------
@app.route("/api/attendance", methods=["POST"])
def mark_attendance():
    data = request.get_json(force=True)
    employee_id = data.get("employee_id")
    date = data.get("date")
    if not employee_id or not date:
        return jsonify({"error": "employee_id and date are required"}), 400

    if data.get("hours") is not None:
        try:
            hours = float(data["hours"])
        except (TypeError, ValueError):
            return jsonify({"error": "hours must be a number"}), 400
        if hours < 0 or hours > 24:
            return jsonify({"error": "hours must be between 0 and 24"}), 400
        status = "present" if hours > 0 else "absent"
    else:
        hours = 0.0
        status = data.get("status")
        if status not in ("present", "absent", "half"):
            return jsonify({"error": "status must be present, absent, or half"}), 400

    db = get_db()
    db.execute(
        "INSERT INTO attendance (employee_id, date, status, hours) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (employee_id, date) DO UPDATE SET status = excluded.status, hours = excluded.hours",
        (employee_id, date, status, hours),
    )
    db.commit()
    return jsonify({"employee_id": employee_id, "date": date, "status": status, "hours": hours})


@app.route("/api/attendance", methods=["GET"])
def get_attendance():
    """?month=YYYY-MM  (optional &employee_id=)"""
    month = request.args.get("month")
    employee_id = request.args.get("employee_id")
    db = get_db()
    query = "SELECT * FROM attendance WHERE date LIKE %s"
    params = [f"{month}%"]
    if employee_id:
        query += " AND employee_id=%s"
        params.append(employee_id)
    rows = db.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Payroll — advances (money an employee takes during the week)
# ---------------------------------------------------------------------------
def week_range(any_date_str):
    """Given any YYYY-MM-DD, return (monday, sunday) as date objects for that ISO week."""
    d = datetime.strptime(any_date_str, "%Y-%m-%d").date()
    monday = d - timedelta(days=d.isoweekday() - 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


@app.route("/api/advances", methods=["POST"])
def add_advance():
    data = request.get_json(force=True)
    try:
        employee_id = int(data.get("employee_id"))
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return jsonify({"error": "employee_id and amount are required"}), 400
    date = data.get("date") or datetime.now().strftime("%Y-%m-%d")
    try:
        monday, _ = week_range(date)
    except ValueError:
        return jsonify({"error": "date must be YYYY-MM-DD"}), 400
    if amount <= 0:
        return jsonify({"error": "amount must be greater than 0"}), 400

    db = get_db()
    emp = db.execute("SELECT id FROM employees WHERE id=%s AND active=1", (employee_id,)).fetchone()
    if not emp:
        return jsonify({"error": "employee not found"}), 404
    cur = db.execute(
        "INSERT INTO payments (employee_id, week_start, date, amount, note, paid_on) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING *",
        (employee_id, monday.isoformat(), date, amount, (data.get("note") or "").strip(), now_iso()),
    )
    row = cur.fetchone()
    db.commit()
    return jsonify(dict(row)), 201


@app.route("/api/advances", methods=["GET"])
def list_advances():
    """?employee_id=  &limit=50   — newest first, with the employee's name."""
    employee_id = request.args.get("employee_id")
    try:
        limit = max(1, min(int(request.args.get("limit", 50)), 200))
    except ValueError:
        limit = 50
    db = get_db()
    query = (
        "SELECT p.id, p.employee_id, e.name AS employee_name, p.date, p.amount, p.note "
        "FROM payments p JOIN employees e ON e.id = p.employee_id"
    )
    params = []
    if employee_id:
        query += " WHERE p.employee_id=%s"
        params.append(employee_id)
    query += " ORDER BY p.date DESC, p.id DESC LIMIT %s"
    params.append(limit)
    rows = db.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/advances/<int:advance_id>", methods=["DELETE"])
def delete_advance(advance_id):
    db = get_db()
    db.execute("DELETE FROM payments WHERE id=%s", (advance_id,))
    db.commit()
    return jsonify({"deleted": True})


# ---------------------------------------------------------------------------
# Payroll — weekly summary:  earned  -  advances taken that week  =  amount due
# ---------------------------------------------------------------------------
@app.route("/api/payroll/summary", methods=["GET"])
def payroll_summary():
    """?week=YYYY-MM-DD  (any date inside the target week; defaults to today)"""
    week_date = request.args.get("week") or datetime.now().strftime("%Y-%m-%d")
    try:
        monday, sunday = week_range(week_date)
    except ValueError:
        return jsonify({"error": "week must be YYYY-MM-DD"}), 400
    monday_s, sunday_s = monday.isoformat(), sunday.isoformat()

    db = get_db()
    employees = db.execute("SELECT * FROM employees WHERE active=1 ORDER BY LOWER(name)").fetchall()

    summary = []
    for emp in employees:
        rows = db.execute(
            "SELECT status, hours FROM attendance WHERE employee_id=%s AND date BETWEEN %s AND %s",
            (emp["id"], monday_s, sunday_s),
        ).fetchall()

        wage_type = emp["wage_type"] or "daily"
        if wage_type == "hourly":
            total_hours = round(sum(float(r["hours"] or 0) for r in rows), 2)
            days_worked = sum(1 for r in rows if float(r["hours"] or 0) > 0)
            earned = round(float(emp["hourly_wage"]) * total_hours, 2)
            present_days = days_worked
        else:
            present_days = sum(1 if r["status"] == "present" else 0.5 if r["status"] == "half" else 0 for r in rows)
            total_hours = 0
            earned = round(float(emp["daily_wage"]) * present_days, 2)

        adv_rows = db.execute(
            "SELECT id, date, amount, note FROM payments "
            "WHERE employee_id=%s AND date BETWEEN %s AND %s ORDER BY date, id",
            (emp["id"], monday_s, sunday_s),
        ).fetchall()
        advances = [
            {"id": a["id"], "date": a["date"], "amount": round(float(a["amount"]), 2), "note": a["note"] or ""}
            for a in adv_rows
        ]
        advances_total = round(sum(a["amount"] for a in advances), 2)

        summary.append({
            "employee_id": emp["id"],
            "name": emp["name"],
            "role": emp["role"],
            "wage_type": wage_type,
            "daily_wage": emp["daily_wage"],
            "hourly_wage": emp["hourly_wage"],
            "present_days": present_days,
            "total_hours": total_hours,
            "earned": earned,
            "advances": advances,
            "advances_total": advances_total,
            "balance": round(earned - advances_total, 2),
        })
    return jsonify({"week_start": monday_s, "week_end": sunday_s, "employees": summary})


# ---------------------------------------------------------------------------
# One-time / cold-start schema setup.
# Vercel imports this module fresh on each cold start, so this runs
# occasionally rather than on every request — CREATE TABLE IF NOT EXISTS and
# ON CONFLICT DO NOTHING make it safe to repeat.
# ---------------------------------------------------------------------------
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
