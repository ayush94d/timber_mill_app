"""
Awadh Saw Mill — backend
Flask + SQLite. Serves the frontend (static/) and a JSON API under /api/*.

Run locally:
    pip install -r requirements.txt
    python app.py
Then open http://localhost:5000
"""
import os
import sqlite3
from datetime import datetime, timedelta
from flask import Flask, request, jsonify, g, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "awadh.db")

app = Flask(__name__, static_folder="static", static_url_path="")

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
        CREATE TABLE IF NOT EXISTS rates (
            wood TEXT PRIMARY KEY,
            rate REAL NOT NULL
        );

        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS estimates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL REFERENCES customers(id),
            making_charges REAL NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS estimate_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        );

        CREATE TABLE IF NOT EXISTS challans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            estimate_id INTEGER NOT NULL REFERENCES estimates(id),
            challan_number TEXT UNIQUE NOT NULL,
            advance_paid REAL NOT NULL DEFAULT 0,
            delivery_date TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS challan_counters (
            date_key TEXT PRIMARY KEY,
            counter INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            role TEXT,
            daily_wage REAL NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            date TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('present','absent','half')),
            UNIQUE(employee_id, date)
        );

        CREATE TABLE IF NOT EXISTS payments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            employee_id INTEGER NOT NULL REFERENCES employees(id),
            week_start TEXT NOT NULL,
            amount REAL NOT NULL,
            note TEXT,
            paid_on TEXT NOT NULL
        );
        """
    )

    # --- migration: older deployments had employees.monthly_salary instead
    # of daily_wage. Add the new column and backfill a sensible daily rate
    # (monthly ÷ 30) so existing employee records aren't lost.
    cols = [r[1] for r in db.execute("PRAGMA table_info(employees)").fetchall()]
    if "daily_wage" not in cols:
        db.execute("ALTER TABLE employees ADD COLUMN daily_wage REAL NOT NULL DEFAULT 0")
    if "monthly_salary" in cols:
        db.execute(
            "UPDATE employees SET daily_wage = ROUND(monthly_salary / 30.0, 2) "
            "WHERE (daily_wage IS NULL OR daily_wage = 0) AND monthly_salary > 0"
        )
        try:
            db.execute("ALTER TABLE employees DROP COLUMN monthly_salary")
        except sqlite3.OperationalError:
            pass  # older SQLite without DROP COLUMN support — harmless to leave the column

    # --- migration: estimate_items gained item_type/size_label to support
    # fixed-price ready-made items (takhat, frames) alongside CFT-based pieces.
    item_cols = [r[1] for r in db.execute("PRAGMA table_info(estimate_items)").fetchall()]
    if "item_type" not in item_cols:
        db.execute("ALTER TABLE estimate_items ADD COLUMN item_type TEXT NOT NULL DEFAULT 'custom'")
    if "size_label" not in item_cols:
        db.execute("ALTER TABLE estimate_items ADD COLUMN size_label TEXT DEFAULT ''")

    for wood, rate in DEFAULT_RATES.items():
        db.execute(
            "INSERT OR IGNORE INTO rates (wood, rate) VALUES (?, ?)", (wood, rate)
        )
    db.commit()
    db.close()


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


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
        "INSERT INTO rates (wood, rate) VALUES (?, ?) "
        "ON CONFLICT(wood) DO UPDATE SET rate=excluded.rate",
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
        "INSERT INTO customers (name, phone, created_at) VALUES (?, ?, ?)",
        (name, phone, now_iso()),
    )
    customer_id = cur.lastrowid

    cur = db.execute(
        "INSERT INTO estimates (customer_id, making_charges, created_at) VALUES (?, ?, ?)",
        (customer_id, making_charges, now_iso()),
    )
    estimate_id = cur.lastrowid

    for it in items:
        item_type = it.get("item_type", "custom")
        if item_type not in ("custom", "readymade"):
            item_type = "custom"
        db.execute(
            """INSERT INTO estimate_items
               (estimate_id, item_type, name, size_label, wood, rate, qty, len_ft, len_in, wid_ft, wid_in, hgt_ft, hgt_in)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
    est = db.execute("SELECT * FROM estimates WHERE id=?", (estimate_id,)).fetchone()
    if not est:
        return None
    customer = db.execute("SELECT * FROM customers WHERE id=?", (est["customer_id"],)).fetchone()
    items = db.execute("SELECT * FROM estimate_items WHERE estimate_id=?", (estimate_id,)).fetchall()

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

    challan = db.execute("SELECT * FROM challans WHERE estimate_id=?", (estimate_id,)).fetchone()

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
    existing = db.execute("SELECT * FROM challans WHERE estimate_id=?", (estimate_id,)).fetchone()
    if existing:
        db.execute(
            "UPDATE challans SET advance_paid=?, delivery_date=? WHERE id=?",
            (advance_paid, delivery_date, existing["id"]),
        )
        db.commit()
        return jsonify(_estimate_payload(db, estimate_id))

    date_key = datetime.now().strftime("%Y%m%d")
    row = db.execute("SELECT counter FROM challan_counters WHERE date_key=?", (date_key,)).fetchone()
    next_no = (row["counter"] + 1) if row else 1
    db.execute(
        "INSERT INTO challan_counters (date_key, counter) VALUES (?, ?) "
        "ON CONFLICT(date_key) DO UPDATE SET counter=excluded.counter",
        (date_key, next_no),
    )
    challan_number = f"CH-{date_key}-{next_no:03d}"
    db.execute(
        "INSERT INTO challans (estimate_id, challan_number, advance_paid, delivery_date, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (estimate_id, challan_number, advance_paid, delivery_date, now_iso()),
    )
    db.commit()
    return jsonify(_estimate_payload(db, estimate_id)), 201


