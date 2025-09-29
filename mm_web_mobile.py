import os, io, datetime, sqlite3, tempfile
from collections import defaultdict
from flask import Flask, render_template, request, redirect, url_for, session, send_file, flash
from jinja2 import DictLoader

# ---------------- Helpers ----------------
MM_DIGITS = "၀၁၂၃၄၅၆၇၈၉"
EN_DIGITS = "0123456789"

def to_myanmar_num(n):
    return str(n).translate(str.maketrans(EN_DIGITS, MM_DIGITS))

def mmize(s: str) -> str:
    if s is None:
        return ""
    return str(s).translate(str.maketrans(EN_DIGITS, MM_DIGITS))

def en_number_string(s: str) -> str:
    if s is None:
        return ""
    s = str(s)
    trans = str.maketrans(MM_DIGITS + ",", EN_DIGITS + ",")
    return s.translate(trans).replace(",", "").strip()

def format_amount(n):
    try:
        return f"{int(round(float(n))):,}"
    except Exception:
        return str(n)

def format_amount_mm(n):
    return mmize(format_amount(n))

def date_no_zeroes(d: datetime.date) -> str:
    return f"{d.day}-{d.month}-{d.year}"

def find_myanmar_ttf():
    candidates_names = [
        "pyidaungsu", "noto sans myanmar", "myanmar text", "myanmar mn", "noto sans myanmar ui"
    ]
    search_dirs = []
    if os.name == "nt":
        search_dirs += [r"C:\Windows\Fonts"]
    search_dirs += ["/Library/Fonts", "/System/Library/Fonts", os.path.expanduser("~/Library/Fonts")]
    search_dirs += ["/usr/share/fonts", "/usr/local/share/fonts", os.path.expanduser("~/.fonts")]

    for base in search_dirs:
        if not os.path.isdir(base):
            continue
        for root, _, files in os.walk(base):
            for fn in files:
                ext = os.path.splitext(fn)[1].lower()
                if ext not in (".ttf", ".otf"):
                    continue
                low = fn.lower()
                if any(name in low for name in candidates_names):
                    return os.path.join(root, fn)
    return None

# ---------------- Database ----------------
# On Vercel (serverless), write to /tmp (ephemeral). Locally, use file in project root.
if os.environ.get("VERCEL"):
    DB_FILE = os.path.join(tempfile.gettempdir(), "expense.db")
else:
    DB_FILE = os.environ.get("DB_FILE", "expense.db")

class Database:
    def __init__(self, path=DB_FILE):
        # Ensure parent dir exists (for local custom paths)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        cur = self.conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE,
                password TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                date TEXT,
                description TEXT,
                amount REAL,
                note TEXT DEFAULT ''
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS incomes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                date TEXT,
                description TEXT,
                amount REAL,
                note TEXT DEFAULT ''
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS month_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                year INTEGER,
                month INTEGER,
                total REAL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()
        cur.execute("SELECT COUNT(*) FROM users")
        if cur.fetchone()[0] == 0:
            cur.execute("INSERT INTO users(username,password) VALUES(?,?)", ("admin","1234"))
            self.conn.commit()

    # users
    def create_user(self, username, password):
        u = (username or "").strip()
        p = (password or "")
        if not u or not p:
            return False, "Username / Password ထည့်ပါ"
        try:
            self.conn.execute("INSERT INTO users(username, password) VALUES(?, ?)", (u, p))
            self.conn.commit()
            return True, None
        except sqlite3.IntegrityError:
            return False, "ဤ Username နာမည်ရှိပြီးသား ဖြစ်နေပါသည်"

    def verify_user(self, username, password):
        row = self.conn.execute("SELECT id FROM users WHERE username=? AND password=?", (username,password)).fetchone()
        return row[0] if row else None

    # expenses
    def add_expense(self, user_id, date_str, desc, amount, note=""):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO expenses(user_id,date,description,amount,note) VALUES(?,?,?,?,?)",
                    (user_id,date_str,desc,amount,note))
        self.conn.commit()
        return cur.lastrowid

    def update_expense(self, exp_id, desc, amount, note=""):
        self.conn.execute("UPDATE expenses SET description=?, amount=?, note=? WHERE id=?",
                          (desc,amount,note,exp_id))
        self.conn.commit()

    def delete_expense(self, exp_id):
        self.conn.execute("DELETE FROM expenses WHERE id=?", (exp_id,))
        self.conn.commit()

    def get_expenses_by_month(self, user_id, year:int, month:int):
        return self.conn.execute("""SELECT id,date,description,amount,note
                       FROM expenses
                       WHERE user_id=? AND strftime('%Y',date)=? AND strftime('%m',date)=?
                       ORDER BY date ASC""",
                    (user_id,str(year),f"{month:02d}")).fetchall()

    def get_total_expenses_by_month(self, user_id, year:int, month:int):
        row = self.conn.execute("""SELECT SUM(amount) FROM expenses
                       WHERE user_id=? AND strftime('%Y',date)=? AND strftime('%m',date)=?""",
                    (user_id,str(year),f"{month:02d}")).fetchone()
        return row[0] or 0.0

    # incomes
    def add_income(self, user_id, date_str, amount, desc="Income", note=""):
        cur = self.conn.cursor()
        cur.execute("INSERT INTO incomes(user_id,date,description,amount,note) VALUES(?,?,?,?,?)",
                    (user_id, date_str, desc, amount, note))
        self.conn.commit()
        return cur.lastrowid

    def update_income(self, inc_id, amount, desc="Income", note=""):
        self.conn.execute("UPDATE incomes SET description=?, amount=?, note=? WHERE id=?",
                          (desc, amount, note, inc_id))
        self.conn.commit()

    def delete_income(self, inc_id):
        self.conn.execute("DELETE FROM incomes WHERE id=?", (inc_id,))
        self.conn.commit()

    def get_incomes_by_month(self, user_id, year:int, month:int):
        return self.conn.execute("""SELECT id,date,description,amount,note
                       FROM incomes
                       WHERE user_id=? AND strftime('%Y',date)=? AND strftime('%m',date)=?
                       ORDER BY date ASC""",
                    (user_id, str(year), f"{month:02d}")).fetchall()

    # month summary
    def close_month(self, user_id, year:int, month:int):
        row = self.conn.execute("""SELECT SUM(amount) FROM expenses
                       WHERE user_id=? AND strftime('%Y',date)=? AND strftime('%m',date)=?""",
                    (user_id,str(year),f"{month:02d}")).fetchone()
        total = row[0] or 0.0
        self.conn.execute("""INSERT INTO month_summary(user_id,year,month,total) VALUES(?,?,?,?)""",
                    (user_id,year,month,total))
        self.conn.commit()
        return total

    def get_month_summary(self, user_id):
        return self.conn.execute("""SELECT id,year,month,total
                       FROM month_summary
                       WHERE user_id=?
                       ORDER BY year DESC, month DESC""", (user_id,)).fetchall()

# ---------------- Flask app ----------------
app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "change-me-secret")
db = Database()

APP_FOOTER = "Developed by: Ko Phyo (NaYaKha), Updated at 28/09/2025, Version 1.1.6/"

# ---------------- Templates (inline) ----------------
BASE_HTML = """<!doctype html>
<html lang="my">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ငွေအသုံးစာရင်းမှတ်တမ်း</title>

  <!-- Fonts / CSS -->
  <link href="https://fonts.googleapis.com/css2?family=Noto+Sans+Myanmar:wght@400;600;700&display=swap" rel="stylesheet">
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">

  <!-- PWA: manifest + theme color -->
  <link rel="manifest" href="{{ url_for('static', filename='manifest.json') }}">
  <meta name="theme-color" content="#4CAF50">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <link rel="apple-touch-icon" href="/static/icons/icon-192.png">

  <style>
    body{ font-family:'Noto Sans Myanmar', system-ui, -apple-system, Segoe UI, Roboto, sans-serif; }
    .footer-bar{ position:fixed; left:0; right:0; bottom:0; background:#0f172a; color:#e5e7eb; text-align:center; padding:6px 10px; font-size:13px; z-index:1000; border-top:1px solid #1f2937; }
    main.container{ padding-bottom:56px; }
    .amount-mm{ font-variant-numeric: tabular-nums; letter-spacing:.3px; }
  </style>
</head>
<body>
  <nav class="navbar navbar-expand-lg bg-body-tertiary border-bottom">
    <div class="container-fluid">
      <a class="navbar-brand fw-semibold" href="{{ url_for('daily') }}">ငွေအသုံးစာရင်း</a>
      <div class="d-flex gap-2">
        {% if session.get('user_id') %}
          <a class="btn btn-sm btn-outline-secondary" href="{{ url_for('summary') }}">လစဉ်စာရင်း</a>
          <a class="btn btn-sm btn-outline-danger" href="{{ url_for('logout') }}">Logout</a>
        {% else %}
          <a class="btn btn-sm btn-outline-primary" href="{{ url_for('login') }}">Login</a>
        {% endif %}
      </div>
    </div>
  </nav>

  <main class="container my-3">
    {% with messages = get_flashed_messages(with_categories=true) %}
      {% if messages %}
        <div class="mb-2">
        {% for cat, msg in messages %}
          <div class="alert alert-{{ 'success' if cat=='ok' else 'danger' }} py-2">{{ msg }}</div>
        {% endfor %}
        </div>
      {% endif %}
    {% endwith %}
    {% block content %}{% endblock %}
  </main>

  <footer class="footer-bar">{{ footer }}</footer>

  <!-- JS -->
  <script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>

  <!-- PWA: service worker registration -->
  <script>
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', function () {
        navigator.serviceWorker.register('{{ url_for('static', filename='service-worker.js') }}')
          .then(function (reg) { console.log('Service Worker registered', reg); })
          .catch(function (err) { console.log('Service Worker registration failed', err); });
      });
    }
  </script>
</body>
</html>
"""