# ---------------------------------------------------------------------------
# Payroll — employees
# ---------------------------------------------------------------------------
@app.route("/api/employees", methods=["GET"])
def list_employees():
    db = get_db()
    rows = db.execute(
        "SELECT * FROM employees WHERE active=1 ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/employees", methods=["POST"])
def add_employee():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "employee name is required"}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO employees (name, phone, role, daily_wage, created_at) VALUES (?, ?, ?, ?, ?)",
        (name, data.get("phone", ""), data.get("role", ""), float(data.get("daily_wage", 0)), now_iso()),
    )
    db.commit()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(emp)), 201


@app.route("/api/employees/<int:emp_id>", methods=["PUT"])
def update_employee(emp_id):
    data = request.get_json(force=True)
    db = get_db()
    db.execute(
        "UPDATE employees SET name=?, phone=?, role=?, daily_wage=? WHERE id=?",
        (data.get("name"), data.get("phone", ""), data.get("role", ""),
         float(data.get("daily_wage", 0)), emp_id),
    )
    db.commit()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone()
    if not emp:
        return jsonify({"error": "not found"}), 404
    return jsonify(dict(emp))


@app.route("/api/employees/<int:emp_id>", methods=["DELETE"])
def remove_employee(emp_id):
    db = get_db()
    db.execute("UPDATE employees SET active=0 WHERE id=?", (emp_id,))
    db.commit()
    return jsonify({"deleted": True})


# ---------------------------------------------------------------------------
# Payroll — attendance
# ---------------------------------------------------------------------------
@app.route("/api/attendance", methods=["POST"])
def mark_attendance():
    data = request.get_json(force=True)
    employee_id = data.get("employee_id")
    date = data.get("date")
    status = data.get("status")
    if status not in ("present", "absent", "half"):
        return jsonify({"error": "status must be present, absent, or half"}), 400
    db = get_db()
    db.execute(
        "INSERT INTO attendance (employee_id, date, status) VALUES (?, ?, ?) "
        "ON CONFLICT(employee_id, date) DO UPDATE SET status=excluded.status",
        (employee_id, date, status),
    )
    db.commit()
    return jsonify({"employee_id": employee_id, "date": date, "status": status})