LOGIN_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="row justify-content-center">
  <div class="col-md-6 col-lg-5">
    <div class="card shadow-sm">
      <div class="card-body p-4">
        <h4 class="mb-1 fw-bold">ငွေအသုံးစာရင်းမှတ်တမ်း</h4>
        <div class="text-muted mb-3">Welcome back! Sign in to continue.</div>
        <form method="post" action="{{ url_for('do_login') }}" class="vstack gap-3">
          <div>
            <label class="form-label">Username</label>
            <input name="username" class="form-control" required>
          </div>
          <div>
            <label class="form-label">Password</label>
            <input name="password" type="password" class="form-control" required>
          </div>
          <div class="d-grid gap-2">
            <button class="btn btn-primary">Login</button>
            <a class="btn btn-outline-secondary" href="{{ url_for('signup') }}">Create Account</a>
          </div>
        </form>
      </div>
    </div>
  </div>
</div>
{% endblock %}
"""

SIGNUP_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="row justify-content-center">
  <div class="col-md-6 col-lg-5">
    <div class="card shadow-sm">
      <div class="card-body p-4">
        <h4 class="mb-3 fw-bold">Create your account</h4>
        <form method="post" action="{{ url_for('do_signup') }}" class="vstack gap-3">
          <div>
            <label class="form-label">New Username</label>
            <input name="username" class="form-control" required>
          </div>
          <div>
            <label class="form-label">Password</label>
            <input name="password" type="password" class="form-control" required>
          </div>
          <div>
            <label class="form-label">Confirm Password</label>
            <input name="confirm" type="password" class="form-control" required>
          </div>
          <div class="d-grid">
            <button class="btn btn-primary">Create Account</button>
          </div>
        </form>
      </div>
    </div>
  </div>
</div>
{% endblock %}
"""

DAILY_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="card shadow-sm mb-3">
  <div class="card-body">
    <form method="post" action="{{ url_for('set_month') }}" class="d-flex align-items-center gap-2 flex-wrap">
      <button name="action" value="prev" class="btn btn-outline-secondary btn-sm">← Previous</button>
      <input type="month" class="form-control form-control-sm" style="max-width: 200px" name="ym" value="{{ '%04d-%02d'|format(y, m) }}">
      <button class="btn btn-outline-secondary btn-sm">Go</button>
      <button name="action" value="next" class="btn btn-outline-secondary btn-sm">Next →</button>
      <div class="ms-auto fw-bold">ယခုလ သုံးငွေ စုစုပေါင်း: <span class="text-primary">{{ month_total }}</span> ကျပ်</div>
    </form>
  </div>
</div>

<div class="card shadow-sm mb-3">
  <div class="card-body">
    <form method="post" class="row g-2 align-items-end">
      <div class="col-md-2">
        <label class="form-label">နေ့စွဲ</label>
        <input type="date" name="date" value="{{ today_date }}" class="form-control" required>
      </div>
      <div class="col-md-2">
        <label class="form-label">အချိန်</label>
        <input type="time" name="time" value="{{ now_time }}" class="form-control">
      </div>
      <div class="col-md-2">
        <label class="form-label">ပမာဏ</label>
        <input type="text" name="amount" class="form-control" placeholder="10,000" required>
      </div>
      <div class="col-md-3">
        <label class="form-label">အကြောင်းအရာ</label>
        <input type="text" name="desc" class="form-control" placeholder="Coffee / Lunch / ...">
      </div>
      <div class="col-md-3">
        <label class="form-label">မှတ်ချက် (optional)</label>
        <input type="text" name="note" class="form-control">
      </div>
      <div class="col-md-12 d-flex gap-2 mt-2">
        <button class="btn btn-primary" formaction="{{ url_for('add_expense') }}">သုံးငွေမှတ်မယ်</button>
        <button class="btn btn-success" formaction="{{ url_for('add_income') }}">ဝင်ငွေမှတ်မယ်</button>
        <span class="ms-auto">
          <!-- Important: don't validate amount/desc when closing month -->
          <button formaction="{{ url_for('close_month') }}" formnovalidate class="btn btn-outline-danger">လချုပ်စာရင်း ချုပ်မယ်</button>
        </span>
      </div>
    </form>
  </div>
</div>

<div class="card shadow-sm">
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-sm table-bordered align-middle mb-0 table-hover">
        <thead class="table-light text-center">
          <tr>
            <th style="width:60px;">စဉ်</th>
            <th style="width:120px;">ရက်စွဲ</th>
            <th style="width:90px;">အချိန်</th>
            <th class="text-center">အကြောင်းအရာ</th>
            <th style="width:140px;">ဝင်ငွေ</th>
            <th style="width:140px;">သုံးငွေ</th>
            <th style="width:160px;">လက်ကျန်ငွေ</th>
            <th style="width:150px;">မှတ်ချက်</th>
            <th style="width:110px;"></th>
          </tr>
        </thead>
        <tbody>
          {% for r in rows %}
            {% if r.kind == 'T' %}
              <tr class="text-secondary">
                <td colspan="9" class="text-center fst-italic">{{ r.desc }}</td>
              </tr>
            {% else %}
              <tr class="{% if r.highlight %}fw-bold table-warning{% endif %}">
                <td class="text-center">{{ r.no }}</td>
                <td class="text-center">{{ r.date }}</td>
                <td class="text-center">{{ r.time }}</td>
                <td class="text-center">{{ r.desc }}</td>
                <td class="text-end amount-mm">{{ r.income }}</td>
                <td class="text-end amount-mm">{{ r.expense }}</td>
                <td class="text-end amount-mm">{{ r.balance }}</td>
                <td class="text-center">{{ r.note }}</td>
                <td class="text-center">
                  <a class="btn btn-sm btn-outline-primary" href="{{ url_for('edit_entry', kind=r.kind, rid=r.key.split('-')[1]|int) }}">Edit</a>
                  <form method="post" action="{{ url_for('delete_entry') }}" style="display:inline-block" onsubmit="return confirm('ဖျက်မည်လား?')">
                    <input type="hidden" name="key" value="{{ r.key }}">
                    <button class="btn btn-sm btn-outline-danger">Delete</button>
                  </form>
                </td>
              </tr>
            {% endif %}
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}
"""

SUMMARY_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="card shadow-sm">
  <div class="card-body">
    <div class="table-responsive">
      <table class="table table-sm table-bordered align-middle mb-0 table-hover">
        <thead class="table-light text-center">
          <tr>
            <th style="width:160px;">လ/နှစ်</th>
            <th style="width:220px;">စုစုပေါင်း (သုံးငွေ)</th>
            <th style="width:260px;"></th>
          </tr>
        </thead>
        <tbody>
          {% for r in items %}
          <tr>
            <td class="text-center">{{ to_myanmar_num(r['month']) }}/{{ to_myanmar_num(r['year']) }}</td>
            <td class="text-end amount-mm">{{ format_amount_mm(r['total']) }}</td>
            <td class="text-center">
              <a class="btn btn-sm btn-outline-primary" href="{{ url_for('month_detail', year=r['year'], month=r['month']) }}">View</a>
              <a class="btn btn-sm btn-outline-success" href="{{ url_for('export_txt', year=r['year'], month=r['month']) }}">Export TXT</a>
              <a class="btn btn-sm btn-outline-danger" href="{{ url_for('export_pdf', year=r['year'], month=r['month']) }}">Export PDF</a>
            </td>
          </tr>
          {% else %}
          <tr><td colspan="3" class="text-center text-muted">မည်သည့် လချုပ်မှတ်တမ်းမျှ မရှိသေးပါ</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}
"""

MONTH_DETAIL_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="d-flex align-items-center mb-2">
  <h5 class="mb-0">လစဉ်အသေးစိတ် — {{ '%02d'|format(m) }}/{{ y }}</h5>
  <div class="ms-auto">
    <a class="btn btn-sm btn-outline-success" href="{{ url_for('export_txt', year=y, month=m) }}">Export TXT</a>
    <a class="btn btn-sm btn-outline-danger" href="{{ url_for('export_pdf', year=y, month=m) }}">Export PDF</a>
  </div>