@app.route("/api/attendance", methods=["GET"])
def get_attendance():
    """?month=YYYY-MM  (optional &employee_id=)"""
    month = request.args.get("month")
    employee_id = request.args.get("employee_id")
    db = get_db()
    query = "SELECT * FROM attendance WHERE date LIKE ?"
    params = [f"{month}%"]
    if employee_id:
        query += " AND employee_id=?"
        params.append(employee_id)
    rows = db.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
# Payroll — weekly wage summary & payments
# ---------------------------------------------------------------------------
def week_range(any_date_str):
    """Given any YYYY-MM-DD, return (monday, sunday) as date objects for that ISO week."""
    d = datetime.strptime(any_date_str, "%Y-%m-%d").date()
    monday = d - timedelta(days=d.isoweekday() - 1)
    sunday = monday + timedelta(days=6)
    return monday, sunday


@app.route("/api/payroll/summary", methods=["GET"])
def payroll_summary():
    """?week=YYYY-MM-DD  (any date inside the target week; defaults to today)"""
    week_date = request.args.get("week") or datetime.now().strftime("%Y-%m-%d")
    monday, sunday = week_range(week_date)
    monday_s, sunday_s = monday.isoformat(), sunday.isoformat()

    db = get_db()
    employees = db.execute("SELECT * FROM employees WHERE active=1 ORDER BY name COLLATE NOCASE").fetchall()

    summary = []
    for emp in employees:
        rows = db.execute(
            "SELECT status FROM attendance WHERE employee_id=? AND date BETWEEN ? AND ?",
            (emp["id"], monday_s, sunday_s),
        ).fetchall()
        present_days = sum(1 if r["status"] == "present" else 0.5 if r["status"] == "half" else 0 for r in rows)
        earned = round(emp["daily_wage"] * present_days, 2)

        paid_row = db.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM payments WHERE employee_id=? AND week_start=?",
            (emp["id"], monday_s),
        ).fetchone()
        paid = paid_row["total"] or 0

        summary.append({
            "employee_id": emp["id"],
            "name": emp["name"],
            "role": emp["role"],
            "daily_wage": emp["daily_wage"],
            "present_days": present_days,
            "marked_days": len(rows),
            "earned": earned,
            "paid": round(paid, 2),
            "balance": round(earned - paid, 2),
        })
    return jsonify({"week_start": monday_s, "week_end": sunday_s, "employees": summary})


@app.route("/api/payments", methods=["POST"])
def record_payment():
    data = request.get_json(force=True)
    employee_id = data.get("employee_id")
    week_start = data.get("week_start")
    amount = float(data.get("amount", 0))
    if not employee_id or not week_start or amount <= 0:
        return jsonify({"error": "employee_id, week_start, and a positive amount are required"}), 400
    db = get_db()
    cur = db.execute(
        "INSERT INTO payments (employee_id, week_start, amount, note, paid_on) VALUES (?, ?, ?, ?, ?)",
        (employee_id, week_start, amount, data.get("note", ""), now_iso()),
    )
    db.commit()
    row = db.execute("SELECT * FROM payments WHERE id=?", (cur.lastrowid,)).fetchone()
    return jsonify(dict(row)), 201


@app.route("/api/payments/<int:payment_id>", methods=["DELETE"])
def delete_payment(payment_id):
    db = get_db()
    db.execute("DELETE FROM payments WHERE id=?", (payment_id,))
    db.commit()
    return jsonify({"deleted": True})


@app.route("/api/payments", methods=["GET"])
def list_payments():
    """?week_start=YYYY-MM-DD&employee_id="""
    week_start = request.args.get("week_start")
    employee_id = request.args.get("employee_id")
    db = get_db()
    query = "SELECT * FROM payments WHERE 1=1"
    params = []
    if week_start:
        query += " AND week_start=?"
        params.append(week_start)
    if employee_id:
        query += " AND employee_id=?"
        params.append(employee_id)
    query += " ORDER BY paid_on DESC"
    rows = db.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
else:
    # also initialize when imported by a WSGI server (gunicorn, etc.)
    init_db()