</div>
<div class="card shadow-sm">
  <div class="card-body p-0">
    <div class="table-responsive">
      <table class="table table-sm table-bordered align-middle mb-0 table-hover">
        <thead class="table-light text-center">
          <tr>
            <th style="width:60px;">စဉ်</th>
            <th style="width:120px;">ရက်စွဲ</th>
            <th style="width:90px;">အချိန်</th>
            <th class="text-center">အကြောင်းအရာ</th>
            <th style="width:140px;">ဝင်ငွေ</th>
            <th style="width:140px;">သုံးငွေ</th>
            <th style="width:160px;">လက်ကျန်ငွေ</th>
            <th style="width:150px;">မှတ်ချက်</th>
          </tr>
        </thead>
        <tbody>
          {% for r in rows %}
            {% if r.kind == 'T' %}
              <tr class="text-secondary">
                <td colspan="8" class="text-center fst-italic">{{ r.desc }}</td>
              </tr>
            {% else %}
              <tr>
                <td class="text-center">{{ r.no }}</td>
                <td class="text-center">{{ r.date }}</td>
                <td class="text-center">{{ r.time }}</td>
                <td class="text-center">{{ r.desc }}</td>
                <td class="text-end amount-mm">{{ r.income }}</td>
                <td class="text-end amount-mm">{{ r.expense }}</td>
                <td class="text-end amount-mm">{{ r.balance }}</td>
                <td class="text-center">{{ r.note }}</td>
              </tr>
            {% endif %}
          {% endfor %}
        </tbody>
      </table>
    </div>
  </div>
</div>
{% endblock %}
"""

EDIT_HTML = """
{% extends "base.html" %}
{% block content %}
<div class="row justify-content-center">
  <div class="col-md-7 col-lg-6">
    <div class="card shadow-sm">
      <div class="card-body p-4">
        <h5 class="fw-bold mb-3">{{ 'Income' if kind=='I' else 'Expense' }} ကိုပြင်ရန်</h5>
        <form method="post" class="vstack gap-3">
          <div>
            <label class="form-label">အကြောင်းအရာ</label>
            <input name="desc" class="form-control" value="{{ r['description'] }}">
          </div>
          <div>
            <label class="form-label">ပမာဏ</label>
            <input name="amount" class="form-control" value="{{ r['amount'] }}">
          </div>
          <div>
            <label class="form-label">မှတ်ချက် (optional)</label>
            <input name="note" class="form-control" value="{{ r['note'] }}">
          </div>
          <div class="d-grid">
            <button class="btn btn-primary">Save</button>
          </div>
        </form>
      </div>
    </div>
  </div>
</div>
{% endblock %}
"""

# Register templates
app.jinja_loader = DictLoader({
    "base.html": BASE_HTML,
    "login.html": LOGIN_HTML,
    "signup.html": SIGNUP_HTML,
    "daily.html": DAILY_HTML,
    "summary.html": SUMMARY_HTML,
    "month_detail.html": MONTH_DETAIL_HTML,
    "edit_entry.html": EDIT_HTML,
})

# ---------------- Helpers for routes ----------------
def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def _wrap(*a, **kw):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return fn(*a, **kw)
    return _wrap

def get_active_year_month():
    y = session.get("current_year")
    m = session.get("current_month")
    if not y or not m:
        t = datetime.date.today()
        y, m = t.year, t.month
        session["current_year"], session["current_month"] = y, m
    return int(y), int(m)

def next_month(y, m):
    return (y, m+1) if m < 12 else (y+1, 1)

def group_month_rows(user_id, year, month):
    inc = db.get_incomes_by_month(user_id, year, month)
    exp = db.get_expenses_by_month(user_id, year, month)

    def to_dt(row):
        return datetime.datetime.strptime(row["date"], "%Y-%m-%d %H:%M")

    inc_by_day, exp_by_day, days = defaultdict(list), defaultdict(list), set()
    for r in inc:
        dt = to_dt(r); d = dt.date(); days.add(d)
        inc_by_day[d].append((r["id"], dt, r["description"], float(r["amount"]), r["note"]))
    for r in exp:
        dt = to_dt(r); d = dt.date(); days.add(d)
        exp_by_day[d].append((r["id"], dt, r["description"], float(r["amount"]), r["note"]))
    for d in inc_by_day: inc_by_day[d].sort(key=lambda x: x[1])
    for d in exp_by_day: exp_by_day[d].sort(key=lambda x: x[1])
    all_days = sorted(days)

    rows = []
    running_balance = 0.0
    last_key = session.get("last_kind_id")  # e.g. "I-5"
    for d in all_days:
        per_no = 1
        balance = running_balance
        day_exp_total = 0.0

        # incomes
        for rid, dt, desc, amt, note in inc_by_day.get(d, []):
            balance += amt
            key = f"I-{rid}"
            rows.append(dict(kind="I", key=key,
                             no=to_myanmar_num(per_no),
                             date=mmize(dt.strftime("%d-%m-%Y")),
                             time=mmize(dt.strftime("%H:%M")),
                             desc=desc or "Income",
                             income=format_amount_mm(amt),
                             expense="",
                             balance=format_amount_mm(balance),
                             note=note or "",
                             highlight=(key==last_key)))
            per_no += 1

        # expenses
        for rid, dt, desc, amt, note in exp_by_day.get(d, []):
            balance -= amt
            day_exp_total += amt
            key = f"E-{rid}"
            rows.append(dict(kind="E", key=key,
                             no=to_myanmar_num(per_no),
                             date=mmize(dt.strftime("%d-%m-%Y")),
                             time=mmize(dt.strftime("%H:%M")),
                             desc=desc,
                             income="",
                             expense=format_amount_mm(amt),
                             balance=format_amount_mm(balance),
                             note=note or "",
                             highlight=(key==last_key)))
            per_no += 1

        if inc_by_day.get(d) or exp_by_day.get(d):
            desc_total = f"{mmize(date_no_zeroes(d))} ရက်နေ့ စုစုပေါင်းသုံးငွေ ({format_amount_mm(day_exp_total)} ကျပ်)"
            rows.append(dict(kind="T", key="", no="", date="", time="",
                             desc=desc_total, income="", expense="", balance="", note="", highlight=False))
            rows.append(dict(kind="T", key="", no="", date="", time="",
                             desc="--------", income="", expense="", balance="", note="", highlight=False))

        running_balance = balance
    return rows

# ---------------- Auth routes ----------------
@app.get("/login")
def login():
    if "user_id" in session:
        return redirect(url_for("daily"))
    return render_template("login.html", footer=APP_FOOTER)

@app.post("/login")
def do_login():
    u = request.form.get("username","").strip()
    p = request.form.get("password","").strip()
    uid = db.verify_user(u,p)
    if uid:
        session.clear()
        session["user_id"] = uid
        session["username"] = u
        t = datetime.date.today()
        session["current_year"] = t.year
        session["current_month"] = t.month
        return redirect(url_for("daily"))
    flash("Username / Password မှားနေပါတယ်", "error")
    return redirect(url_for("login"))

@app.get("/signup")
def signup():
    if "user_id" in session:
        return redirect(url_for("daily"))
    return render_template("signup.html", footer=APP_FOOTER)

@app.post("/signup")
def do_signup():
    u = request.form.get("username","").strip()
    p = request.form.get("password","").strip()
    c = request.form.get("confirm","").strip()
    if not u or not p:
        flash("Username / Password ထည့်ပါ", "error"); return redirect(url_for("signup"))
    if p != c:
        flash("Confirm Password မတူပါ", "error"); return redirect(url_for("signup"))
    ok, err = db.create_user(u, p)
    if ok:
        flash("Account တင်ပြီးပါပြီ! Login ပြန်ဝင်ပါ", "ok"); return redirect(url_for("login"))
    flash(err or "အကောင့်မတင်နိုင်ပါ", "error"); return redirect(url_for("signup"))

@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# ---------------- Daily + summary routes ----------------
@app.get("/")
@login_required
def daily():
    y = session.get("current_year") or datetime.date.today().year
    m = session.get("current_month") or datetime.date.today().month
    rows = group_month_rows(session["user_id"], y, m)
    month_total = db.get_total_expenses_by_month(session["user_id"], y, m)
    now = datetime.datetime.now()
    return render_template("daily.html", footer=APP_FOOTER, rows=rows, y=y, m=m,
                           month_total=format_amount_mm(month_total),
                           today_date=now.strftime("%Y-%m-%d"),
                           now_time=now.strftime("%H:%M"))

@app.post("/set-month")
@login_required
def set_month():
    action = request.form.get("action")
    y = int(session.get("current_year", datetime.date.today().year))
    m = int(session.get("current_month", datetime.date.today().month))
    if action == "prev":
        if m > 1: m -= 1
        else: y -= 1; m = 12
    elif action == "next":
        y, m = next_month(y, m)
    else:
        mm = request.form.get("ym")
        if mm:
            y = int(mm.split("-")[0]); m = int(mm.split("-")[1])
    session["current_year"], session["current_month"] = y, m
    return redirect(url_for("daily"))

@app.post("/add-expense")
@login_required
def add_expense():
    date = request.form.get("date")
    time = request.form.get("time") or "09:00"
    desc = (request.form.get("desc") or "").strip()
    amt = en_number_string(request.form.get("amount"))
    note = (request.form.get("note") or "").strip()
    if not desc:
        flash("သုံးငွေအကြောင်းအရာ ထည့်ပါ", "error"); return redirect(url_for("daily"))
    try:
        val = float(amt)
        rid = db.add_expense(session["user_id"], f"{date} {time}", desc, val, note)
        session["last_kind_id"] = f"E-{rid}"
        flash(f"{to_myanmar_num(int(round(val)))} ကျပ် အသစ်ထည့်ပြီးပါပြီ ✔", "ok")
    except Exception:
        flash("ပမာဏမှန်မှန်ရေးပါ", "error")
    return redirect(url_for("daily"))

@app.post("/add-income")
@login_required
def add_income():
    date = request.form.get("date")
    time = request.form.get("time") or "09:00"
    desc = (request.form.get("desc") or "").strip()
    amt = en_number_string(request.form.get("amount"))
    note = (request.form.get("note") or "").strip()
    if not desc:
        flash("ဝင်ငွေ အတွက် အကြောင်းအရာထည့်ပါ", "error"); return redirect(url_for("daily"))
    try:
        val = float(amt)
        rid = db.add_income(session["user_id"], f"{date} {time}", val, desc=desc, note=note)
        session["last_kind_id"] = f"I-{rid}"
        flash(f"Income ({desc}) {to_myanmar_num(int(round(val)))} ကျပ် ထည့်ပြီးပါပြီ ✔", "ok")
    except Exception:
        flash("ဝင်ငွေ ပမာဏမှန်မှန်ရေးပါ", "error")
    return redirect(url_for("daily"))

@app.post("/delete")
@login_required
def delete_entry():
    key = request.form.get("key")
    if not key:
        flash("စုစုပေါင်း / separator row ကို ဖျက်၍ မရပါ", "error"); return redirect(url_for("daily"))
    kind, rid = key.split("-", 1)
    try:
        if kind == "I": db.delete_income(int(rid))
        else: db.delete_expense(int(rid))
        flash("Row ဖျက်ပြီးပါပြီ ✔", "ok")
    except Exception:
        flash("ဖျက်မအောင်မြင်ပါ", "error")
    return redirect(url_for("daily"))

@app.get("/edit/<kind>/<int:rid>")
@login_required
def edit_entry(kind, rid):
    y, m = get_active_year_month()
    target = None
    if kind == "I":
        for r in db.get_incomes_by_month(session["user_id"], y, m):
            if r["id"] == rid: target = r; break
    else:
        for r in db.get_expenses_by_month(session["user_id"], y, m):
            if r["id"] == rid: target = r; break
    if not target:
        flash("မတွေ့ပါ", "error"); return redirect(url_for("daily"))
    return render_template("edit_entry.html", footer=APP_FOOTER, kind=kind, r=target)

@app.post("/edit/<kind>/<int:rid>")
@login_required
def save_edit(kind, rid):
    desc = (request.form.get("desc") or "").strip()
    amt  = en_number_string(request.form.get("amount"))
    note = (request.form.get("note") or "").strip()
    try:
        val = float(amt)
        if kind == "I":
            if not desc: desc = "Income"
            db.update_income(rid, val, desc=desc, note=note)
            session["last_kind_id"] = f"I-{rid}"
            flash(f"Income ကို ({desc}) {to_myanmar_num(int(round(val)))} ကျပ် ပြင်ပြီးပါပြီ ✔", "ok")
        else:
            if not desc:
                flash("အသုံးအကြောင်းအရာ မလွတ်ခွင့်", "error")
                return redirect(url_for("edit_entry", kind=kind, rid=rid))
            db.update_expense(rid, desc, val, note=note)
            session["last_kind_id"] = f"E-{rid}"
            flash(f"{to_myanmar_num(int(round(val)))} ကျပ် ပြင်ပြီးပါပြီ ✔", "ok")
    except Exception:
        flash("ပမာဏမှန်မှန်ရေးပါ", "error")
        return redirect(url_for("edit_entry", kind=kind, rid=rid))
    return redirect(url_for("daily"))

@app.post("/close-month")
@login_required
def close_month():
    y, m = get_active_year_month()
    rows_this_month = db.get_expenses_by_month(session["user_id"], y, m)
    if not rows_this_month:
        flash("ယခု လအတွက် expense entry မရှိသေးပါ။", "error"); return redirect(url_for("daily"))
    total = db.close_month(session["user_id"], y, m)
    flash(f"{to_myanmar_num(m)}/{to_myanmar_num(y)} လ စုစုပေါင်း (သုံးငွေ): {format_amount_mm(total)} ကျပ် ✔", "ok")
    ny, nm = next_month(y, m)
    session["current_year"], session["current_month"] = ny, nm
    session.pop("last_kind_id", None)
    return redirect(url_for("daily"))

@app.get("/summary")
@login_required
def summary():
    items = db.get_month_summary(session["user_id"])
    return render_template("summary.html", footer=APP_FOOTER, items=items,
                           format_amount_mm=format_amount_mm, to_myanmar_num=to_myanmar_num)

@app.get("/month/<int:year>/<int:month>")
@login_required
def month_detail(year, month):
    rows = group_month_rows(session["user_id"], year, month)
    return render_template("month_detail.html", footer=APP_FOOTER, rows=rows, y=year, m=month)

# ---------------- Export ----------------
@app.get("/export/txt")
@login_required
def export_txt():
    y = int(request.args.get("year"))
    m = int(request.args.get("month"))
    rows = group_month_rows(session["user_id"], y, m)
    buf = io.StringIO()
    buf.write("စဉ်\tရက်စွဲ\tအချိန်\tအကြောင်းအရာ\tဝင်ငွေ\tသုံးငွေ\tလက်ကျန်ငွေ\tမှတ်ချက်\n")
    for r in rows:
        if r["kind"] == "T":
            buf.write(f"\t\t\t{r['desc']}\t\t\t\t\n")
        else:
            buf.write(f"{r['no']}\t{r['date']}\t{r['time']}\t{r['desc']}\t{r['income']}\t{r['expense']}\t{r['balance']}\t{r['note']}\n")
    data = buf.getvalue().encode("utf-8"); buf.close()
    return send_file(io.BytesIO(data), mimetype="text/plain; charset=utf-8",
                     as_attachment=True, download_name=f"summary_{y}_{m:02d}.txt")

@app.get("/export/pdf")
@login_required
def export_pdf():
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4, landscape
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.enums import TA_LEFT
    except ImportError:
        flash("PDF ထုတ်/export လုပ်ရန် 'reportlab' library လိုအပ်ပါတယ် (pip install reportlab)", "error")
        return redirect(url_for("summary"))

    y = int(request.args.get("year"))
    m = int(request.args.get("month"))
    rows = group_month_rows(session["user_id"], y, m)
    font_name = "Helvetica"
    font_path = find_myanmar_ttf()
    if font_path:
        try:
            pdfmetrics.registerFont(TTFont("MMFont", font_path)); font_name = "MMFont"
        except Exception:
            pass

    data = [["စဉ်","ရက်စွဲ","အချိန်","အကြောင်းအရာ","ဝင်ငွေ","သုံးငွေ","လက်ကျန်ငွေ","မှတ်ချက်"]]
    for r in rows:
        if r["kind"] == "T":
            data.append(["", "", "", r["desc"], "", "", "", ""])
        else:
            data.append([r["no"], r["date"], r["time"], r["desc"], r["income"], r["expense"], r["balance"], r["note"]])

    pdf = io.BytesIO()
    doc = SimpleDocTemplate(pdf, pagesize=landscape(A4), leftMargin=18, rightMargin=18, topMargin=18, bottomMargin=18)
    elements = []
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("mm-title", parent=styles["Heading2"], fontName=font_name, alignment=TA_LEFT, leading=16)
    elements.append(Paragraph(f"{to_myanmar_num(m)}/{to_myanmar_num(y)}အတွက်ဝင်ငွေ/သုံးငွေစာရင်းချုပ်", title_style))
    elements.append(Spacer(1, 8))
    col_widths = [35, 80, 60, 320, 95, 95, 110, 160]
    tbl = Table(data, repeatRows=1, colWidths=col_widths)
    ts = TableStyle([
        ('FONT', (0,0), (-1,-1), font_name),
        ('FONTSIZE', (0,0), (-1,-1), 9),
        ('ALIGN', (0,0), (2,0), 'CENTER'),
        ('ALIGN', (3,0), (3,0), 'CENTER'),
        ('ALIGN', (4,0), (6,0), 'CENTER'),
        ('ALIGN', (7,0), (7,0), 'CENTER'),
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor('#e5e7eb')),
        ('TEXTCOLOR', (0,0), (-1,0), colors.HexColor('#111827')),
        ('LINEBELOW', (0,0), (-1,0), 0.8, colors.HexColor('#9ca3af')),
        ('ALIGN', (0,1), (2,-1), 'CENTER'),
        ('ALIGN', (3,1), (3,-1), 'CENTER'),
        ('ALIGN', (4,1), (6,-1), 'RIGHT'),
        ('ALIGN', (7,1), (7,-1), 'CENTER'),
        ('INNERGRID', (0,0), (-1,-1), 0.25, colors.HexColor('#d1d5db')),
        ('BOX', (0,0), (-1,-1), 0.5, colors.HexColor('#9ca3af')),
    ])
    tbl.setStyle(ts); elements.append(tbl); doc.build(elements)
    pdf.seek(0)
    return send_file(pdf, mimetype="application/pdf", as_attachment=True,
                     download_name=f"summary_{y}_{m:02d}.pdf")

# ---------------- Local run (not used on Vercel) ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
