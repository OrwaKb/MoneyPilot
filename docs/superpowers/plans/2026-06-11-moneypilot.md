# MoneyPilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build MoneyPilot v1 — a local Windows desktop finance cockpit with natural-language entry (Claude via the user's subscription), deterministic budget/cycle/goal math, and a dark Mission-Control UI — per the approved spec at `docs/superpowers/specs/2026-06-11-moneypilot-design.md`.

**Architecture:** Python 3.11 core (SQLite WAL storage, pure-deterministic `engine/`, AI layer that can never crash the app) bridged to an HTML/CSS/JS cockpit UI through pywebview (WebView2). Claude is reached through the Claude Agent SDK with a `claude -p` CLI fallback; both use the machine's existing Claude Code login. Every AI failure path degrades to a regex fallback parser or template briefing — entries are never lost.

**Tech Stack:** Python 3.11, pywebview ≥5, claude-agent-sdk, pydantic v2, requests, pytest. UI: vanilla HTML/CSS/JS (no framework). Storage: SQLite at `%LOCALAPPDATA%\MoneyPilot\ledger.db`, amounts as integer agorot.

**Working directory for all commands:** `C:\Users\LENOVO\OneDrive\Desktop\Project - Finance Tracker` (the git repo root). All test commands use the project venv: `.venv\Scripts\python -m pytest …`.

---

## Interface map (signatures used across tasks — keep these exact)

```
app/models.py      to_agorot(x) -> int · fmt_ils(agorot) -> str · class ParsedTxn(BaseModel)
app/db.py          connect(path) · init_db(conn) · get_setting/set_setting/get_settings
                   categories(conn) · category_id_by_name(conn, name)
                   add_transaction(conn, **kw) -> int · update_transaction · list_transactions
                   soft_delete_transaction · undelete_transaction
                   add_rule · match_rule(conn, text) -> int|None · list_rules
                   add_goal · update_goal · list_goals · get_goal_by_name
                   set_budget · get_budgets(conn) -> dict[int, int]
                   get_briefing(conn, date_iso) · put_briefing(conn, date_iso, text, fp_json)
                   add_chat · recent_chat(conn, n)
                   export_json(conn) -> dict · import_json(conn, data) · write_daily_backup(conn, dir, today)
app/engine/cycles.py    clamped_date(y, m, d) · salary_cycle(today, salary_day) -> dict
                        card_window(today, charge_day) -> dict
app/engine/budget.py    safe_to_spend(conn, today) -> dict · category_status(conn, today) -> list
                        card_accrual(conn, today) -> dict · cycle_net(conn, start, end) -> (income, expenses)
app/engine/goals.py     goal_report(conn, today) -> list · monthly_savings_pace(conn, today) -> int
app/engine/fx.py        get_rates(conn, today) -> dict · to_ils(amount, currency, rates) -> (agorot, rate)
app/engine/insights.py  fact_pack(conn, today) -> dict
app/ai/client.py        ask_claude(prompt, system=None, timeout_s=60) -> str · AIUnavailable · extract_json(text, opener)
app/ai/parser.py        fallback_parse(text, today) -> list[ParsedTxn]
                        parse_and_store(conn, text, today) -> dict · resweep(conn, today) -> int
app/ai/advisor.py       get_briefing(conn, today, force=False) -> dict · template_briefing(fp) -> str
                        chat(conn, text, today) -> dict · apply_action(conn, action, today) -> dict
                        onboarding_propose(conn, braindump, today) -> dict
app/api.py              class Api  (pywebview js_api — one method per UI command)
```

Conventions: dates stored as ISO strings; `amount_agorot` signed (income +, expense −, goal_contribution −); expense sums in reports use absolute values; "discretionary" = expense categories with `is_fixed=0`.

> **Amendments (code review, Task 2):** `to_agorot` raises `ValueError` (not
> `decimal.InvalidOperation`) on junk input. `ParsedTxn.amount` rejects inf/NaN
> (`allow_inf_nan=False`). `ParsedTxn.currency` normalizes aliases
> (NIS/SHEKEL/₪→ILS, $→USD, €→EUR) and **raises** on non-3-letter codes instead
> of truncating — invalid currency now engages the Task 13 AI repair loop as a
> `ValidationError`.

> **Amendments (code review, Task 3):** `init_db` stamps `schema_version` with
> `INSERT OR IGNORE` (not REPLACE) so a future migration runner can read the DB's
> true version. The `transactions` table has a CHECK enforcing the sign convention
> (income > 0; expense/goal_contribution < 0); `category_rules.created_from_txn`
> now has `REFERENCES transactions(id)`. **Task 15 note:** every `Api` method that
> writes (including `startup`'s `resweep`, `chat_send`, and `get_briefing`'s
> `put_briefing`) must hold `self._lock` — not only the entry/edit paths; and
> `save_settings` must skip `None` values (else `str(None)` would store "None").

> **Amendments (code review, Task 4):** `update_transaction` is a no-op when no
> fields are passed (Task 15's `update_txn` can produce an empty dict). `add_rule`
> raises `ValueError` on a blank pattern — a stored "" would substring-match every
> entry and silently hijack Task 13's rule fast path. Explicit `created_at=` is
> ISO-normalized via `_iso`. **Task 15 note:** `onboarding_complete`'s
> `with self.conn:` does NOT make the helper-based writes atomic — every db helper
> autocommits internally; either perform onboarding writes as raw statements in one
> explicit transaction or document best-effort semantics and make the handler
> idempotent.

> **Amendments (code review, Task 5):** `import_json` validates the payload
> (schema_version match + non-empty categories) before deleting anything —
> `--restore` with a wrong file must error, not wipe the ledger. `add_goal`
> raises `ValueError` on a duplicate active goal name (case-insensitive) so
> `goal_name` resolution stays unambiguous. `write_daily_backup` writes via a
> temp file + `os.replace` (atomic; safe under OneDrive sync) and tolerates
> prune unlink errors. `update_goal` no-ops on an empty field dict.
> `get_goal_by_name`'s LIKE fallback orders by `LENGTH(name), id`.

> **Amendments (code review, Task 6):** `salary_cycle`/`card_window` validate the
> day-of-month (1..31) and raise `ValueError` otherwise — day 32 was silently
> treated as 31. `salary_cycle` now documents its dict contract (end INCLUSIVE,
> day_index 1-based, days_left counts today). `db.add_goal` stores the stripped
> goal name. **Tasks 14/15 note:** validate `salary_day`/`card_charge_day` as
> ints in 1..31 at write time too — in `onboarding_complete` and in the advisor's
> `adjust_setting` action (LLM-produced values must not be stored verbatim).

> **Amendments (code review, Task 7):** `category_status` reports
> `pace_ratio: None` (JSON `null`) for a category with spend but no budget —
> 0.0 read as "healthy/green" in the UI and kept the offline briefing silent
> about unplanned spend. Consumers (Tasks 10/16/18): `null > 1.1` is false in
> JS so amber logic needs no change, but render unbudgeted rows distinctly
> (e.g. "unbudgeted" tag) rather than a 0% green bar. Keep fact-pack values
> strictly JSON-safe — never use `inf`/`NaN` sentinels. Known accepted noise:
> pace_ratio is linear proration, so day-1/2 lumpy spends flare amber and
> self-correct within days.

> **Amendments (code review, Task 8):** A dateless `save_by_date` goal now gets
> the "₪X to go" verdict instead of permanently "behind"; `goal_report` computes
> `monthly_savings_pace` once, not per goal. **Known limitation (multi-goal):**
> each dated goal's "on track" compares the FULL savings pace against its own
> need — two goals can both claim the same surplus. **Task 10 must add**
> `total_pace_needed_agorot` (sum of dated goals' pace_needed) next to
> `monthly_savings_pace_agorot` in the fact-pack, and **Task 11's briefing/chat
> system prompts must say**: "savings pace is shared across all goals — if the
> sum of pace_needed exceeds the pace, point out the conflict." Accepted v1
> behavior: with no completed cycle, pace falls back to the current cycle's
> net-so-far, which is optimistic right after salary day.

---

### Task 1: Project scaffolding & test harness

**Files:**
- Create: `requirements.txt`, `pytest.ini`, `app/__init__.py`, `app/engine/__init__.py`, `app/ai/__init__.py`, `tests/conftest.py`, `tests/test_smoke.py`

- [ ] **Step 1: Create the package skeleton and config files**

`requirements.txt`:
```
pywebview>=5.1
claude-agent-sdk>=0.1.0
pydantic>=2.7
requests>=2.32
pytest>=8.2
```

`pytest.ini`:
```ini
[pytest]
testpaths = tests
pythonpath = .
```

`app/__init__.py`, `app/engine/__init__.py`, `app/ai/__init__.py` — all empty files.

`tests/conftest.py` (fixtures used by every later task — `db` doesn't exist yet, that's fine, the smoke test doesn't import it):
```python
import datetime as dt

import pytest

TODAY = dt.date(2026, 6, 11)  # Thursday; salary_day=10 → cycle started 2026-06-10


@pytest.fixture
def conn(tmp_path):
    from app import db
    c = db.connect(tmp_path / "test.db")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    from app import db
    db.set_setting(conn, "user_name", "Tester")
    db.set_setting(conn, "salary_day", "10")
    db.set_setting(conn, "salary_amount_agorot", "900000")   # ₪9,000
    db.set_setting(conn, "card_charge_day", "2")
    db.set_setting(conn, "opening_balance_agorot", "500000") # ₪5,000
    db.set_setting(conn, "opening_balance_date", "2026-06-01")
    for name, amt in [("Food out", 60000), ("Groceries", 120000),
                      ("Transport", 40000), ("Bills", 250000), ("Fun", 40000)]:
        db.set_budget(conn, db.category_id_by_name(conn, name), amt)
    return conn
```

`tests/test_smoke.py`:
```python
def test_python_version():
    import sys
    assert sys.version_info >= (3, 11)
```

- [ ] **Step 2: Create the venv and install dependencies**

Run:
```powershell
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
Expected: pip finishes without errors. (If `claude-agent-sdk` is unavailable on PyPI for any reason, remove that line and continue — `ai/client.py` has a CLI fallback and tests mock the SDK.)

- [ ] **Step 3: Run the smoke test**

Run: `.venv\Scripts\python -m pytest tests\test_smoke.py -v`
Expected: `1 passed`

- [ ] **Step 4: Commit**

```powershell
git add -A; git commit -m "chore: project scaffolding, venv, pytest harness"
```

---

### Task 2: Money primitives & AI output schema (`app/models.py`)

**Files:**
- Create: `app/models.py`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py`:
```python
import datetime as dt

import pytest
from pydantic import ValidationError

from app.models import ParsedTxn, fmt_ils, to_agorot


def test_to_agorot_int():
    assert to_agorot(45) == 4500

def test_to_agorot_float_half_up():
    assert to_agorot("10.255") == 1026
    assert to_agorot(10.25) == 1025

def test_fmt_ils_whole():
    assert fmt_ils(4500) == "₪45"

def test_fmt_ils_cents_and_thousands():
    assert fmt_ils(123456) == "₪1,234.56"

def test_fmt_ils_negative():
    assert fmt_ils(-4500) == "-₪45"

def test_parsed_txn_defaults():
    p = ParsedTxn(effective_date=dt.date(2026, 6, 11), amount=45,
                  description="falafel")
    assert p.direction == "expense" and p.currency == "ILS"
    assert p.payment_method == "card" and p.confidence == 1.0

def test_parsed_txn_rejects_nonpositive_amount():
    with pytest.raises(ValidationError):
        ParsedTxn(effective_date=dt.date(2026, 6, 11), amount=0, description="x")

def test_parsed_txn_rejects_bad_direction():
    with pytest.raises(ValidationError):
        ParsedTxn(effective_date=dt.date(2026, 6, 11), amount=1,
                  description="x", direction="loan")

def test_parsed_txn_normalizes_currency():
    p = ParsedTxn(effective_date=dt.date(2026, 6, 11), amount=1,
                  description="x", currency="usd")
    assert p.currency == "USD"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models'`

- [ ] **Step 3: Implement `app/models.py`**

```python
from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Direction = Literal["expense", "income", "goal_contribution"]
PayMethod = Literal["card", "cash", "transfer"]


def to_agorot(amount) -> int:
    """Money in → integer agorot, banker-proof (HALF_UP on the agora)."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"),
               rounding=ROUND_HALF_UP))


def fmt_ils(agorot: int) -> str:
    sign = "-" if agorot < 0 else ""
    shekels, ag = divmod(abs(agorot), 100)
    base = f"{sign}₪{shekels:,}"
    return base if ag == 0 else f"{base}.{ag:02d}"


class ParsedTxn(BaseModel):
    """One transaction as returned by the AI parser (or fallback parser)."""
    effective_date: dt.date
    amount: float = Field(gt=0)          # in currency units, always positive
    currency: str = "ILS"
    direction: Direction = "expense"
    category: str = "Other"
    description: str = ""
    merchant: Optional[str] = None
    people: Optional[str] = None
    payment_method: PayMethod = "card"
    goal_name: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("currency")
    @classmethod
    def _norm_currency(cls, v: str) -> str:
        return v.strip().upper()[:3]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_models.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```powershell
git add app\models.py tests\test_models.py; git commit -m "feat: money primitives and ParsedTxn schema"
```

---

### Task 3: Database — connection, schema, seeds, settings (`app/db.py`)

**Files:**
- Create: `app/db.py`
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_db.py`:
```python
from app import db


def test_init_seeds_categories(conn):
    cats = db.categories(conn)
    names = {c["name"] for c in cats}
    assert "Food out" in names and "Salary" in names
    assert len(cats) == 12

def test_bills_is_fixed(conn):
    bills = [c for c in db.categories(conn) if c["name"] == "Bills"][0]
    assert bills["is_fixed"] == 1

def test_init_is_idempotent(conn):
    db.init_db(conn)  # second call must not duplicate seeds
    assert len(db.categories(conn)) == 12

def test_settings_roundtrip(conn):
    db.set_setting(conn, "salary_day", "10")
    assert db.get_setting(conn, "salary_day") == "10"
    assert db.get_setting(conn, "missing", "dflt") == "dflt"
    assert db.get_settings(conn)["salary_day"] == "10"

def test_category_id_by_name_case_insensitive(conn):
    assert db.category_id_by_name(conn, "food OUT") is not None
    assert db.category_id_by_name(conn, "nope") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_db.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.db'`

- [ ] **Step 3: Implement the first slice of `app/db.py`**

```python
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

DEFAULT_CATEGORIES = [
    # (name, emoji, is_income, is_fixed)
    ("Food out", "🍔", 0, 0), ("Groceries", "🛒", 0, 0), ("Transport", "⛽", 0, 0),
    ("Bills", "📄", 0, 1), ("Fun", "🎮", 0, 0), ("Health", "💊", 0, 0),
    ("Education", "🎓", 0, 0), ("Shopping", "🛍️", 0, 0), ("Gifts", "🎁", 0, 0),
    ("Other", "📦", 0, 0), ("Salary", "💰", 1, 0), ("Other income", "➕", 1, 0),
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS categories(
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
  emoji TEXT NOT NULL DEFAULT '', is_income INTEGER NOT NULL DEFAULT 0,
  is_fixed INTEGER NOT NULL DEFAULT 0, sort INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS goals(
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, emoji TEXT NOT NULL DEFAULT '🎯',
  type TEXT NOT NULL CHECK(type IN ('save_by_date','purchase_fund')),
  target_agorot INTEGER NOT NULL, target_date TEXT,
  status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS transactions(
  id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, effective_date TEXT NOT NULL,
  amount_agorot INTEGER NOT NULL,
  direction TEXT NOT NULL CHECK(direction IN ('expense','income','goal_contribution')),
  currency_orig TEXT NOT NULL DEFAULT 'ILS', amount_orig REAL, fx_rate REAL,
  category_id INTEGER REFERENCES categories(id),
  description TEXT NOT NULL DEFAULT '', merchant TEXT, people TEXT,
  payment_method TEXT NOT NULL DEFAULT 'card'
    CHECK(payment_method IN ('card','cash','transfer')),
  goal_id INTEGER REFERENCES goals(id), raw_text TEXT,
  source TEXT NOT NULL DEFAULT 'manual', ai_confidence REAL,
  needs_review INTEGER NOT NULL DEFAULT 0, deleted_at TEXT);
CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(effective_date);
CREATE TABLE IF NOT EXISTS category_rules(
  id INTEGER PRIMARY KEY, pattern TEXT NOT NULL,
  category_id INTEGER NOT NULL REFERENCES categories(id), created_from_txn INTEGER);
CREATE TABLE IF NOT EXISTS budgets(
  category_id INTEGER PRIMARY KEY REFERENCES categories(id),
  amount_agorot INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_history(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS briefings(
  date TEXT PRIMARY KEY, text TEXT NOT NULL, fact_pack_json TEXT NOT NULL);
"""


def connect(path) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: pywebview dispatches js_api calls on worker
    # threads; api.py serializes writes with a Lock.
    conn = sqlite3.connect(str(p), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    if conn.execute("SELECT COUNT(*) FROM categories").fetchone()[0] == 0:
        conn.executemany(
            "INSERT INTO categories(name, emoji, is_income, is_fixed, sort)"
            " VALUES(?,?,?,?,?)",
            [(n, e, i, f, idx) for idx, (n, e, i, f) in enumerate(DEFAULT_CATEGORIES)])
    conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES('schema_version', ?)",
                 (str(SCHEMA_VERSION),))
    conn.commit()


# --- settings -------------------------------------------------------------

def get_setting(conn, key, default=None):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default

def set_setting(conn, key, value) -> None:
    conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES(?,?)",
                 (key, str(value)))
    conn.commit()

def get_settings(conn) -> dict:
    return {r["key"]: r["value"] for r in conn.execute("SELECT * FROM settings")}


# --- categories -----------------------------------------------------------

def categories(conn) -> list:
    return list(conn.execute("SELECT * FROM categories ORDER BY sort"))

def category_id_by_name(conn, name):
    row = conn.execute("SELECT id FROM categories WHERE LOWER(name)=LOWER(?)",
                       (name.strip(),)).fetchone()
    return row["id"] if row else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_db.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```powershell
git add app\db.py tests\test_db.py; git commit -m "feat: sqlite schema, seeds, settings"
```

---

### Task 4: Database — transactions & learned rules (`app/db.py` continued)

**Files:**
- Modify: `app/db.py` (append)
- Test: `tests/test_db_transactions.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_db_transactions.py`:
```python
import datetime as dt

from app import db

D = dt.date(2026, 6, 11)


def _add(conn, **kw):
    base = dict(effective_date=D, amount_agorot=-4500, direction="expense",
                category_id=db.category_id_by_name(conn, "Food out"),
                description="falafel")
    base.update(kw)
    return db.add_transaction(conn, **base)


def test_add_and_list(conn):
    tid = _add(conn)
    rows = db.list_transactions(conn)
    assert len(rows) == 1 and rows[0]["id"] == tid
    assert rows[0]["category_name"] == "Food out"

def test_soft_delete_hides_then_undelete_restores(conn):
    tid = _add(conn)
    db.soft_delete_transaction(conn, tid)
    assert db.list_transactions(conn) == []
    assert len(db.list_transactions(conn, include_deleted=True)) == 1
    db.undelete_transaction(conn, tid)
    assert len(db.list_transactions(conn)) == 1

def test_filters(conn):
    _add(conn)
    _add(conn, effective_date=dt.date(2026, 5, 1), description="old pizza")
    assert len(db.list_transactions(conn, start=dt.date(2026, 6, 1))) == 1
    assert len(db.list_transactions(conn, text="pizza")) == 1
    assert len(db.list_transactions(conn, direction="income")) == 0

def test_update_whitelist(conn):
    tid = _add(conn)
    db.update_transaction(conn, tid, description="hummus", amount_agorot=-5000)
    row = db.list_transactions(conn)[0]
    assert row["description"] == "hummus" and row["amount_agorot"] == -5000

def test_rules_match_substring_case_insensitive(conn):
    food = db.category_id_by_name(conn, "Food out")
    db.add_rule(conn, "falafel", food)
    assert db.match_rule(conn, "45 FALAFEL with karim") == food
    assert db.match_rule(conn, "bus ticket") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_db_transactions.py -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'add_transaction'`

- [ ] **Step 3: Append to `app/db.py`**

```python
# --- transactions -----------------------------------------------------------

_TXN_FIELDS = {"effective_date", "amount_agorot", "direction", "currency_orig",
               "amount_orig", "fx_rate", "category_id", "description", "merchant",
               "people", "payment_method", "goal_id", "raw_text", "source",
               "ai_confidence", "needs_review"}


def _iso(v):
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v


def add_transaction(conn, *, created_at=None, **kw) -> int:
    bad = set(kw) - _TXN_FIELDS
    if bad:
        raise ValueError(f"unknown transaction fields: {bad}")
    kw = {k: _iso(v) for k, v in kw.items()}
    kw["created_at"] = created_at or dt.datetime.now().isoformat(timespec="seconds")
    cols = ",".join(kw)
    cur = conn.execute(
        f"INSERT INTO transactions({cols}) VALUES({','.join('?'*len(kw))})",
        tuple(kw.values()))
    conn.commit()
    return cur.lastrowid


def update_transaction(conn, txn_id: int, **kw) -> None:
    bad = set(kw) - _TXN_FIELDS
    if bad:
        raise ValueError(f"unknown transaction fields: {bad}")
    kw = {k: _iso(v) for k, v in kw.items()}
    sets = ",".join(f"{k}=?" for k in kw)
    conn.execute(f"UPDATE transactions SET {sets} WHERE id=?",
                 (*kw.values(), txn_id))
    conn.commit()


def soft_delete_transaction(conn, txn_id: int) -> None:
    conn.execute("UPDATE transactions SET deleted_at=? WHERE id=?",
                 (dt.datetime.now().isoformat(timespec="seconds"), txn_id))
    conn.commit()


def undelete_transaction(conn, txn_id: int) -> None:
    conn.execute("UPDATE transactions SET deleted_at=NULL WHERE id=?", (txn_id,))
    conn.commit()


def list_transactions(conn, *, start=None, end=None, category_id=None, text=None,
                      direction=None, payment_method=None, needs_review=None,
                      include_deleted=False, limit=None) -> list:
    q = ("SELECT t.*, c.name AS category_name, c.emoji AS category_emoji"
         " FROM transactions t LEFT JOIN categories c ON c.id=t.category_id"
         " WHERE 1=1")
    args: list = []
    if not include_deleted:
        q += " AND t.deleted_at IS NULL"
    if start:
        q += " AND t.effective_date >= ?"; args.append(_iso(start))
    if end:
        q += " AND t.effective_date <= ?"; args.append(_iso(end))
    if category_id:
        q += " AND t.category_id = ?"; args.append(category_id)
    if direction:
        q += " AND t.direction = ?"; args.append(direction)
    if payment_method:
        q += " AND t.payment_method = ?"; args.append(payment_method)
    if needs_review is not None:
        q += " AND t.needs_review = ?"; args.append(int(needs_review))
    if text:
        q += (" AND (t.description LIKE ? OR t.merchant LIKE ?"
              " OR t.raw_text LIKE ?)")
        args += [f"%{text}%"] * 3
    q += " ORDER BY t.effective_date DESC, t.id DESC"
    if limit:
        q += " LIMIT ?"; args.append(limit)
    return list(conn.execute(q, args))


# --- learned category rules -------------------------------------------------

def add_rule(conn, pattern: str, category_id: int, created_from_txn=None) -> None:
    conn.execute("INSERT INTO category_rules(pattern, category_id, created_from_txn)"
                 " VALUES(?,?,?)", (pattern.strip().lower(), category_id,
                                    created_from_txn))
    conn.commit()


def match_rule(conn, text: str):
    low = text.lower()
    for r in conn.execute("SELECT * FROM category_rules ORDER BY id DESC"):
        if r["pattern"] in low:
            return r["category_id"]
    return None


def list_rules(conn) -> list:
    return list(conn.execute(
        "SELECT r.*, c.name AS category_name FROM category_rules r"
        " JOIN categories c ON c.id=r.category_id ORDER BY r.id"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_db_transactions.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: transactions CRUD with soft delete, learned category rules"
```

---

### Task 5: Database — goals, budgets, briefings, chat, backup/restore (`app/db.py` continued)

**Files:**
- Modify: `app/db.py` (append)
- Test: `tests/test_db_rest.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_db_rest.py`:
```python
import datetime as dt
import json

from app import db

D = dt.date(2026, 6, 11)


def test_goals_crud(conn):
    gid = db.add_goal(conn, name="Drone", emoji="🚁", type="purchase_fund",
                      target_agorot=450000)
    goals = db.list_goals(conn)
    assert goals[0]["id"] == gid and goals[0]["status"] == "active"
    db.update_goal(conn, gid, status="archived")
    assert db.list_goals(conn) == []  # default: active only
    assert len(db.list_goals(conn, include_archived=True)) == 1

def test_get_goal_by_name(conn):
    db.add_goal(conn, name="Summer Trip", type="save_by_date",
                target_agorot=1000000, target_date=dt.date(2026, 8, 1))
    assert db.get_goal_by_name(conn, "summer trip") is not None
    assert db.get_goal_by_name(conn, "drone") is None

def test_budgets(conn):
    food = db.category_id_by_name(conn, "Food out")
    db.set_budget(conn, food, 60000)
    db.set_budget(conn, food, 70000)  # upsert
    assert db.get_budgets(conn)[food] == 70000

def test_briefing_cache(conn):
    assert db.get_briefing(conn, "2026-06-11") is None
    db.put_briefing(conn, "2026-06-11", "all good", "{}")
    assert db.get_briefing(conn, "2026-06-11")["text"] == "all good"

def test_chat(conn):
    db.add_chat(conn, "user", "hello")
    db.add_chat(conn, "assistant", "hi")
    rows = db.recent_chat(conn, 10)
    assert [r["role"] for r in rows] == ["user", "assistant"]  # oldest first

def test_export_import_roundtrip(conn, tmp_path):
    db.set_setting(conn, "salary_day", "10")
    db.add_goal(conn, name="G", type="purchase_fund", target_agorot=100)
    db.add_transaction(conn, effective_date=D, amount_agorot=-100,
                       direction="expense")
    data = db.export_json(conn)
    c2 = db.connect(tmp_path / "restored.db")
    db.init_db(c2)
    db.import_json(c2, data)
    assert db.get_setting(c2, "salary_day") == "10"
    assert len(db.list_transactions(c2)) == 1
    assert len(db.list_goals(c2)) == 1

def test_daily_backup_written_once_and_pruned(conn, tmp_path):
    bdir = tmp_path / "backups"
    p1 = db.write_daily_backup(conn, bdir, D)
    p2 = db.write_daily_backup(conn, bdir, D)
    assert p1 is not None and p2 is None  # second call same day: skipped
    for i in range(35):  # fabricate old backups to test pruning
        (bdir / f"ledger-2026-04-{i:02d}.json").write_text("{}", encoding="utf-8")
    db.write_daily_backup(conn, bdir, D + dt.timedelta(days=1))
    assert len(list(bdir.glob("ledger-*.json"))) == 30
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_db_rest.py -v`
Expected: FAIL — `AttributeError: module 'app.db' has no attribute 'add_goal'`

- [ ] **Step 3: Append to `app/db.py`**

```python
# --- goals -------------------------------------------------------------------

_GOAL_FIELDS = {"name", "emoji", "type", "target_agorot", "target_date", "status"}


def add_goal(conn, *, name, type, target_agorot, emoji="🎯", target_date=None) -> int:
    cur = conn.execute(
        "INSERT INTO goals(name, emoji, type, target_agorot, target_date,"
        " status, created_at) VALUES(?,?,?,?,?,'active',?)",
        (name, emoji, type, target_agorot, _iso(target_date),
         dt.datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    return cur.lastrowid


def update_goal(conn, goal_id: int, **kw) -> None:
    bad = set(kw) - _GOAL_FIELDS
    if bad:
        raise ValueError(f"unknown goal fields: {bad}")
    kw = {k: _iso(v) for k, v in kw.items()}
    sets = ",".join(f"{k}=?" for k in kw)
    conn.execute(f"UPDATE goals SET {sets} WHERE id=?", (*kw.values(), goal_id))
    conn.commit()


def list_goals(conn, include_archived=False) -> list:
    q = "SELECT * FROM goals"
    if not include_archived:
        q += " WHERE status='active'"
    return list(conn.execute(q + " ORDER BY id"))


def get_goal_by_name(conn, name: str):
    row = conn.execute(
        "SELECT * FROM goals WHERE status='active' AND LOWER(name)=LOWER(?)",
        (name.strip(),)).fetchone()
    if row:
        return row
    return conn.execute(
        "SELECT * FROM goals WHERE status='active' AND LOWER(name) LIKE ?",
        (f"%{name.strip().lower()}%",)).fetchone()


# --- budgets / briefings / chat ----------------------------------------------

def set_budget(conn, category_id: int, amount_agorot: int) -> None:
    conn.execute("INSERT OR REPLACE INTO budgets(category_id, amount_agorot)"
                 " VALUES(?,?)", (category_id, amount_agorot))
    conn.commit()


def get_budgets(conn) -> dict:
    return {r["category_id"]: r["amount_agorot"]
            for r in conn.execute("SELECT * FROM budgets")}


def get_briefing(conn, date_iso: str):
    return conn.execute("SELECT * FROM briefings WHERE date=?",
                        (date_iso,)).fetchone()


def put_briefing(conn, date_iso: str, text: str, fact_pack_json: str) -> None:
    conn.execute("INSERT OR REPLACE INTO briefings(date, text, fact_pack_json)"
                 " VALUES(?,?,?)", (date_iso, text, fact_pack_json))
    conn.commit()


def add_chat(conn, role: str, text: str) -> None:
    conn.execute("INSERT INTO chat_history(ts, role, text) VALUES(?,?,?)",
                 (dt.datetime.now().isoformat(timespec="seconds"), role, text))
    conn.commit()


def recent_chat(conn, n: int) -> list:
    rows = list(conn.execute(
        "SELECT * FROM chat_history ORDER BY id DESC LIMIT ?", (n,)))
    return rows[::-1]  # oldest first


# --- backup / restore ----------------------------------------------------------

_EXPORT_TABLES = ["categories", "goals", "transactions", "category_rules",
                  "budgets", "settings", "chat_history", "briefings"]


def export_json(conn) -> dict:
    out = {"schema_version": SCHEMA_VERSION}
    for t in _EXPORT_TABLES:
        out[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t}")]
    return out


def import_json(conn, data: dict) -> None:
    with conn:  # single transaction
        for t in reversed(_EXPORT_TABLES):
            conn.execute(f"DELETE FROM {t}")
        for t in _EXPORT_TABLES:
            rows = data.get(t, [])
            if not rows:
                continue
            cols = list(rows[0].keys())
            conn.executemany(
                f"INSERT INTO {t}({','.join(cols)})"
                f" VALUES({','.join('?'*len(cols))})",
                [tuple(r[c] for c in cols) for r in rows])
    conn.commit()


def write_daily_backup(conn, backup_dir, today: dt.date):
    """Write backups/ledger-YYYY-MM-DD.json once per day; prune to 30 files."""
    bdir = Path(backup_dir)
    bdir.mkdir(parents=True, exist_ok=True)
    target = bdir / f"ledger-{today.isoformat()}.json"
    if target.exists():
        return None
    target.write_text(json.dumps(export_json(conn), ensure_ascii=False, indent=1),
                      encoding="utf-8")
    backups = sorted(bdir.glob("ledger-*.json"))
    for old in backups[:-30]:
        old.unlink()
    return target
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_db_rest.py tests\test_db.py tests\test_db_transactions.py -v`
Expected: `17 passed`

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: goals, budgets, briefing cache, chat, JSON backup/restore"
```

---

### Task 6: Cycle engine (`app/engine/cycles.py`)

The heart of the "Israeli salary + card charge day" model. Definitions (from spec §4):
- A **salary cycle** starts ON the (clamped) salary day and ends the day before the next one.
- A **card purchase on charge day C belongs to the NEXT statement** — so the upcoming
  statement window is `[last charge date ≤ today, first charge date > today)`.

**Files:**
- Create: `app/engine/cycles.py`
- Test: `tests/test_cycles.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_cycles.py`:
```python
import datetime as dt

from app.engine.cycles import card_window, clamped_date, salary_cycle


def test_clamp_day_31_in_short_month():
    assert clamped_date(2026, 6, 31) == dt.date(2026, 6, 30)
    assert clamped_date(2026, 2, 30) == dt.date(2026, 2, 28)
    assert clamped_date(2024, 2, 30) == dt.date(2024, 2, 29)  # leap

def test_cycle_mid_month():
    c = salary_cycle(dt.date(2026, 6, 11), salary_day=10)
    assert c["start"] == dt.date(2026, 6, 10)
    assert c["end"] == dt.date(2026, 7, 9)
    assert c["day_index"] == 2 and c["length"] == 30 and c["days_left"] == 29

def test_cycle_on_salary_day():
    c = salary_cycle(dt.date(2026, 6, 10), salary_day=10)
    assert c["start"] == dt.date(2026, 6, 10) and c["day_index"] == 1

def test_cycle_day_before_salary():
    c = salary_cycle(dt.date(2026, 6, 9), salary_day=10)
    assert c["start"] == dt.date(2026, 5, 10) and c["days_left"] == 1

def test_cycle_straddles_year():
    c = salary_cycle(dt.date(2026, 1, 5), salary_day=10)
    assert c["start"] == dt.date(2025, 12, 10)
    assert c["end"] == dt.date(2026, 1, 9)

def test_cycle_salary_day_31_clamps():
    c = salary_cycle(dt.date(2026, 6, 30), salary_day=31)
    # June's anchor clamps to Jun 30 → today IS cycle start
    assert c["start"] == dt.date(2026, 6, 30)
    assert c["end"] == dt.date(2026, 7, 30)  # day before Jul 31 anchor

def test_card_window_purchase_on_charge_day_rolls_forward():
    w = card_window(dt.date(2026, 6, 2), charge_day=2)
    # today IS charge day → today's purchases belong to the NEXT statement
    assert w["start"] == dt.date(2026, 6, 2)
    assert w["charge_date"] == dt.date(2026, 7, 2)
    assert w["days_to_charge"] == 30

def test_card_window_day_before_charge():
    w = card_window(dt.date(2026, 6, 1), charge_day=2)
    assert w["start"] == dt.date(2026, 5, 2)
    assert w["charge_date"] == dt.date(2026, 6, 2)
    assert w["days_to_charge"] == 1

def test_card_window_feb_leap():
    w = card_window(dt.date(2024, 2, 29), charge_day=30)
    # Feb 2024 anchor clamps to Feb 29 → today is a charge day
    assert w["start"] == dt.date(2024, 2, 29)
    assert w["charge_date"] == dt.date(2024, 3, 30)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_cycles.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.cycles'`

- [ ] **Step 3: Implement `app/engine/cycles.py`**

```python
from __future__ import annotations

import calendar
import datetime as dt


def clamped_date(year: int, month: int, day: int) -> dt.date:
    """The requested day, clamped to the month's last day (salary day 31 → Jun 30)."""
    return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _next_anchor(today: dt.date, day: int) -> dt.date:
    """First clamped anchor date STRICTLY after today."""
    cand = clamped_date(today.year, today.month, day)
    if cand > today:
        return cand
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return clamped_date(y, m, day)


def _last_anchor(today: dt.date, day: int) -> dt.date:
    """Last clamped anchor date on or before today."""
    cand = clamped_date(today.year, today.month, day)
    if cand <= today:
        return cand
    y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return clamped_date(y, m, day)


def salary_cycle(today: dt.date, salary_day: int) -> dict:
    start = _last_anchor(today, salary_day)
    nxt = _next_anchor(today, salary_day)
    return {
        "start": start,
        "end": nxt - dt.timedelta(days=1),
        "day_index": (today - start).days + 1,
        "length": (nxt - start).days,
        "days_left": (nxt - today).days,  # includes today
    }


def card_window(today: dt.date, charge_day: int) -> dict:
    """Window of purchases accruing to the UPCOMING charge.
    A purchase ON a charge date belongs to the next statement, so:
    start = last charge date <= today, charge_date = first charge date > today,
    and accrual sums purchases with start <= date < charge_date."""
    return {
        "start": _last_anchor(today, charge_day),
        "charge_date": _next_anchor(today, charge_day),
        "days_to_charge": (_next_anchor(today, charge_day) - today).days,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_cycles.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: salary-cycle and card-statement calendar math"
```

---

### Task 7: Budget math (`app/engine/budget.py`)

**Files:**
- Create: `app/engine/budget.py`
- Test: `tests/test_budget.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_budget.py`:
```python
import datetime as dt

from app import db
from app.engine import budget

TODAY = dt.date(2026, 6, 11)  # cycle = Jun 10 .. Jul 9 (salary_day 10)


def _spend(conn, cat, agorot, day=TODAY, method="card"):
    db.add_transaction(conn, effective_date=day, amount_agorot=-agorot,
                       direction="expense",
                       category_id=db.category_id_by_name(conn, cat),
                       payment_method=method, description="t")


def test_safe_to_spend_basic(seeded):
    # discretionary pool = 60000+120000+40000+40000 = 260000 (Bills excluded)
    _spend(seeded, "Food out", 4500)
    s = budget.safe_to_spend(seeded, TODAY)
    assert s["pool_agorot"] == 260000
    assert s["spent_agorot"] == 4500
    assert s["days_left"] == 29
    assert s["today_agorot"] == (260000 - 4500) // 29

def test_safe_to_spend_excludes_bills_and_cash_counts(seeded):
    _spend(seeded, "Bills", 100000)          # fixed → not discretionary
    _spend(seeded, "Food out", 1000, method="cash")  # cash still counts
    s = budget.safe_to_spend(seeded, TODAY)
    assert s["spent_agorot"] == 1000

def test_safe_to_spend_ignores_out_of_cycle(seeded):
    _spend(seeded, "Food out", 9999, day=dt.date(2026, 6, 9))  # previous cycle
    assert budget.safe_to_spend(seeded, TODAY)["spent_agorot"] == 0

def test_category_status_pace(seeded):
    _spend(seeded, "Food out", 30000)  # half the 60000 budget on day 2 of 30
    rows = {r["name"]: r for r in budget.category_status(seeded, TODAY)}
    food = rows["Food out"]
    assert food["spent_agorot"] == 30000 and food["budget_agorot"] == 60000
    assert food["pace_ratio"] > 1.0  # way over pace this early

def test_card_accrual_window_and_methods(seeded):
    _spend(seeded, "Food out", 5000)                       # card, in window
    _spend(seeded, "Fun", 2000, method="cash")             # cash → excluded
    _spend(seeded, "Food out", 7000, day=dt.date(2026, 5, 30))  # previous statement
    acc = budget.card_accrual(seeded, TODAY)
    assert acc["total_agorot"] == 5000
    assert acc["charge_date"] == dt.date(2026, 7, 2)

def test_cycle_net(seeded):
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=900000,
                       direction="income",
                       category_id=db.category_id_by_name(seeded, "Salary"))
    _spend(seeded, "Food out", 4500)
    income, expenses = budget.cycle_net(seeded, dt.date(2026, 6, 10),
                                        dt.date(2026, 7, 9))
    assert income == 900000 and expenses == 4500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_budget.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.budget'`

- [ ] **Step 3: Implement `app/engine/budget.py`**

```python
from __future__ import annotations

import datetime as dt

from app import db
from app.engine import cycles


def _salary_day(conn) -> int:
    return int(db.get_setting(conn, "salary_day", "1"))


def _cycle(conn, today: dt.date) -> dict:
    return cycles.salary_cycle(today, _salary_day(conn))


def _expense_sum(conn, *, start, end, category_ids=None, payment_method=None) -> int:
    """Positive agorot total of (non-deleted) expenses in [start, end]."""
    q = ("SELECT COALESCE(SUM(-amount_agorot),0) AS s FROM transactions"
         " WHERE deleted_at IS NULL AND direction='expense'"
         " AND effective_date >= ? AND effective_date <= ?")
    args = [start.isoformat(), end.isoformat()]
    if category_ids is not None:
        q += f" AND category_id IN ({','.join('?'*len(category_ids))})"
        args += list(category_ids)
    if payment_method:
        q += " AND payment_method = ?"
        args.append(payment_method)
    return conn.execute(q, args).fetchone()["s"]


def _discretionary_ids(conn) -> list[int]:
    return [c["id"] for c in db.categories(conn)
            if not c["is_income"] and not c["is_fixed"]]


def safe_to_spend(conn, today: dt.date) -> dict:
    cyc = _cycle(conn, today)
    budgets = db.get_budgets(conn)
    disc = _discretionary_ids(conn)
    pool = sum(budgets.get(cid, 0) for cid in disc)
    spent = _expense_sum(conn, start=cyc["start"], end=cyc["end"],
                         category_ids=disc)
    remaining = pool - spent
    return {
        "pool_agorot": pool,
        "spent_agorot": spent,
        "remaining_agorot": remaining,
        "days_left": cyc["days_left"],
        "today_agorot": remaining // cyc["days_left"] if remaining > 0 else 0,
        "cycle": cyc,
    }


def category_status(conn, today: dt.date) -> list[dict]:
    cyc = _cycle(conn, today)
    budgets = db.get_budgets(conn)
    progress = cyc["day_index"] / cyc["length"]
    out = []
    for c in db.categories(conn):
        if c["is_income"]:
            continue
        spent = _expense_sum(conn, start=cyc["start"], end=cyc["end"],
                             category_ids=[c["id"]])
        bud = budgets.get(c["id"], 0)
        if bud == 0 and spent == 0:
            continue
        ratio = (spent / bud) / progress if bud > 0 and progress > 0 else 0.0
        out.append({"category_id": c["id"], "name": c["name"],
                    "emoji": c["emoji"], "is_fixed": bool(c["is_fixed"]),
                    "spent_agorot": spent, "budget_agorot": bud,
                    "pace_ratio": round(ratio, 2)})
    return out


def card_accrual(conn, today: dt.date) -> dict:
    w = cycles.card_window(today, int(db.get_setting(conn, "card_charge_day", "1")))
    total = _expense_sum(conn, start=w["start"],
                         end=w["charge_date"] - dt.timedelta(days=1),
                         payment_method="card")
    return {"total_agorot": total, "charge_date": w["charge_date"],
            "days_to_charge": w["days_to_charge"]}


def cycle_net(conn, start: dt.date, end: dt.date) -> tuple[int, int]:
    """(income, expenses) as positive agorot for the window."""
    income = conn.execute(
        "SELECT COALESCE(SUM(amount_agorot),0) AS s FROM transactions"
        " WHERE deleted_at IS NULL AND direction='income'"
        " AND effective_date >= ? AND effective_date <= ?",
        (start.isoformat(), end.isoformat())).fetchone()["s"]
    expenses = _expense_sum(conn, start=start, end=end)
    return income, expenses
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_budget.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: safe-to-spend, category pace, card accrual"
```

---

### Task 8: Goal math (`app/engine/goals.py`)

**Files:**
- Create: `app/engine/goals.py`
- Test: `tests/test_goals.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_goals.py`:
```python
import datetime as dt

from app import db
from app.engine import goals

TODAY = dt.date(2026, 6, 11)


def _contribute(conn, gid, agorot, day):
    db.add_transaction(conn, effective_date=day, amount_agorot=-agorot,
                       direction="goal_contribution", goal_id=gid,
                       description="saving")


def test_progress_and_pct(seeded):
    gid = db.add_goal(seeded, name="Drone", type="purchase_fund",
                      target_agorot=450000)
    _contribute(seeded, gid, 100000, dt.date(2026, 5, 20))
    _contribute(seeded, gid, 71000, dt.date(2026, 6, 5))
    (r,) = goals.goal_report(seeded, TODAY)
    assert r["progress_agorot"] == 171000
    assert r["pct"] == 38  # 171000/450000

def test_pace_needed_for_dated_goal(seeded):
    gid = db.add_goal(seeded, name="Trip", type="save_by_date",
                      target_agorot=300000, target_date=dt.date(2026, 9, 11))
    _contribute(seeded, gid, 60000, dt.date(2026, 6, 1))
    (r,) = goals.goal_report(seeded, TODAY)
    # 240000 remaining over ~3.02 months → ≈ 79500/mo; allow rounding window
    assert 75000 <= r["pace_needed_agorot"] <= 82000

def test_projection_from_recent_contributions(seeded):
    gid = db.add_goal(seeded, name="Drone", type="purchase_fund",
                      target_agorot=200000)
    _contribute(seeded, gid, 50000, TODAY - dt.timedelta(days=60))
    _contribute(seeded, gid, 50000, TODAY - dt.timedelta(days=30))
    (r,) = goals.goal_report(seeded, TODAY)
    assert r["projected_date"] is not None  # ~2 months away at 50k/30d

def test_fund_verdict_ready(seeded):
    gid = db.add_goal(seeded, name="Game", type="purchase_fund",
                      target_agorot=10000)
    _contribute(seeded, gid, 10000, TODAY)
    (r,) = goals.goal_report(seeded, TODAY)
    assert r["verdict"] == "ready"

def test_no_contributions_no_projection(seeded):
    db.add_goal(seeded, name="Empty", type="purchase_fund", target_agorot=5000)
    (r,) = goals.goal_report(seeded, TODAY)
    assert r["progress_agorot"] == 0 and r["projected_date"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_goals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.goals'`

- [ ] **Step 3: Implement `app/engine/goals.py`**

```python
from __future__ import annotations

import datetime as dt

from app import db
from app.engine import budget, cycles
from app.models import fmt_ils

AVG_MONTH_DAYS = 30.44


def goal_progress(conn, goal_id: int) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(-amount_agorot),0) AS s FROM transactions"
        " WHERE deleted_at IS NULL AND direction='goal_contribution'"
        " AND goal_id=?", (goal_id,)).fetchone()["s"]


def monthly_savings_pace(conn, today: dt.date, n_cycles: int = 3) -> int:
    """Average (income − expenses) over the last n completed salary cycles;
    falls back to the current cycle's net-so-far if none are complete."""
    salary_day = int(db.get_setting(conn, "salary_day", "1"))
    cyc = cycles.salary_cycle(today, salary_day)
    nets = []
    cursor = cyc["start"]
    for _ in range(n_cycles):
        prev = cycles.salary_cycle(cursor - dt.timedelta(days=1), salary_day)
        income, expenses = budget.cycle_net(conn, prev["start"], prev["end"])
        if income or expenses:
            nets.append(income - expenses)
        cursor = prev["start"]
    if nets:
        return sum(nets) // len(nets)
    income, expenses = budget.cycle_net(conn, cyc["start"], today)
    return income - expenses


def _projection(conn, goal_id: int, remaining: int, today: dt.date):
    """Projected completion from the last 90 days of contributions."""
    since = today - dt.timedelta(days=90)
    contributed = conn.execute(
        "SELECT COALESCE(SUM(-amount_agorot),0) AS s FROM transactions"
        " WHERE deleted_at IS NULL AND direction='goal_contribution'"
        " AND goal_id=? AND effective_date >= ?",
        (goal_id, since.isoformat())).fetchone()["s"]
    if contributed <= 0 or remaining <= 0:
        return None
    per_day = contributed / 90.0
    return today + dt.timedelta(days=round(remaining / per_day))


def goal_report(conn, today: dt.date) -> list[dict]:
    out = []
    for g in db.list_goals(conn):
        progress = goal_progress(conn, g["id"])
        target = g["target_agorot"]
        remaining = max(target - progress, 0)
        pct = min(int(progress * 100 / target), 100) if target else 0
        pace_needed = None
        if g["target_date"]:
            tdate = dt.date.fromisoformat(g["target_date"])
            months_left = max((tdate - today).days / AVG_MONTH_DAYS, 0.1)
            pace_needed = int(remaining / months_left)
        if remaining == 0:
            verdict = "ready"
        elif g["type"] == "purchase_fund":
            verdict = f"{fmt_ils(remaining)} to go"
        else:
            verdict = "on track" if pace_needed is not None and \
                monthly_savings_pace(conn, today) >= pace_needed else "behind"
        out.append({
            "id": g["id"], "name": g["name"], "emoji": g["emoji"],
            "type": g["type"], "target_agorot": target,
            "target_date": g["target_date"],
            "progress_agorot": progress, "remaining_agorot": remaining,
            "pct": pct, "pace_needed_agorot": pace_needed,
            "projected_date": _projection(conn, g["id"], remaining, today),
            "verdict": verdict,
        })
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_goals.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: goal progress, pace, projection, verdicts"
```

---

### Task 9: FX rates (`app/engine/fx.py`)

**Files:**
- Create: `app/engine/fx.py`
- Test: `tests/test_fx.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fx.py`:
```python
import datetime as dt
import json

from app import db
from app.engine import fx

TODAY = dt.date(2026, 6, 11)


def test_to_ils():
    agorot, rate = fx.to_ils(15, "USD", {"USD": 3.65})
    assert agorot == 5475 and rate == 3.65

def test_to_ils_ils_passthrough():
    agorot, rate = fx.to_ils(45, "ILS", {})
    assert agorot == 4500 and rate is None

def test_get_rates_uses_fallback_when_offline(conn, monkeypatch):
    monkeypatch.setattr(fx, "_fetch", lambda: (_ for _ in ()).throw(OSError()))
    rates = fx.get_rates(conn, TODAY)
    assert rates["USD"] > 0  # fallback table

def test_get_rates_caches_and_respects_age(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(fx, "_fetch", lambda: calls.append(1) or {"USD": 3.5})
    fx.get_rates(conn, TODAY)
    fx.get_rates(conn, TODAY)          # fresh cache → no second fetch
    assert len(calls) == 1
    stored = json.loads(db.get_setting(conn, "fx_rates_json"))
    stored["fetched"] = (TODAY - dt.timedelta(days=8)).isoformat()
    db.set_setting(conn, "fx_rates_json", json.dumps(stored))
    fx.get_rates(conn, TODAY)          # stale → refetch
    assert len(calls) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_fx.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.fx'`

- [ ] **Step 3: Implement `app/engine/fx.py`**

```python
from __future__ import annotations

import datetime as dt
import json

import requests

from app import db
from app.models import to_agorot

RATES_URL = "https://api.frankfurter.app/latest?base=ILS&symbols=USD,EUR,GBP"
FALLBACK_RATES = {"USD": 3.7, "EUR": 4.0, "GBP": 4.7}  # 1 unit → ILS
MAX_AGE_DAYS = 7


def _fetch() -> dict:
    """Fetch ILS-base rates and invert them to '1 foreign unit = X ILS'."""
    resp = requests.get(RATES_URL, timeout=5)
    resp.raise_for_status()
    data = resp.json()["rates"]  # e.g. {"USD": 0.27} meaning 1 ILS = 0.27 USD
    return {cur: round(1.0 / v, 4) for cur, v in data.items() if v}


def get_rates(conn, today: dt.date) -> dict:
    raw = db.get_setting(conn, "fx_rates_json")
    if raw:
        stored = json.loads(raw)
        age = (today - dt.date.fromisoformat(stored["fetched"])).days
        if age <= MAX_AGE_DAYS:
            return stored["rates"]
    try:
        rates = _fetch()
        db.set_setting(conn, "fx_rates_json",
                       json.dumps({"fetched": today.isoformat(), "rates": rates}))
        return rates
    except Exception:
        if raw:
            return json.loads(raw)["rates"]  # stale beats nothing
        return dict(FALLBACK_RATES)


def to_ils(amount: float, currency: str, rates: dict):
    """→ (agorot, fx_rate_used). ILS passes through with rate None."""
    if currency == "ILS":
        return to_agorot(amount), None
    rate = rates.get(currency) or FALLBACK_RATES.get(currency)
    if rate is None:
        raise ValueError(f"no FX rate for {currency}")
    return to_agorot(amount * rate), rate
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_fx.py -v`
Expected: `4 passed`

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: FX rate table with weekly refresh and offline fallback"
```

---

### Task 10: Fact-pack builder (`app/engine/insights.py`)

The single deterministic JSON blob consumed by briefings, chat, and templates.

**Files:**
- Create: `app/engine/insights.py`
- Test: `tests/test_insights.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_insights.py`:
```python
import datetime as dt
import json

from app import db
from app.engine import insights

TODAY = dt.date(2026, 6, 11)


def test_fact_pack_shape_and_json_safe(seeded):
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-4500,
                       direction="expense",
                       category_id=db.category_id_by_name(seeded, "Food out"),
                       description="falafel")
    db.add_transaction(seeded, effective_date=dt.date(2026, 6, 10),
                       amount_agorot=900000, direction="income",
                       category_id=db.category_id_by_name(seeded, "Salary"))
    gid = db.add_goal(seeded, name="Drone", type="purchase_fund",
                      target_agorot=450000)
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-171000,
                       direction="goal_contribution", goal_id=gid)
    fp = insights.fact_pack(seeded, TODAY)
    for key in ("user_name", "today", "cycle", "safe_to_spend", "categories",
                "card", "goals", "balance", "last_cycle"):
        assert key in fp, key
    json.dumps(fp)  # must be JSON-serializable as-is

def test_balance_math(seeded):
    # opening 500000 + income 900000 − expense 4500 − earmarked 171000
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=900000,
                       direction="income")
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-4500,
                       direction="expense",
                       category_id=db.category_id_by_name(seeded, "Food out"))
    gid = db.add_goal(seeded, name="D", type="purchase_fund", target_agorot=450000)
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-171000,
                       direction="goal_contribution", goal_id=gid)
    fp = insights.fact_pack(seeded, TODAY)
    assert fp["balance"]["available_agorot"] == 500000 + 900000 - 4500 - 171000
    assert fp["balance"]["earmarked_agorot"] == 171000
    assert fp["balance"]["total_agorot"] == 500000 + 900000 - 4500
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_insights.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.engine.insights'`

- [ ] **Step 3: Implement `app/engine/insights.py`**

```python
from __future__ import annotations

import datetime as dt

from app import db
from app.engine import budget, cycles, goals
from app.models import fmt_ils


def _iso_dict(d: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, dt.date) else v)
            for k, v in d.items()}


def fact_pack(conn, today: dt.date) -> dict:
    salary_day = int(db.get_setting(conn, "salary_day", "1"))
    cyc = cycles.salary_cycle(today, salary_day)
    sts = budget.safe_to_spend(conn, today)
    card = budget.card_accrual(conn, today)
    income, expenses = budget.cycle_net(conn, cyc["start"], cyc["end"])

    opening = int(db.get_setting(conn, "opening_balance_agorot", "0"))
    signed_sum = conn.execute(
        "SELECT COALESCE(SUM(amount_agorot),0) AS s FROM transactions"
        " WHERE deleted_at IS NULL").fetchone()["s"]
    available = opening + signed_sum
    earmarked = sum(g["progress_agorot"] for g in goals.goal_report(conn, today))

    prev = cycles.salary_cycle(cyc["start"] - dt.timedelta(days=1), salary_day)
    prev_income, prev_expenses = budget.cycle_net(conn, prev["start"], prev["end"])

    return {
        "user_name": db.get_setting(conn, "user_name", ""),
        "today": today.isoformat(),
        "weekday": today.strftime("%A"),
        "cycle": _iso_dict(cyc),
        "safe_to_spend": {
            "today_agorot": sts["today_agorot"],
            "today_fmt": fmt_ils(sts["today_agorot"]),
            "remaining_agorot": sts["remaining_agorot"],
            "remaining_fmt": fmt_ils(sts["remaining_agorot"]),
            "pool_agorot": sts["pool_agorot"],
            "days_left": sts["days_left"],
        },
        "categories": budget.category_status(conn, today),
        "card": {"total_agorot": card["total_agorot"],
                 "total_fmt": fmt_ils(card["total_agorot"]),
                 "charge_date": card["charge_date"].isoformat(),
                 "days_to_charge": card["days_to_charge"]},
        "income_this_cycle_agorot": income,
        "expenses_this_cycle_agorot": expenses,
        "balance": {"available_agorot": available,
                    "available_fmt": fmt_ils(available),
                    "earmarked_agorot": earmarked,
                    "total_agorot": available + earmarked,
                    "total_fmt": fmt_ils(available + earmarked)},
        "goals": [_iso_dict({**g,
                             "projected_date": g["projected_date"]})
                  for g in goals.goal_report(conn, today)],
        "monthly_savings_pace_agorot": goals.monthly_savings_pace(conn, today),
        "last_cycle": {"income_agorot": prev_income,
                       "expenses_agorot": prev_expenses},
        "recent_transactions": [
            {"date": r["effective_date"], "amount_fmt": fmt_ils(r["amount_agorot"]),
             "category": r["category_name"], "description": r["description"]}
            for r in db.list_transactions(conn, limit=20)],
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_insights.py -v`
Expected: `2 passed`

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: deterministic fact-pack for briefings, chat and templates"
```

---

### Task 11: Prompts & Claude transport (`app/ai/prompts.py`, `app/ai/client.py`)

**Files:**
- Create: `app/ai/prompts.py`, `app/ai/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_client.py`:
```python
import json
import subprocess

import pytest

from app.ai import client


def test_extract_json_plain():
    assert client.extract_json('[{"a": 1}]') == [{"a": 1}]

def test_extract_json_with_fences_and_prose():
    text = 'Sure! Here it is:\n```json\n[{"a": "br]acket"}]\n```\nDone.'
    assert client.extract_json(text) == [{"a": "br]acket"}]

def test_extract_json_object():
    assert client.extract_json('noise {"x": 2} noise', opener="{") == {"x": 2}

def test_extract_json_missing_raises():
    with pytest.raises(ValueError):
        client.extract_json("no json here")

def test_cli_transport_parses_result(monkeypatch):
    monkeypatch.setattr(client.shutil, "which", lambda _: r"C:\fake\claude.exe")
    def fake_run(cmd, **kw):
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps({"result": "hello"}), stderr="")
    monkeypatch.setattr(client.subprocess, "run", fake_run)
    assert client._via_cli("hi", None, 5) == "hello"

def test_cli_transport_missing_exe_raises(monkeypatch):
    monkeypatch.setattr(client.shutil, "which", lambda _: None)
    with pytest.raises(client.AIUnavailable):
        client._via_cli("hi", None, 5)

def test_ask_claude_falls_back_to_cli(monkeypatch):
    monkeypatch.setattr(client, "_via_sdk",
                        lambda *a: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(client, "_via_cli", lambda *a: "from-cli")
    assert client.ask_claude("hi") == "from-cli"

def test_ask_claude_raises_when_both_fail(monkeypatch):
    monkeypatch.setattr(client, "_via_sdk",
                        lambda *a: (_ for _ in ()).throw(RuntimeError()))
    monkeypatch.setattr(client, "_via_cli",
                        lambda *a: (_ for _ in ()).throw(client.AIUnavailable("x")))
    with pytest.raises(client.AIUnavailable):
        client.ask_claude("hi")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai.client'`

- [ ] **Step 3: Implement `app/ai/prompts.py`**

```python
"""All prompt templates in one place. Keep wording stable — tests and the
repair loop depend on the JSON contracts described here."""

PARSE_SYSTEM = """You convert casual personal-finance notes into strict JSON.
Reply with ONLY a JSON array (no prose, no markdown fences). Each element:
{"effective_date": "YYYY-MM-DD", "amount": <positive number, units of currency>,
 "currency": "ILS"|"USD"|"EUR"|..., "direction": "expense"|"income"|"goal_contribution",
 "category": "<one of the provided category names, exactly>",
 "description": "<short human description>", "merchant": "<or null>",
 "people": "<who it was with, or null>", "payment_method": "card"|"cash"|"transfer",
 "goal_name": "<goal name if this is a goal contribution, else null>",
 "confidence": <0..1>}
Rules:
- Resolve relative dates ("yesterday", weekday names) against TODAY; never future dates.
- One element per distinct transaction; a line may contain several.
- "salary landed" or similar means income of SALARY AMOUNT on TODAY, category Salary.
- "put X in/toward <goal>" means direction goal_contribution with that goal_name.
- HOUSE RULES override your own category instincts.
- If genuinely unsure of category, use "Other" and lower confidence below 0.7.
- amount is ALWAYS positive; direction carries the sign."""

PARSE_USER_TMPL = """TODAY: {today} ({weekday})
CATEGORIES: {categories}
HOUSE RULES (keyword -> category): {rules}
ACTIVE GOALS: {goals}
SALARY AMOUNT: {salary}
DEFAULT PAYMENT METHOD: card

TEXT:
{text}"""

REPAIR_TMPL = """Your previous reply could not be used: {error}
Reply again with ONLY the corrected JSON array, nothing else.
Previous reply:
{previous}"""

BRIEFING_SYSTEM = """You are the advisor voice of MoneyPilot, a personal finance
cockpit. Write a daily briefing in second person, cockpit-crisp, max 90 words,
plain text (no markdown, no headers, no emoji spam — one emoji max). Use ONLY
numbers present in the FACTS JSON; never invent figures. Mention: safe-to-spend
today, the most notable category pace (good or bad), the upcoming card charge,
and the most relevant goal. End with one short, concrete, actionable suggestion."""

BRIEFING_USER_TMPL = """FACTS (JSON):
{facts}

Write today's briefing."""

CHAT_SYSTEM = """You are the MoneyPilot advisor: a sharp, friendly personal-finance
copilot. Answer using ONLY the numbers in FACTS — never invent figures. Be concrete
and brief; plain text. Amounts are in agorot in FACTS unless the field name ends
in _fmt; always present amounts to the user in shekels (the _fmt fields are
preformatted for you).
If — and only if — the user asks you to change something (create a goal, change a
budget, log a transaction, change a setting), append exactly one action block:
```action
{"type": "create_goal"|"update_budget"|"add_transaction"|"adjust_setting", ...}
```
Schemas: create_goal {"type":"create_goal","name":str,"goal_type":"save_by_date"|"purchase_fund","target_ils":number,"target_date":"YYYY-MM-DD"|null}
update_budget {"type":"update_budget","category":str,"amount_ils":number}
add_transaction {"type":"add_transaction","txn":{<same fields as the parser schema>}}
adjust_setting {"type":"adjust_setting","key":"salary_day"|"salary_amount_agorot"|"card_charge_day"|"user_name","value":str}
The app will show the action to the user for confirmation — describe it in your
text too."""

CHAT_USER_TMPL = """FACTS (JSON):
{facts}

RECENT CONVERSATION:
{history}

USER: {question}"""

ONBOARD_SYSTEM = """You turn a person's free-text description of their current money
situation into strict JSON for initializing a finance app. Reply with ONLY a JSON
object (no prose):
{"opening_balance_ils": <number, their cash/bank now, 0 if unknown>,
 "transactions": [<array of month-to-date items, same schema as the parser:
   effective_date, amount, currency, direction, category, description, merchant,
   people, payment_method, goal_name, confidence>],
 "suggested_budgets": {"<category name>": <ILS per month>, ...}}
Budget suggestions must cover the provided category list (skip income categories),
be realistic given their salary and dump, and sum comfortably below the salary."""

ONBOARD_USER_TMPL = """TODAY: {today}
CATEGORIES: {categories}
SALARY: {salary} ILS on day {salary_day} of the month

THE USER'S DUMP:
{text}"""
```

- [ ] **Step 4: Implement `app/ai/client.py`**

```python
from __future__ import annotations

import json
import shutil
import subprocess


class AIUnavailable(Exception):
    """Claude could not be reached / produced nothing usable. Callers fall back."""


def extract_json(text: str, opener: str = "["):
    """Pull the first JSON value opening with `opener` out of an AI reply,
    tolerating prose and markdown fences around it."""
    i = text.find(opener)
    if i < 0:
        raise ValueError(f"no {opener!r} found in AI reply")
    obj, _ = json.JSONDecoder().raw_decode(text[i:])
    return obj


def _via_sdk(prompt: str, system, timeout_s: int) -> str:
    """Claude Agent SDK transport — rides the local Claude Code login."""
    import anyio
    from claude_agent_sdk import ClaudeAgentOptions, query

    async def _run():
        opts = ClaudeAgentOptions(system_prompt=system, max_turns=1,
                                  allowed_tools=[])
        result = None
        with anyio.move_on_after(timeout_s):
            async for message in query(prompt=prompt, options=opts):
                r = getattr(message, "result", None)
                if isinstance(r, str):
                    result = r
        if result is None:
            raise TimeoutError("no result from Agent SDK")
        return result

    return anyio.run(_run)


def _via_cli(prompt: str, system, timeout_s: int) -> str:
    """`claude -p` headless transport — same login, zero extra deps."""
    exe = shutil.which("claude")
    if not exe:
        raise AIUnavailable("claude CLI not found on PATH")
    cmd = [exe, "-p", "--output-format", "json", "--max-turns", "1"]
    if system:
        cmd += ["--append-system-prompt", system]
    try:
        res = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout_s,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as e:
        raise AIUnavailable("claude CLI timed out") from e
    if res.returncode != 0:
        raise AIUnavailable(f"claude CLI exit {res.returncode}: {res.stderr[:200]}")
    try:
        out = json.loads(res.stdout).get("result")
    except (json.JSONDecodeError, AttributeError):
        out = res.stdout
    if not out or not str(out).strip():
        raise AIUnavailable("empty reply from claude CLI")
    return str(out)


def ask_claude(prompt: str, system=None, timeout_s: int = 60) -> str:
    """Primary: Agent SDK. Fallback: claude -p. Raises AIUnavailable if both fail."""
    try:
        return _via_sdk(prompt, system, timeout_s)
    except Exception:
        pass  # SDK missing/broken/timed out — the CLI is the safety net
    return _via_cli(prompt, system, timeout_s)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_client.py -v`
Expected: `8 passed`

- [ ] **Step 6: One-shot REAL transport check (manual, requires Claude login)**

Run: `.venv\Scripts\python -c "from app.ai.client import ask_claude; print(ask_claude('Reply with exactly: PONG'))"`
Expected: prints a line containing `PONG`. If this fails but tests pass, the app still works offline — note it and continue.

- [ ] **Step 7: Commit**

```powershell
git add -A; git commit -m "feat: prompt templates and dual Claude transport (SDK + CLI)"
```

---

### Task 12: Fallback parser (`app/ai/parser.py`, offline path)

**Files:**
- Create: `app/ai/parser.py` (fallback half)
- Test: `tests/test_fallback_parser.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_fallback_parser.py`:
```python
import datetime as dt

import pytest

from app.ai.parser import fallback_parse

TODAY = dt.date(2026, 6, 11)


def test_simple_expense():
    (p,) = fallback_parse("45 falafel with karim", TODAY)
    assert p.amount == 45 and p.direction == "expense"
    assert p.category == "Food out" and p.effective_date == TODAY
    assert p.confidence <= 0.5

def test_yesterday():
    (p,) = fallback_parse("30 coffee yesterday", TODAY)
    assert p.effective_date == TODAY - dt.timedelta(days=1)

def test_currency_detection():
    (p,) = fallback_parse("bought a game 15 dollars", TODAY)
    assert p.currency == "USD" and p.amount == 15

def test_income_hint():
    (p,) = fallback_parse("got paid 9000 salary", TODAY)
    assert p.direction == "income"

def test_multiline_gives_multiple():
    ps = fallback_parse("45 falafel\n220 fuel", TODAY)
    assert len(ps) == 2
    assert ps[1].category == "Transport"

def test_decimal_amount_and_unknown_category():
    (p,) = fallback_parse("12.50 mystery thing", TODAY)
    assert p.amount == 12.5 and p.category == "Other"

def test_no_amount_raises():
    with pytest.raises(ValueError):
        fallback_parse("had a nice day", TODAY)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_fallback_parser.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai.parser'`

- [ ] **Step 3: Implement the fallback half of `app/ai/parser.py`**

```python
from __future__ import annotations

import datetime as dt
import re

from app.models import ParsedTxn

# --- offline fallback parser --------------------------------------------------

KEYWORDS = {
    "Food out": ["falafel", "shawarma", "pizza", "burger", "restaurant", "cafe",
                 "coffee", "lunch", "dinner", "hummus", "sushi", "mcdonald"],
    "Groceries": ["groceries", "supermarket", "shufersal", "rami levy", "victory",
                  "market"],
    "Transport": ["fuel", "gas", "tank", "bus", "train", "taxi", "gett",
                  "parking", "diesel"],
    "Bills": ["rent", "electricity", "water bill", "arnona", "internet",
              "phone bill", "cellcom", "partner", "bezeq"],
    "Fun": ["movie", "cinema", "game", "beer", "bar", "party", "netflix",
            "spotify"],
    "Health": ["pharmacy", "doctor", "medicine", "dentist", "superpharm"],
    "Education": ["course", "book", "tuition", "university"],
}
CURRENCY_HINTS = {"$": "USD", "dollar": "USD", "usd": "USD",
                  "€": "EUR", "euro": "EUR", "eur": "EUR",
                  "£": "GBP", "pound": "GBP", "gbp": "GBP"}
INCOME_HINTS = ["salary", "paycheck", "income", "got paid", "received", "refund"]

_AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d{1,2})?)")


def _line_to_txn(line: str, today: dt.date) -> ParsedTxn | None:
    m = _AMOUNT_RE.search(line)
    if not m:
        return None
    amount = float(m.group(1).replace(",", "."))
    low = line.lower()
    currency = "ILS"
    for hint, cur in CURRENCY_HINTS.items():
        if hint in low:
            currency = cur
            break
    when = today
    if "yesterday" in low:
        when = today - dt.timedelta(days=1)
    direction = "income" if any(h in low for h in INCOME_HINTS) else "expense"
    category = "Other" if direction == "expense" else "Other income"
    if direction == "expense":
        for cat, words in KEYWORDS.items():
            if any(w in low for w in words):
                category = cat
                break
    return ParsedTxn(effective_date=when, amount=amount, currency=currency,
                     direction=direction, category=category,
                     description=line.strip(), confidence=0.3)


def fallback_parse(text: str, today: dt.date) -> list[ParsedTxn]:
    """Regex-only parse used when Claude is unreachable. Low confidence by design;
    callers must store results with needs_review=1."""
    out = []
    for line in filter(None, (ln.strip() for ln in text.splitlines())):
        txn = _line_to_txn(line, today)
        if txn:
            out.append(txn)
    if not out:
        raise ValueError("no amount found in text")
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_fallback_parser.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: offline regex fallback parser"
```

---

### Task 13: AI parse pipeline & review sweep (`app/ai/parser.py` continued)

**Files:**
- Modify: `app/ai/parser.py` (append)
- Test: `tests/test_parser_pipeline.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_parser_pipeline.py`:
```python
import datetime as dt
import json

import pytest

from app import db
from app.ai import client, parser

TODAY = dt.date(2026, 6, 11)

GOOD_REPLY = json.dumps([{
    "effective_date": "2026-06-11", "amount": 45, "currency": "ILS",
    "direction": "expense", "category": "Food out",
    "description": "falafel with karim", "merchant": None, "people": "karim",
    "payment_method": "card", "goal_name": None, "confidence": 0.95}])


def test_ai_path_stores_transaction(seeded, monkeypatch):
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: GOOD_REPLY)
    res = parser.parse_and_store(seeded, "45 falafel with karim", TODAY)
    assert res["used_ai"] is True and len(res["entries"]) == 1
    (row,) = db.list_transactions(seeded)
    assert row["amount_agorot"] == -4500 and row["category_name"] == "Food out"
    assert row["needs_review"] == 0 and row["source"] == "ai"

def test_repair_retry_then_success(seeded, monkeypatch):
    replies = iter(["not json at all", GOOD_REPLY])
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: next(replies))
    res = parser.parse_and_store(seeded, "45 falafel", TODAY)
    assert res["used_ai"] is True and len(db.list_transactions(seeded)) == 1

def test_ai_down_uses_fallback_flagged(seeded, monkeypatch):
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("offline")))
    res = parser.parse_and_store(seeded, "45 falafel", TODAY)
    assert res["used_ai"] is False
    (row,) = db.list_transactions(seeded)
    assert row["needs_review"] == 1 and row["source"] == "fallback"

def test_low_confidence_flags_review(seeded, monkeypatch):
    low = json.loads(GOOD_REPLY); low[0]["confidence"] = 0.5
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: json.dumps(low))
    parser.parse_and_store(seeded, "45 falafel", TODAY)
    assert db.list_transactions(seeded)[0]["needs_review"] == 1

def test_unknown_category_lands_in_other_flagged(seeded, monkeypatch):
    odd = json.loads(GOOD_REPLY); odd[0]["category"] = "Spaceships"
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: json.dumps(odd))
    parser.parse_and_store(seeded, "45 rocket", TODAY)
    row = db.list_transactions(seeded)[0]
    assert row["category_name"] == "Other" and row["needs_review"] == 1

def test_goal_contribution_links_goal(seeded, monkeypatch):
    gid = db.add_goal(seeded, name="Drone", type="purchase_fund",
                      target_agorot=450000)
    reply = json.dumps([{"effective_date": "2026-06-11", "amount": 500,
                         "currency": "ILS", "direction": "goal_contribution",
                         "category": "Other", "description": "drone fund",
                         "merchant": None, "people": None,
                         "payment_method": "transfer", "goal_name": "drone",
                         "confidence": 0.9}])
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: reply)
    parser.parse_and_store(seeded, "put 500 into drone fund", TODAY)
    row = db.list_transactions(seeded)[0]
    assert row["goal_id"] == gid and row["amount_agorot"] == -50000

def test_foreign_currency_converted(seeded, monkeypatch):
    db.set_setting(seeded, "fx_rates_json", json.dumps(
        {"fetched": TODAY.isoformat(), "rates": {"USD": 3.6}}))
    reply = json.dumps([{"effective_date": "2026-06-11", "amount": 15,
                         "currency": "USD", "direction": "expense",
                         "category": "Fun", "description": "game",
                         "merchant": None, "people": None,
                         "payment_method": "card", "goal_name": None,
                         "confidence": 0.9}])
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: reply)
    parser.parse_and_store(seeded, "bought a game 15 dollars", TODAY)
    row = db.list_transactions(seeded)[0]
    assert row["amount_agorot"] == -5400 and row["fx_rate"] == 3.6
    assert row["currency_orig"] == "USD" and row["amount_orig"] == 15

def test_fast_path_rule_skips_ai(seeded, monkeypatch):
    food = db.category_id_by_name(seeded, "Food out")
    db.add_rule(seeded, "falafel", food)
    def boom(*a, **k):
        raise AssertionError("AI should not be called on fast path")
    monkeypatch.setattr(parser.client, "ask_claude", boom)
    res = parser.parse_and_store(seeded, "45 falafel", TODAY)
    assert res["used_ai"] is False
    row = db.list_transactions(seeded)[0]
    assert row["source"] == "rule" and row["needs_review"] == 0

def test_resweep_upgrades_fallback_rows(seeded, monkeypatch):
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("offline")))
    parser.parse_and_store(seeded, "45 weird falafel snack", TODAY)
    assert db.list_transactions(seeded)[0]["needs_review"] == 1
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: GOOD_REPLY)
    upgraded = parser.resweep(seeded, TODAY)
    assert upgraded == 1
    row = db.list_transactions(seeded)[0]
    assert row["needs_review"] == 0 and row["source"] == "ai"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_parser_pipeline.py -v`
Expected: FAIL — `AttributeError: module 'app.ai.parser' has no attribute 'parse_and_store'`

- [ ] **Step 3: Append the pipeline to `app/ai/parser.py`**

Add these imports at the top of the file:
```python
import json

from pydantic import ValidationError

from app import db
from app.ai import client, prompts
from app.engine import fx
from app.models import to_agorot
```

Append:
```python
# --- AI parse pipeline ----------------------------------------------------------

REVIEW_CONFIDENCE = 0.7
_FAST_RE = re.compile(r"^\s*(\d+(?:[.,]\d{1,2})?)\s+([^\d].*)$")


def _fast_path(conn, text: str, today: dt.date) -> ParsedTxn | None:
    """'<amount> <words>' where a learned rule matches the words → no AI call."""
    if "\n" in text.strip():
        return None
    m = _FAST_RE.match(text)
    if not m:
        return None
    cat_id = db.match_rule(conn, m.group(2))
    if cat_id is None:
        return None
    cat = conn.execute("SELECT name FROM categories WHERE id=?",
                       (cat_id,)).fetchone()["name"]
    return ParsedTxn(effective_date=today,
                     amount=float(m.group(1).replace(",", ".")),
                     category=cat, description=m.group(2).strip(),
                     confidence=1.0)


def _build_prompt(conn, text: str, today: dt.date) -> tuple[str, str]:
    cats = ", ".join(c["name"] for c in db.categories(conn))
    rules = "; ".join(f"{r['pattern']} -> {r['category_name']}"
                      for r in db.list_rules(conn)) or "none"
    goal_names = ", ".join(g["name"] for g in db.list_goals(conn)) or "none"
    salary = int(db.get_setting(conn, "salary_amount_agorot", "0")) // 100
    user = prompts.PARSE_USER_TMPL.format(
        today=today.isoformat(), weekday=today.strftime("%A"),
        categories=cats, rules=rules, goals=goal_names, salary=salary, text=text)
    return prompts.PARSE_SYSTEM, user


def _ai_parse(conn, text: str, today: dt.date) -> list[ParsedTxn]:
    system, user = _build_prompt(conn, text, today)
    reply = client.ask_claude(user, system=system, timeout_s=60)
    for attempt in range(2):
        try:
            items = client.extract_json(reply)
            return [ParsedTxn(**item) for item in items]
        except (ValueError, ValidationError, TypeError) as e:
            if attempt == 1:
                raise
            reply = client.ask_claude(
                prompts.REPAIR_TMPL.format(error=str(e)[:300], previous=reply[:2000]),
                system=system, timeout_s=60)
    raise client.AIUnavailable("unreachable")  # pragma: no cover


def _store(conn, p: ParsedTxn, *, raw_text: str, source: str) -> int:
    rates = fx.get_rates(conn, p.effective_date) if p.currency != "ILS" else {}
    agorot, rate = fx.to_ils(p.amount, p.currency, rates)
    needs_review = 1 if (source == "fallback"
                         or p.confidence < REVIEW_CONFIDENCE) else 0
    cat_id = db.category_id_by_name(conn, p.category)
    if cat_id is None:
        fallback_cat = "Other income" if p.direction == "income" else "Other"
        cat_id = db.category_id_by_name(conn, fallback_cat)
        needs_review = 1
    goal_id = None
    if p.direction == "goal_contribution":
        g = db.get_goal_by_name(conn, p.goal_name or p.description)
        if g is None:
            needs_review = 1
        else:
            goal_id = g["id"]
    signed = agorot if p.direction == "income" else -agorot
    return db.add_transaction(
        conn, effective_date=p.effective_date, amount_agorot=signed,
        direction=p.direction, currency_orig=p.currency,
        amount_orig=(p.amount if p.currency != "ILS" else None),
        fx_rate=rate, category_id=cat_id, description=p.description,
        merchant=p.merchant, people=p.people, payment_method=p.payment_method,
        goal_id=goal_id, raw_text=raw_text, source=source,
        ai_confidence=p.confidence, needs_review=needs_review)


def parse_and_store(conn, text: str, today: dt.date) -> dict:
    """Entry point used by the UI. Never raises for AI problems; the only
    user-visible error is 'I could not find an amount in that'."""
    fast = _fast_path(conn, text, today)
    if fast is not None:
        ids = [_store(conn, fast, raw_text=text, source="rule")]
        return {"entries": ids, "used_ai": False, "source": "rule"}
    try:
        parsed = _ai_parse(conn, text, today)
        source, used_ai = "ai", True
    except Exception:
        parsed = fallback_parse(text, today)  # raises ValueError if no amount
        source, used_ai = "fallback", False
    ids = [_store(conn, p, raw_text=text, source=source) for p in parsed]
    return {"entries": ids, "used_ai": used_ai, "source": source}


def resweep(conn, today: dt.date) -> int:
    """Re-parse fallback-sourced rows once AI is reachable again. Only rows whose
    raw_text yields exactly one transaction are upgraded in place."""
    rows = db.list_transactions(conn, needs_review=True)
    upgraded = 0
    for row in rows:
        if row["source"] != "fallback" or not row["raw_text"]:
            continue
        try:
            parsed = _ai_parse(conn, row["raw_text"], today)
        except Exception:
            break  # still offline — stop trying
        if len(parsed) != 1:
            continue  # ambiguous → leave for manual review
        p = parsed[0]
        rates = fx.get_rates(conn, p.effective_date) if p.currency != "ILS" else {}
        agorot, rate = fx.to_ils(p.amount, p.currency, rates)
        cat_id = db.category_id_by_name(conn, p.category) or row["category_id"]
        db.update_transaction(
            conn, row["id"], effective_date=p.effective_date,
            amount_agorot=(agorot if p.direction == "income" else -agorot),
            direction=p.direction, category_id=cat_id, description=p.description,
            merchant=p.merchant, people=p.people,
            payment_method=p.payment_method, fx_rate=rate,
            ai_confidence=p.confidence, source="ai",
            needs_review=1 if p.confidence < REVIEW_CONFIDENCE else 0)
        upgraded += 1
    return upgraded
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_parser_pipeline.py tests\test_fallback_parser.py -v`
Expected: `16 passed`

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: AI parse pipeline with repair retry, fast path, review resweep"
```

---

### Task 14: Advisor — briefing, chat, actions, onboarding proposal (`app/ai/advisor.py`)

**Files:**
- Create: `app/ai/advisor.py`
- Test: `tests/test_advisor.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_advisor.py`:
```python
import datetime as dt
import json

from app import db
from app.ai import advisor, client

TODAY = dt.date(2026, 6, 11)


def test_briefing_ai_path_cached(seeded, monkeypatch):
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: "All systems nominal.")
    b1 = advisor.get_briefing(seeded, TODAY)
    assert b1 == {"text": "All systems nominal.", "source": "ai"}
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError))
    b2 = advisor.get_briefing(seeded, TODAY)  # second call: served from cache
    assert b2["source"] == "cache" and b2["text"] == b1["text"]

def test_briefing_offline_template_not_cached(seeded, monkeypatch):
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("x")))
    b = advisor.get_briefing(seeded, TODAY)
    assert b["source"] == "template"
    assert "₪" in b["text"]                      # has real numbers
    assert db.get_briefing(seeded, TODAY.isoformat()) is None  # not cached

def test_chat_plain_reply(seeded, monkeypatch):
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: "You are doing fine.")
    r = advisor.chat(seeded, "how am I doing?", TODAY)
    assert r["text"] == "You are doing fine." and r["action"] is None
    roles = [c["role"] for c in db.recent_chat(seeded, 10)]
    assert roles == ["user", "assistant"]

def test_chat_extracts_action_block(seeded, monkeypatch):
    reply = ('I will create that goal.\n```action\n'
             '{"type": "create_goal", "name": "Trip", '
             '"goal_type": "save_by_date", "target_ils": 2000, '
             '"target_date": "2026-10-01"}\n```')
    monkeypatch.setattr(advisor.client, "ask_claude", lambda *a, **k: reply)
    r = advisor.chat(seeded, "make a trip goal", TODAY)
    assert r["action"]["type"] == "create_goal"
    assert "```" not in r["text"]

def test_chat_offline(seeded, monkeypatch):
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("x")))
    r = advisor.chat(seeded, "hello?", TODAY)
    assert r["offline"] is True

def test_apply_action_create_goal(seeded):
    advisor.apply_action(seeded, {"type": "create_goal", "name": "Trip",
                                  "goal_type": "save_by_date",
                                  "target_ils": 2000,
                                  "target_date": "2026-10-01"}, TODAY)
    (g,) = db.list_goals(seeded)
    assert g["name"] == "Trip" and g["target_agorot"] == 200000

def test_apply_action_update_budget(seeded):
    advisor.apply_action(seeded, {"type": "update_budget",
                                  "category": "Food out",
                                  "amount_ils": 700}, TODAY)
    food = db.category_id_by_name(seeded, "Food out")
    assert db.get_budgets(seeded)[food] == 70000

def test_apply_action_rejects_unknown_type(seeded):
    import pytest
    with pytest.raises(ValueError):
        advisor.apply_action(seeded, {"type": "rm_rf"}, TODAY)

def test_onboarding_propose(seeded, monkeypatch):
    proposal = {"opening_balance_ils": 5000,
                "transactions": [{"effective_date": "2026-06-05", "amount": 800,
                                  "currency": "ILS", "direction": "expense",
                                  "category": "Groceries",
                                  "description": "month so far",
                                  "merchant": None, "people": None,
                                  "payment_method": "card", "goal_name": None,
                                  "confidence": 0.8}],
                "suggested_budgets": {"Food out": 600, "Groceries": 1200}}
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: json.dumps(proposal))
    p = advisor.onboarding_propose(seeded, "I have 5000, spent 800 groceries",
                                   TODAY)
    assert p["opening_balance_ils"] == 5000
    assert p["suggested_budgets"]["Groceries"] == 1200
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_advisor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.ai.advisor'`

- [ ] **Step 3: Implement `app/ai/advisor.py`**

```python
from __future__ import annotations

import datetime as dt
import json

from app import db
from app.ai import client, prompts
from app.engine import insights
from app.models import ParsedTxn, fmt_ils, to_agorot

_SETTING_WHITELIST = {"salary_day", "salary_amount_agorot", "card_charge_day",
                      "user_name"}


def template_briefing(fp: dict) -> str:
    """Deterministic briefing used when Claude is unreachable."""
    over = [c for c in fp["categories"]
            if not c["is_fixed"] and c["pace_ratio"] > 1.1]
    pace_line = (f" Watch {over[0]['name']} — over pace." if over
                 else " Spending pace looks fine.")
    goal_line = ""
    if fp["goals"]:
        g = fp["goals"][0]
        goal_line = f" {g['emoji']} {g['name']}: {g['pct']}%."
    return (f"Safe to spend today: {fp['safe_to_spend']['today_fmt']}."
            f"{pace_line} Card charges in {fp['card']['days_to_charge']}d"
            f" ({fp['card']['total_fmt']} so far).{goal_line}")


def get_briefing(conn, today: dt.date, force: bool = False) -> dict:
    cached = db.get_briefing(conn, today.isoformat())
    if cached and not force:
        return {"text": cached["text"], "source": "cache"}
    fp = insights.fact_pack(conn, today)
    try:
        text = client.ask_claude(
            prompts.BRIEFING_USER_TMPL.format(facts=json.dumps(fp)),
            system=prompts.BRIEFING_SYSTEM, timeout_s=45).strip()
        db.put_briefing(conn, today.isoformat(), text, json.dumps(fp))
        return {"text": text, "source": "ai"}
    except Exception:
        # template is NOT cached so a later refresh can upgrade to AI
        return {"text": template_briefing(fp), "source": "template"}


def chat(conn, text: str, today: dt.date) -> dict:
    db.add_chat(conn, "user", text)
    fp = insights.fact_pack(conn, today)
    history = "\n".join(f"{c['role'].upper()}: {c['text']}"
                        for c in db.recent_chat(conn, 20)[:-1]) or "(none)"
    try:
        reply = client.ask_claude(
            prompts.CHAT_USER_TMPL.format(facts=json.dumps(fp),
                                          history=history, question=text),
            system=prompts.CHAT_SYSTEM, timeout_s=60)
    except Exception:
        return {"text": "Advisor offline — your data is safe and the numbers on"
                        " the Overview are still live. Try again later.",
                "action": None, "offline": True}
    action = None
    if "```action" in reply:
        head, _, tail = reply.partition("```action")
        block, _, rest = tail.partition("```")
        try:
            action = client.extract_json(block, opener="{")
        except ValueError:
            action = None
        reply = (head + rest).strip()
    reply = reply.strip()
    db.add_chat(conn, "assistant", reply)
    return {"text": reply, "action": action, "offline": False}


def apply_action(conn, action: dict, today: dt.date) -> dict:
    """Apply a user-confirmed advisor action. Raises ValueError on bad input."""
    kind = action.get("type")
    if kind == "create_goal":
        gid = db.add_goal(conn, name=str(action["name"]),
                          type=("save_by_date"
                                if action.get("goal_type") == "save_by_date"
                                else "purchase_fund"),
                          target_agorot=to_agorot(action["target_ils"]),
                          target_date=(dt.date.fromisoformat(action["target_date"])
                                       if action.get("target_date") else None))
        return {"summary": f"Goal '{action['name']}' created", "goal_id": gid}
    if kind == "update_budget":
        cat_id = db.category_id_by_name(conn, str(action["category"]))
        if cat_id is None:
            raise ValueError(f"unknown category {action['category']!r}")
        db.set_budget(conn, cat_id, to_agorot(action["amount_ils"]))
        return {"summary": f"Budget for {action['category']} set to "
                           f"{fmt_ils(to_agorot(action['amount_ils']))}"}
    if kind == "add_transaction":
        from app.ai import parser  # late import avoids a cycle
        p = ParsedTxn(**action["txn"])
        tid = parser._store(conn, p, raw_text="(advisor action)", source="ai")
        return {"summary": "Transaction added", "txn_id": tid}
    if kind == "adjust_setting":
        key = str(action["key"])
        if key not in _SETTING_WHITELIST:
            raise ValueError(f"setting {key!r} is not adjustable via chat")
        db.set_setting(conn, key, str(action["value"]))
        return {"summary": f"{key} updated"}
    raise ValueError(f"unknown action type {kind!r}")


def onboarding_propose(conn, braindump: str, today: dt.date) -> dict:
    cats = ", ".join(c["name"] for c in db.categories(conn) if not c["is_income"])
    salary = int(db.get_setting(conn, "salary_amount_agorot", "0")) // 100
    user = prompts.ONBOARD_USER_TMPL.format(
        today=today.isoformat(), categories=cats, salary=salary,
        salary_day=db.get_setting(conn, "salary_day", "1"), text=braindump)
    reply = client.ask_claude(user, system=prompts.ONBOARD_SYSTEM, timeout_s=90)
    proposal = client.extract_json(reply, opener="{")
    # validate transactions now so confirm can't fail later
    proposal["transactions"] = [ParsedTxn(**t).model_dump(mode="json")
                                for t in proposal.get("transactions", [])]
    return proposal
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_advisor.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: advisor briefing, chat with confirmable actions, onboarding proposal"
```

---

### Task 15: The bridge API (`app/api.py`) + CSV export

Everything the UI can do crosses this class. pywebview runs each `js_api` call on
its own worker thread, so blocking on Claude inside a method is fine (the UI thread
never blocks) — but writes are serialized with a `Lock`.

**Files:**
- Create: `app/api.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write the failing tests**

`tests/test_api.py`:
```python
import datetime as dt
import json

import pytest

from app import db
from app.api import Api

TODAY = dt.date(2026, 6, 11)

GOOD_REPLY = json.dumps([{
    "effective_date": "2026-06-11", "amount": 45, "currency": "ILS",
    "direction": "expense", "category": "Food out", "description": "falafel",
    "merchant": None, "people": None, "payment_method": "card",
    "goal_name": None, "confidence": 0.95}])


@pytest.fixture
def api(tmp_path, monkeypatch):
    a = Api(tmp_path / "ledger.db", backup_dir=tmp_path / "backups",
            today_fn=lambda: TODAY)
    # seed minimal settings so views render
    a.save_settings({"user_name": "Tester", "salary_day": "10",
                     "salary_amount_agorot": "900000", "card_charge_day": "2",
                     "opening_balance_agorot": "500000",
                     "opening_balance_date": "2026-06-01"})
    db.set_budget(a.conn, db.category_id_by_name(a.conn, "Food out"), 60000)
    return a


def test_not_onboarded_until_settings(tmp_path):
    a = Api(tmp_path / "x.db", backup_dir=tmp_path / "b",
            today_fn=lambda: TODAY)
    assert a.is_onboarded() is False

def test_add_entry_roundtrip(api, monkeypatch):
    from app.ai import parser
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: GOOD_REPLY)
    res = api.add_entry("45 falafel")
    assert res["ok"] is True and len(res["entries"]) == 1
    e = res["entries"][0]
    assert e["amount_fmt"] == "-₪45" and e["category_name"] == "Food out"

def test_add_entry_bad_text_is_clean_error(api, monkeypatch):
    from app.ai import client, parser
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("x")))
    res = api.add_entry("nothing numeric here")
    assert res["ok"] is False and "amount" in res["error"]

def test_get_overview_shape(api):
    res = api.get_overview()
    assert res["ok"] is True
    for key in ("safe_to_spend", "categories", "card", "goals", "recent",
                "cycle", "balance"):
        assert key in res, key

def test_undo_and_update(api, monkeypatch):
    from app.ai import parser
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: GOOD_REPLY)
    tid = api.add_entry("45 falafel")["entries"][0]["id"]
    assert api.undo_txn(tid)["ok"] is True
    assert api.list_ledger({})["rows"] == []
    api.restore_txn(tid)
    res = api.update_txn(tid, {"description": "hummus"})
    assert res["ok"] is True
    assert api.list_ledger({})["rows"][0]["description"] == "hummus"

def test_recategorize_learns_rule(api, monkeypatch):
    from app.ai import parser
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: GOOD_REPLY)
    tid = api.add_entry("45 falafel")["entries"][0]["id"]
    fun = db.category_id_by_name(api.conn, "Fun")
    api.update_txn(tid, {"category_id": fun})
    assert db.match_rule(api.conn, "more falafel please") == fun

def test_goal_endpoints(api):
    res = api.save_goal({"name": "Drone", "goal_type": "purchase_fund",
                         "target_ils": 4500})
    assert res["ok"] is True
    assert api.get_goals()["goals"][0]["name"] == "Drone"

def test_csv_export(api, tmp_path, monkeypatch):
    from app.ai import parser
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: GOOD_REPLY)
    api.add_entry("45 falafel")
    res = api.export_csv("2026-06")
    assert res["ok"] is True
    text = open(res["path"], encoding="utf-8-sig").read()
    assert "falafel" in text and "-45.00" in text

def test_onboarding_complete_writes_everything(api, monkeypatch):
    from app.ai import advisor
    proposal = {"opening_balance_ils": 7000,
                "transactions": [{"effective_date": "2026-06-05", "amount": 800,
                                  "currency": "ILS", "direction": "expense",
                                  "category": "Groceries", "description": "so far",
                                  "merchant": None, "people": None,
                                  "payment_method": "card", "goal_name": None,
                                  "confidence": 0.8}],
                "suggested_budgets": {"Groceries": 1200}}
    res = api.onboarding_complete(
        {"user_name": "Orwa", "salary_day": "10",
         "salary_amount_agorot": "900000", "card_charge_day": "2"},
        proposal)
    assert res["ok"] is True
    assert db.get_setting(api.conn, "opening_balance_agorot") == "700000"
    assert len(db.list_transactions(api.conn)) == 1
    groceries = db.category_id_by_name(api.conn, "Groceries")
    assert db.get_budgets(api.conn)[groceries] == 120000
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_api.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.api'`

- [ ] **Step 3: Implement `app/api.py`**

```python
from __future__ import annotations

import csv
import datetime as dt
import functools
import threading
from pathlib import Path

from app import db
from app.ai import advisor, parser
from app.engine import budget, goals as goals_eng, insights
from app.models import fmt_ils, to_agorot

ONBOARD_KEYS = ("user_name", "salary_day", "salary_amount_agorot",
                "card_charge_day")


def _safe(fn):
    """Every bridge method returns a dict; exceptions become {ok: False}."""
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        try:
            out = fn(self, *args, **kwargs)
            out.setdefault("ok", True)
            return out
        except Exception as e:  # surfaced in the UI as a toast
            return {"ok": False, "error": str(e)}
    return wrapper


def _txn_dict(row) -> dict:
    d = dict(row)
    d["amount_fmt"] = fmt_ils(row["amount_agorot"])
    return d


class Api:
    def __init__(self, db_path, *, backup_dir, today_fn=dt.date.today):
        self.conn = db.connect(db_path)
        db.init_db(self.conn)
        self.backup_dir = Path(backup_dir)
        self._today = today_fn
        self._lock = threading.Lock()
        self._window = None  # set by __main__ for focus/quit

    # --- lifecycle -----------------------------------------------------------

    def is_onboarded(self) -> bool:
        return db.get_setting(self.conn, "salary_day") is not None

    @_safe
    def startup(self):
        """Called by the UI once on load: backup + best-effort review resweep."""
        db.write_daily_backup(self.conn, self.backup_dir, self._today())
        try:
            parser.resweep(self.conn, self._today())
        except Exception:
            pass  # offline is fine
        return {"onboarded": self.is_onboarded(),
                "user_name": db.get_setting(self.conn, "user_name", "")}

    # --- entries ---------------------------------------------------------------

    @_safe
    def add_entry(self, text: str):
        with self._lock:
            res = parser.parse_and_store(self.conn, text, self._today())
        ids = res["entries"]
        qmarks = ",".join("?" * len(ids))
        rows = self.conn.execute(
            "SELECT t.*, c.name AS category_name, c.emoji AS category_emoji"
            " FROM transactions t LEFT JOIN categories c ON c.id=t.category_id"
            f" WHERE t.id IN ({qmarks})", ids).fetchall()
        return {"entries": [_txn_dict(r) for r in rows],
                "used_ai": res["used_ai"], "source": res["source"]}

    @_safe
    def undo_txn(self, txn_id: int):
        with self._lock:
            db.soft_delete_transaction(self.conn, txn_id)
        return {}

    @_safe
    def restore_txn(self, txn_id: int):
        with self._lock:
            db.undelete_transaction(self.conn, txn_id)
        return {}

    @_safe
    def update_txn(self, txn_id: int, fields: dict):
        allowed = {"description", "amount_agorot", "category_id",
                   "effective_date", "payment_method", "needs_review"}
        clean = {k: v for k, v in fields.items() if k in allowed}
        with self._lock:
            old = self.conn.execute(
                "SELECT * FROM transactions WHERE id=?", (txn_id,)).fetchone()
            db.update_transaction(self.conn, txn_id, **clean)
            # learn from re-categorization
            if ("category_id" in clean and old
                    and clean["category_id"] != old["category_id"]
                    and old["description"]):
                key = old["description"].strip().lower().split(" with ")[0]
                if key and db.match_rule(self.conn, key) != clean["category_id"]:
                    db.add_rule(self.conn, key, clean["category_id"],
                                created_from_txn=txn_id)
        return {}

    # --- views -----------------------------------------------------------------

    @_safe
    def get_overview(self):
        today = self._today()
        fp = insights.fact_pack(self.conn, today)
        return {"safe_to_spend": fp["safe_to_spend"],
                "categories": fp["categories"], "card": fp["card"],
                "goals": fp["goals"], "cycle": fp["cycle"],
                "balance": fp["balance"],
                "recent": [
                    _txn_dict(r) for r in db.list_transactions(self.conn, limit=5)]}

    @_safe
    def list_ledger(self, filters: dict):
        kw = {}
        if filters.get("month"):           # "2026-06"
            y, m = map(int, filters["month"].split("-"))
            kw["start"] = dt.date(y, m, 1)
            kw["end"] = (dt.date(y + 1, 1, 1) if m == 12
                         else dt.date(y, m + 1, 1)) - dt.timedelta(days=1)
        if filters.get("category_id"):
            kw["category_id"] = int(filters["category_id"])
        if filters.get("text"):
            kw["text"] = filters["text"]
        if filters.get("needs_review"):
            kw["needs_review"] = True
        rows = db.list_transactions(self.conn, limit=500, **kw)
        return {"rows": [_txn_dict(r) for r in rows],
                "categories": [dict(c) for c in db.categories(self.conn)]}

    @_safe
    def get_goals(self):
        reports = goals_eng.goal_report(self.conn, self._today())
        for g in reports:
            g["target_fmt"] = fmt_ils(g["target_agorot"])
            g["progress_fmt"] = fmt_ils(g["progress_agorot"])
            if g["pace_needed_agorot"] is not None:
                g["pace_needed_fmt"] = fmt_ils(g["pace_needed_agorot"])
            if isinstance(g.get("projected_date"), dt.date):
                g["projected_date"] = g["projected_date"].isoformat()
        return {"goals": reports}

    @_safe
    def save_goal(self, g: dict):
        with self._lock:
            if g.get("id"):
                db.update_goal(self.conn, int(g["id"]),
                               name=g["name"],
                               target_agorot=to_agorot(g["target_ils"]),
                               target_date=g.get("target_date"))
            else:
                db.add_goal(self.conn, name=g["name"],
                            emoji=g.get("emoji", "🎯"),
                            type=("save_by_date" if g.get("goal_type") ==
                                  "save_by_date" else "purchase_fund"),
                            target_agorot=to_agorot(g["target_ils"]),
                            target_date=(dt.date.fromisoformat(g["target_date"])
                                         if g.get("target_date") else None))
        return {}

    @_safe
    def archive_goal(self, goal_id: int):
        with self._lock:
            db.update_goal(self.conn, goal_id, status="archived")
        return {}

    # --- advisor -----------------------------------------------------------------

    @_safe
    def get_briefing(self, force: bool = False):
        return advisor.get_briefing(self.conn, self._today(), force=force)

    @_safe
    def chat_send(self, text: str):
        return advisor.chat(self.conn, text, self._today())

    @_safe
    def chat_apply_action(self, action: dict):
        with self._lock:
            return advisor.apply_action(self.conn, action, self._today())

    @_safe
    def get_chat_history(self):
        return {"messages": [dict(c) for c in db.recent_chat(self.conn, 50)]}

    # --- settings & onboarding ------------------------------------------------------

    @_safe
    def get_app_settings(self):
        return {"settings": db.get_settings(self.conn),
                "categories": [dict(c) for c in db.categories(self.conn)],
                "budgets": {str(k): v for k, v in db.get_budgets(self.conn).items()}}

    @_safe
    def save_settings(self, settings: dict):
        with self._lock:
            for k, v in settings.items():
                db.set_setting(self.conn, k, v)
        return {}

    @_safe
    def set_category_budget(self, category_id: int, amount_ils):
        with self._lock:
            db.set_budget(self.conn, int(category_id), to_agorot(amount_ils))
        return {}

    @_safe
    def onboarding_braindump(self, text: str):
        return {"proposal": advisor.onboarding_propose(self.conn, text,
                                                       self._today())}

    @_safe
    def onboarding_complete(self, profile: dict, proposal: dict):
        from app.models import ParsedTxn
        with self._lock, self.conn:
            for k in ONBOARD_KEYS:
                if k in profile:
                    db.set_setting(self.conn, k, profile[k])
            ob = proposal.get("opening_balance_ils") or 0
            db.set_setting(self.conn, "opening_balance_agorot", to_agorot(ob))
            db.set_setting(self.conn, "opening_balance_date",
                           self._today().isoformat())
            for t in proposal.get("transactions", []):
                parser._store(self.conn, ParsedTxn(**t),
                              raw_text="(onboarding)", source="onboarding")
            for name, ils in (proposal.get("suggested_budgets") or {}).items():
                cid = db.category_id_by_name(self.conn, name)
                if cid:
                    db.set_budget(self.conn, cid, to_agorot(ils))
        return {}

    # --- export ----------------------------------------------------------------------

    @_safe
    def export_csv(self, month: str):
        y, m = map(int, month.split("-"))
        start = dt.date(y, m, 1)
        end = (dt.date(y + 1, 1, 1) if m == 12
               else dt.date(y, m + 1, 1)) - dt.timedelta(days=1)
        rows = db.list_transactions(self.conn, start=start, end=end)
        out_dir = Path(__file__).resolve().parent.parent / "exports"
        out_dir.mkdir(exist_ok=True)
        path = out_dir / f"moneypilot-{month}.csv"
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["date", "amount_ils", "direction", "category",
                        "description", "people", "method", "source"])
            for r in rows:
                w.writerow([r["effective_date"], f"{r['amount_agorot']/100:.2f}",
                            r["direction"], r["category_name"] or "",
                            r["description"], r["people"] or "",
                            r["payment_method"], r["source"]])
        return {"path": str(path)}
```

Note on `source` values: transactions written by `onboarding_complete` pass
`source="onboarding"` through `parser._store`, which only flags review for
fallback/low-confidence — exactly what we want for user-confirmed onboarding data.
`parser._store`'s `needs_review` check treats any non-"fallback" source the same way.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_api.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: pywebview bridge API with CSV export and onboarding writes"
```

---

### Task 16: Dev seed, entry point, single-instance guard (`scripts/dev_seed.py`, `app/__main__.py`)

**Files:**
- Create: `scripts/dev_seed.py`, `app/__main__.py`
- Test: `tests/test_main_helpers.py` (paths + seed only; the window itself is manual)

- [ ] **Step 1: Write the failing tests**

`tests/test_main_helpers.py`:
```python
import datetime as dt

from app import db
from app.__main__ import data_dir
from scripts.dev_seed import seed


def test_data_dir_is_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert data_dir() == tmp_path / "MoneyPilot"

def test_seed_populates(tmp_path):
    conn = db.connect(tmp_path / "dev.db")
    db.init_db(conn)
    seed(conn, today=dt.date(2026, 6, 11))
    assert db.get_setting(conn, "salary_day") == "10"
    assert len(db.list_transactions(conn)) > 30
    assert len(db.list_goals(conn)) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python -m pytest tests\test_main_helpers.py -v`
Expected: FAIL — `ModuleNotFoundError` (no `scripts.dev_seed` / `app.__main__`)

Also create an empty `scripts/__init__.py` so the import works.

- [ ] **Step 3: Implement `scripts/dev_seed.py`**

```python
"""Seed a ledger with two months of plausible data for UI work: `--dev` mode."""
from __future__ import annotations

import datetime as dt
import random

from app import db

ITEMS = [  # (category, description, lo_ils, hi_ils, weight)
    ("Food out", "falafel", 18, 45, 5), ("Food out", "coffee", 10, 18, 6),
    ("Food out", "shawarma with karim", 35, 55, 2),
    ("Groceries", "supermarket run", 80, 350, 3),
    ("Transport", "fuel", 180, 280, 1), ("Transport", "bus", 6, 12, 4),
    ("Fun", "cinema", 40, 60, 1), ("Fun", "steam game", 30, 90, 1),
    ("Health", "pharmacy", 20, 80, 1), ("Shopping", "stuff", 40, 200, 1),
]


def seed(conn, today: dt.date) -> None:
    rng = random.Random(42)  # deterministic seed data
    db.set_setting(conn, "user_name", "Orwa")
    db.set_setting(conn, "salary_day", "10")
    db.set_setting(conn, "salary_amount_agorot", "900000")
    db.set_setting(conn, "card_charge_day", "2")
    db.set_setting(conn, "opening_balance_agorot", "650000")
    db.set_setting(conn, "opening_balance_date",
                   (today - dt.timedelta(days=70)).isoformat())
    budgets = {"Food out": 70000, "Groceries": 140000, "Transport": 50000,
               "Bills": 280000, "Fun": 45000, "Health": 30000,
               "Shopping": 40000}
    for name, amt in budgets.items():
        db.set_budget(conn, db.category_id_by_name(conn, name), amt)
    g1 = db.add_goal(conn, name="Drone", emoji="🚁", type="purchase_fund",
                     target_agorot=450000)
    g2 = db.add_goal(conn, name="Summer trip", emoji="✈️", type="save_by_date",
                     target_agorot=1000000,
                     target_date=today + dt.timedelta(days=80))
    for back in range(70, -1, -1):
        day = today - dt.timedelta(days=back)
        if day.day == 10:
            db.add_transaction(conn, effective_date=day, amount_agorot=900000,
                               direction="income",
                               category_id=db.category_id_by_name(conn, "Salary"),
                               description="salary", source="onboarding")
        if day.day == 1:
            db.add_transaction(conn, effective_date=day, amount_agorot=-250000,
                               direction="expense",
                               category_id=db.category_id_by_name(conn, "Bills"),
                               description="rent + bills", source="onboarding")
        if day.day == 12:
            db.add_transaction(conn, effective_date=day, amount_agorot=-60000,
                               direction="goal_contribution",
                               goal_id=(g1 if day.month % 2 else g2),
                               description="monthly saving", source="onboarding")
        for _ in range(rng.choice([0, 1, 1, 2])):
            cat, desc, lo, hi, _w = rng.choices(ITEMS,
                                                weights=[i[4] for i in ITEMS])[0]
            db.add_transaction(
                conn, effective_date=day,
                amount_agorot=-rng.randint(lo * 100, hi * 100),
                direction="expense",
                category_id=db.category_id_by_name(conn, cat),
                description=desc, source="ai", ai_confidence=0.9)


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "dev.db"
    c = db.connect(target)
    db.init_db(c)
    seed(c, dt.date.today())
    print(f"seeded {target}")
```

- [ ] **Step 4: Implement `app/__main__.py`**

```python
"""MoneyPilot entry point.

  pythonw -m app             normal launch (real ledger in %LOCALAPPDATA%)
  python -m app --dev        seeded throwaway ledger + DevTools, console visible
  python -m app --restore F  restore ledger from a backup JSON, then exit
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
from pathlib import Path

import webview

from app import db
from app.api import Api

PROJECT_DIR = Path(__file__).resolve().parent.parent
SINGLETON_PORT_FILE = "port.lock"


def data_dir() -> Path:
    import os
    return Path(os.environ["LOCALAPPDATA"]) / "MoneyPilot"


def _try_focus_running(ddir: Path) -> bool:
    """If another instance is alive, ask it to focus itself and return True."""
    pf = ddir / SINGLETON_PORT_FILE
    if not pf.exists():
        return False
    try:
        port = int(pf.read_text())
        with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
            s.sendall(b"FOCUS\n")
        return True
    except (OSError, ValueError):
        pf.unlink(missing_ok=True)  # stale lock
        return False


def _serve_singleton(ddir: Path, api: Api) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    (ddir / SINGLETON_PORT_FILE).write_text(str(srv.getsockname()[1]))

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
                conn.recv(16)
                conn.close()
                if api._window:
                    api._window.restore()
                    api._window.show()
            except OSError:
                return

    threading.Thread(target=loop, daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true")
    ap.add_argument("--restore", metavar="BACKUP_JSON")
    args = ap.parse_args()

    ddir = data_dir()
    ddir.mkdir(parents=True, exist_ok=True)

    if args.restore:
        conn = db.connect(ddir / "ledger.db")
        db.init_db(conn)
        db.import_json(conn, json.loads(Path(args.restore).read_text(
            encoding="utf-8")))
        print(f"restored from {args.restore}")
        return

    if _try_focus_running(ddir):
        return  # another instance took focus

    if args.dev:
        db_path = ddir / "dev.db"
        if not db_path.exists():
            import datetime as dt
            from scripts.dev_seed import seed
            c = db.connect(db_path)
            db.init_db(c)
            seed(c, dt.date.today())
            c.close()
    else:
        db_path = ddir / "ledger.db"

    api = Api(db_path, backup_dir=PROJECT_DIR / "backups")
    _serve_singleton(ddir, api)

    window = webview.create_window(
        "MoneyPilot", str(PROJECT_DIR / "app" / "ui" / "index.html"),
        js_api=api, width=1180, height=760, min_size=(960, 640),
        background_color="#0d1117")
    api._window = window
    webview.start(debug=args.dev)
    (ddir / SINGLETON_PORT_FILE).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python -m pytest tests\test_main_helpers.py -v`
Expected: `2 passed`

- [ ] **Step 6: Commit**

```powershell
git add -A; git commit -m "feat: entry point, --dev seeding, --restore, single-instance guard"
```

---

### Task 17: UI shell — cockpit chrome, tabs, entry bar (`app/ui/`)

UI tasks have no pytest steps — each ends with a `--dev` visual verification.
`app.js` uses an append-only pattern: Task 17 defines a `renderers` registry and
later tasks append `renderers.x = …` functions, so the file is never rewritten.

**Files:**
- Create: `app/ui/index.html`, `app/ui/app.css`, `app/ui/app.js`

- [ ] **Step 1: Create `app/ui/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoneyPilot</title>
<link rel="stylesheet" href="app.css">
</head>
<body>
<div id="onboarding" class="overlay hidden">
  <div class="ob-card">
    <h1>▮ MONEYPILOT // FIRST FLIGHT</h1>
    <div id="ob-steps">
      <div class="ob-step" data-step="0">
        <label>What's your name?</label>
        <input id="ob-name" class="ob-input" placeholder="Orwa">
      </div>
      <div class="ob-step hidden" data-step="1">
        <label>Monthly salary (₪) and the day it lands</label>
        <input id="ob-salary" class="ob-input" type="number" placeholder="9000">
        <input id="ob-salary-day" class="ob-input" type="number" min="1" max="31"
               placeholder="day it lands, e.g. 10">
      </div>
      <div class="ob-step hidden" data-step="2">
        <label>Credit-card charge day of month</label>
        <input id="ob-card-day" class="ob-input" type="number" min="1" max="31"
               placeholder="e.g. 2">
      </div>
      <div class="ob-step hidden" data-step="3">
        <label>The brain-dump: what do you have right now, and what have you
               spent this month so far? Your own words.</label>
        <textarea id="ob-dump" class="ob-input" rows="6"
          placeholder="I have about 5000 in the bank. This month: rent 2500, ~700 groceries, 300 eating out…"></textarea>
      </div>
      <div class="ob-step hidden" data-step="4">
        <label>Proposal — edit anything, then confirm</label>
        <div id="ob-proposal" class="ob-proposal"></div>
      </div>
    </div>
    <div class="ob-nav">
      <span id="ob-status" class="sub"></span>
      <button id="ob-next" class="btn primary">NEXT ▸</button>
    </div>
  </div>
</div>

<header>
  <span class="logo">▮ MONEYPILOT</span>
  <span id="cycle-info" class="cycle-info"></span>
</header>

<div id="entrybar">
  <input id="entry-input" autocomplete="off"
   placeholder='log it…  e.g. "45 falafel with karim" · "salary landed" · "put 500 in drone fund"'>
  <div id="chips"></div>
</div>

<nav id="tabs">
  <button class="tab active" data-tab="overview">OVERVIEW</button>
  <button class="tab" data-tab="ledger">LEDGER</button>
  <button class="tab" data-tab="goals">GOALS</button>
  <button class="tab" data-tab="advisor">ADVISOR</button>
</nav>

<main>
  <section id="tab-overview" class="tabpane">
    <div class="grid-ov">
      <div class="panel hero">
        <div class="label">SAFE TO SPEND TODAY</div>
        <div id="ov-sts" class="hero-num">—</div>
        <div id="ov-sts-sub" class="sub"></div>
        <div class="gaugewrap"><div id="ov-gauge" class="gauge"></div></div>
        <div id="ov-cycle-sub" class="sub"></div>
      </div>
      <div class="panel">
        <div class="label">CATEGORIES // THIS CYCLE</div>
        <div id="ov-cats"></div>
      </div>
      <div class="panel">
        <div class="label">UPCOMING CARD CHARGE</div>
        <div id="ov-card" class="big-num">—</div>
        <div id="ov-card-sub" class="sub"></div>
        <div class="label" style="margin-top:14px">BALANCE</div>
        <div id="ov-balance" class="sub"></div>
      </div>
      <div class="panel wide">
        <div class="label">BRIEFING
          <button id="ov-brief-refresh" class="mini-btn" title="refresh">↻</button>
        </div>
        <div id="ov-briefing" class="briefing">…</div>
      </div>
      <div class="panel">
        <div class="label">GOALS</div>
        <div id="ov-goals"></div>
      </div>
      <div class="panel wide">
        <div class="label">RECENT</div>
        <div id="ov-recent"></div>
      </div>
    </div>
  </section>

  <section id="tab-ledger" class="tabpane hidden">
    <div class="filters">
      <input id="lg-month" type="month">
      <select id="lg-cat"><option value="">all categories</option></select>
      <input id="lg-text" placeholder="search…">
      <label class="chk"><input id="lg-review" type="checkbox"> needs review</label>
      <button id="lg-export" class="btn">EXPORT CSV</button>
    </div>
    <table id="lg-table">
      <thead><tr><th>date</th><th>amount</th><th>category</th>
        <th>description</th><th>method</th><th></th></tr></thead>
      <tbody id="lg-body"></tbody>
    </table>
  </section>

  <section id="tab-goals" class="tabpane hidden">
    <div id="gl-cards" class="goal-grid"></div>
    <div class="panel add-goal">
      <div class="label">NEW GOAL</div>
      <input id="gl-name" placeholder="name, e.g. Drone">
      <select id="gl-type">
        <option value="purchase_fund">purchase fund</option>
        <option value="save_by_date">save by date</option>
      </select>
      <input id="gl-target" type="number" placeholder="target ₪">
      <input id="gl-date" type="date">
      <button id="gl-save" class="btn primary">CREATE</button>
    </div>
  </section>

  <section id="tab-advisor" class="tabpane hidden">
    <div id="ch-thread" class="chat-thread"></div>
    <div class="chat-inputrow">
      <input id="ch-input" placeholder="ask anything about your money…">
      <button id="ch-send" class="btn primary">SEND</button>
    </div>
  </section>
</main>

<div id="toast" class="toast hidden"></div>
<script src="app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `app/ui/app.css`**

```css
:root {
  --bg: #0d1117; --panel: #141b26; --panel2: #161c26; --line: #26334a;
  --txt: #c7d6ea; --dim: #5b7290; --dim2: #8aa3c2;
  --accent: #4ef0c0; --blue: #2da8ff; --amber: #ffb46b; --red: #ff6b7a;
  --mono: Consolas, "Cascadia Mono", monospace;
}
* { box-sizing: border-box; margin: 0; }
body { background: var(--bg); color: var(--txt); font-family: var(--mono);
       font-size: 14px; height: 100vh; display: flex; flex-direction: column; }
.hidden { display: none !important; }

header { display: flex; justify-content: space-between; align-items: center;
         padding: 10px 16px 4px; }
.logo { color: var(--accent); font-weight: bold; letter-spacing: 2px; }
.cycle-info { color: var(--dim); font-size: 12px; }

#entrybar { padding: 8px 16px 0; }
#entry-input { width: 100%; background: var(--panel2); color: var(--txt);
  border: 1px dashed #3b4a61; border-radius: 8px; padding: 11px 14px;
  font: inherit; outline: none; }
#entry-input:focus { border-color: var(--accent); border-style: solid; }
#chips { display: flex; gap: 6px; flex-wrap: wrap; min-height: 30px;
         padding-top: 6px; }
.chip { display: inline-flex; gap: 8px; align-items: center; font-size: 12px;
  border-radius: 99px; padding: 4px 12px; border: 1px solid var(--line);
  background: var(--panel); }
.chip.ok { color: var(--accent); border-color: #2a5c4d; }
.chip.review { color: var(--amber); border-color: #5c482a; }
.chip.pending { color: var(--dim2); animation: pulse 1.2s infinite; }
.chip button { background: none; border: none; color: var(--dim);
  cursor: pointer; font: inherit; font-size: 11px; }
.chip button:hover { color: var(--red); }
@keyframes pulse { 50% { opacity: .45; } }

nav#tabs { display: flex; gap: 4px; padding: 8px 16px 0; }
.tab { background: var(--panel); color: var(--dim); border: 1px solid var(--line);
  border-radius: 6px 6px 0 0; padding: 7px 18px; cursor: pointer;
  font: inherit; font-size: 12px; letter-spacing: 1px; }
.tab.active { color: var(--accent); border-color: var(--blue);
  border-bottom-color: var(--bg); }

main { flex: 1; overflow-y: auto; padding: 12px 16px 16px; }
.panel { background: var(--panel); border: 1px solid var(--line);
  border-radius: 8px; padding: 13px 15px; }
.label { color: var(--dim); font-size: 11px; letter-spacing: 1.2px;
  margin-bottom: 8px; }
.sub { color: var(--dim2); font-size: 12px; margin-top: 4px; }

.grid-ov { display: grid; grid-template-columns: 1.25fr 1fr 1fr; gap: 10px; }
.grid-ov .wide { grid-column: span 2; }
.hero-num { color: var(--accent); font-size: 44px; font-weight: bold; }
.big-num { color: var(--txt); font-size: 26px; font-weight: bold; }
.gaugewrap { height: 9px; background: var(--bg); border: 1px solid var(--line);
  border-radius: 4px; overflow: hidden; margin-top: 12px; }
.gauge { height: 100%; width: 0;
  background: linear-gradient(90deg, var(--accent), var(--blue)); }

.catrow { margin-bottom: 8px; font-size: 12px; }
.catrow .bar { height: 7px; background: var(--bg); border-radius: 3px;
  overflow: hidden; margin-top: 3px; }
.catrow .fill { height: 100%; background: var(--blue); }
.catrow.over .fill { background: var(--amber); }
.catrow .meta { display: flex; justify-content: space-between;
  color: var(--dim2); }

.briefing { color: var(--dim2); line-height: 1.55; }
.mini-btn { background: none; border: 1px solid var(--line); color: var(--dim);
  border-radius: 4px; cursor: pointer; font-size: 11px; }

.recent-row { display: flex; justify-content: space-between; padding: 4px 0;
  border-bottom: 1px dotted #1d2737; font-size: 12.5px; }
.recent-row .neg { color: var(--txt); } .recent-row .pos { color: var(--accent); }

.filters { display: flex; gap: 8px; margin-bottom: 10px; align-items: center; }
.filters input, .filters select { background: var(--panel); color: var(--txt);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px 9px;
  font: inherit; font-size: 12px; }
.chk { color: var(--dim2); font-size: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 12.5px; }
th { text-align: left; color: var(--dim); font-size: 11px; letter-spacing: 1px;
  padding: 6px 8px; border-bottom: 1px solid var(--line); }
td { padding: 6px 8px; border-bottom: 1px solid #1a2230; }
tr.review td { background: rgba(255, 180, 107, .05); }
td .rowbtn { background: none; border: none; color: var(--dim);
  cursor: pointer; font-size: 12px; }
td .rowbtn:hover { color: var(--accent); }
td input, td select { background: var(--bg); color: var(--txt);
  border: 1px solid var(--blue); border-radius: 4px; font: inherit;
  font-size: 12px; padding: 2px 5px; width: 100%; }

.goal-grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;
  margin-bottom: 12px; }
.goalcard .bar { height: 9px; background: var(--bg); border-radius: 4px;
  overflow: hidden; margin: 8px 0 6px; }
.goalcard .fill { height: 100%; background:
  linear-gradient(90deg, var(--accent), var(--blue)); }
.goalcard .verdict.ready { color: var(--accent); }
.goalcard .verdict.behind { color: var(--amber); }
.add-goal { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.add-goal input, .add-goal select { background: var(--bg); color: var(--txt);
  border: 1px solid var(--line); border-radius: 6px; padding: 7px 9px;
  font: inherit; font-size: 12px; }

.btn { background: var(--panel2); border: 1px solid var(--line);
  color: var(--dim2); border-radius: 6px; padding: 7px 16px; cursor: pointer;
  font: inherit; font-size: 12px; letter-spacing: 1px; }
.btn.primary { color: var(--accent); border-color: #2a5c4d; }
.btn:hover { border-color: var(--accent); }

.chat-thread { display: flex; flex-direction: column; gap: 8px;
  max-height: calc(100vh - 290px); overflow-y: auto; padding-bottom: 10px; }
.bubble { max-width: 72%; padding: 9px 13px; border-radius: 10px;
  font-size: 13px; line-height: 1.5; white-space: pre-wrap; }
.bubble.user { align-self: flex-end; background: #1f2c40;
  border: 1px solid #2b3a4f; }
.bubble.assistant { align-self: flex-start; background: var(--panel);
  border: 1px solid var(--line); color: var(--dim2); }
.actioncard { align-self: flex-start; border: 1px dashed var(--amber);
  border-radius: 8px; padding: 9px 13px; font-size: 12px; color: var(--amber); }
.actioncard button { margin-left: 10px; }
.chat-inputrow { display: flex; gap: 8px; margin-top: 10px; }
.chat-inputrow input { flex: 1; background: var(--panel2); color: var(--txt);
  border: 1px solid var(--line); border-radius: 8px; padding: 10px 13px;
  font: inherit; }

.overlay { position: fixed; inset: 0; background: rgba(5, 8, 12, .94);
  z-index: 50; display: flex; align-items: center; justify-content: center; }
.ob-card { width: 620px; background: var(--panel); border: 1px solid var(--blue);
  border-radius: 12px; padding: 26px; }
.ob-card h1 { color: var(--accent); font-size: 16px; letter-spacing: 2px;
  margin-bottom: 18px; }
.ob-step label { display: block; color: var(--dim2); margin-bottom: 10px;
  line-height: 1.5; }
.ob-input { width: 100%; background: var(--bg); color: var(--txt);
  border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px;
  font: inherit; margin-bottom: 8px; }
.ob-nav { display: flex; justify-content: space-between; align-items: center;
  margin-top: 14px; }
.ob-proposal { max-height: 320px; overflow-y: auto; font-size: 12.5px; }
.ob-proposal .prow { display: flex; gap: 8px; align-items: center;
  padding: 3px 0; color: var(--dim2); }
.ob-proposal input { background: var(--bg); color: var(--txt);
  border: 1px solid var(--line); border-radius: 4px; font: inherit;
  font-size: 12px; padding: 3px 6px; width: 90px; }

.toast { position: fixed; bottom: 18px; right: 18px; background: var(--panel2);
  border: 1px solid var(--amber); color: var(--amber); border-radius: 8px;
  padding: 10px 16px; font-size: 12.5px; z-index: 60; max-width: 420px; }
```

- [ ] **Step 3: Create `app/ui/app.js` (core)**

```javascript
/* MoneyPilot UI core. Later tasks APPEND to this file:
   renderers.<name> render functions and the onboarding flow. */
"use strict";

const $ = (sel) => document.querySelector(sel);
const ready = new Promise((res) => window.addEventListener("pywebviewready", res));

async function api(method, ...args) {
  await ready;
  return window.pywebview.api[method](...args);
}

const renderers = {};            // tab renderers, registered by later tasks
async function refreshAll() {
  for (const fn of Object.values(renderers)) await fn();
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.remove("hidden");
  clearTimeout(toast._h);
  toast._h = setTimeout(() => t.classList.add("hidden"), 5000);
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
              "'": "&#39;" }[c]));
}

/* --- tabs ------------------------------------------------------------- */
function initTabs() {
  document.querySelectorAll(".tab").forEach((b) =>
    b.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((x) =>
        x.classList.toggle("active", x === b));
      document.querySelectorAll(".tabpane").forEach((p) =>
        p.classList.toggle("hidden", p.id !== "tab-" + b.dataset.tab));
    }));
}

/* --- entry bar + chips -------------------------------------------------- */
function addChip(text, cls) {
  const c = document.createElement("span");
  c.className = "chip " + cls;
  c.textContent = text;
  $("#chips").appendChild(c);
  return c;
}

function addUndo(chip, txnId) {
  const b = document.createElement("button");
  b.textContent = "undo";
  b.onclick = async () => {
    await api("undo_txn", txnId);
    chip.remove();
    refreshAll();
  };
  chip.appendChild(b);
}

async function submitEntry() {
  const input = $("#entry-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  const pending = addChip("parsing…", "pending");
  const res = await api("add_entry", text);
  pending.remove();
  if (!res.ok) { toast(res.error); input.value = text; return; }
  for (const e of res.entries) {
    const cls = e.needs_review ? "review" : "ok";
    const icon = e.needs_review ? "⚠" : "✓";
    const chip = addChip(
      `${icon} ${e.category_name ?? "?"} · ${e.amount_fmt} · ${e.description}`,
      cls);
    addUndo(chip, e.id);
    setTimeout(() => chip.remove(), 20000);
  }
  if (res.source === "fallback")
    toast("AI offline — logged with my best guess, flagged for review.");
  refreshAll();
}

/* --- boot --------------------------------------------------------------- */
(async function boot() {
  initTabs();
  $("#entry-input").addEventListener("keydown",
    (e) => { if (e.key === "Enter") submitEntry(); });
  const st = await api("startup");
  if (!st.ok) { toast(st.error); return; }
  if (!st.onboarded) {
    if (typeof window.startOnboarding === "function") window.startOnboarding();
    else toast("Onboarding UI not built yet (Task 19).");
    return;
  }
  await refreshAll();
})();
```

- [ ] **Step 4: Visual verification**

Run: `.venv\Scripts\python -m app --dev`
Expected: dark cockpit window opens; four tabs switch; typing gibberish in the
entry bar and pressing Enter shows a toast (AI may or may not be reachable —
either a logged chip or a clean error, never a crash). Close the window.

- [ ] **Step 5: Commit**

```powershell
git add -A; git commit -m "feat: cockpit UI shell - chrome, tabs, entry bar with chips"
```

---

### Task 18: UI — Overview & Ledger renderers (`app/ui/app.js` append)

**Files:**
- Modify: `app/ui/app.js` (append only)

- [ ] **Step 1: Append the Overview renderer**

```javascript
/* --- OVERVIEW ------------------------------------------------------------ */
renderers.overview = async function renderOverview() {
  const o = await api("get_overview");
  if (!o.ok) { toast(o.error); return; }

  $("#ov-sts").textContent = o.safe_to_spend.today_fmt;
  $("#ov-sts-sub").textContent =
    `${o.safe_to_spend.remaining_fmt} left · ${o.safe_to_spend.days_left} days to salary`;
  const pct = Math.min(100,
    Math.round(100 * o.cycle.day_index / o.cycle.length));
  $("#ov-gauge").style.width = pct + "%";
  $("#ov-cycle-sub").textContent =
    `cycle day ${o.cycle.day_index} of ${o.cycle.length}`;
  $("#cycle-info").textContent =
    `CYCLE ${o.cycle.start} → ${o.cycle.end}`;

  $("#ov-cats").innerHTML = o.categories
    .filter((c) => !c.is_fixed)
    .map((c) => {
      const used = c.budget_agorot ?
        Math.min(100, Math.round(100 * c.spent_agorot / c.budget_agorot)) : 0;
      const over = c.pace_ratio > 1.1 ? " over" : "";
      return `<div class="catrow${over}">
        <div class="meta"><span>${esc(c.emoji)} ${esc(c.name)}</span>
        <span>₪${Math.round(c.spent_agorot / 100)} / ₪${Math.round(
          c.budget_agorot / 100)}</span></div>
        <div class="bar"><div class="fill" style="width:${used}%"></div></div>
      </div>`;
    }).join("");

  $("#ov-card").textContent = o.card.total_fmt;
  $("#ov-card-sub").textContent =
    `charges in ${o.card.days_to_charge}d (${o.card.charge_date})`;
  $("#ov-balance").innerHTML =
    `available ${esc(o.balance.available_fmt)}<br>` +
    `earmarked ₪${Math.round(o.balance.earmarked_agorot / 100)} · ` +
    `total ${esc(o.balance.total_fmt)}`;

  $("#ov-goals").innerHTML = o.goals.map((g) =>
    `<div class="catrow"><div class="meta">
      <span>${esc(g.emoji)} ${esc(g.name)}</span><span>${g.pct}%</span></div>
      <div class="bar"><div class="fill" style="width:${g.pct}%"></div></div>
    </div>`).join("") || `<span class="sub">no goals yet — Goals tab</span>`;

  $("#ov-recent").innerHTML = o.recent.map((r) =>
    `<div class="recent-row"><span>${esc(r.effective_date)} · ${
      esc(r.category_emoji ?? "")} ${esc(r.description)}</span>
     <span class="${r.amount_agorot < 0 ? "neg" : "pos"}">${
      esc(r.amount_fmt)}</span></div>`).join("");

  const b = await api("get_briefing", false);
  $("#ov-briefing").textContent = b.ok ? b.text : "briefing unavailable";
};

$("#ov-brief-refresh").addEventListener("click", async () => {
  $("#ov-briefing").textContent = "…";
  const b = await api("get_briefing", true);
  $("#ov-briefing").textContent = b.ok ? b.text : "briefing unavailable";
});
```

- [ ] **Step 2: Append the Ledger renderer with inline edit**

```javascript
/* --- LEDGER ---------------------------------------------------------------- */
let lgCategories = [];

function lgFilters() {
  return { month: $("#lg-month").value || null,
           category_id: $("#lg-cat").value || null,
           text: $("#lg-text").value || null,
           needs_review: $("#lg-review").checked };
}

renderers.ledger = async function renderLedger() {
  const res = await api("list_ledger", lgFilters());
  if (!res.ok) { toast(res.error); return; }
  lgCategories = res.categories;
  const catSel = $("#lg-cat");
  if (catSel.options.length === 1)
    for (const c of res.categories)
      catSel.add(new Option(`${c.emoji} ${c.name}`, c.id));
  $("#lg-body").innerHTML = res.rows.map((r) => `
    <tr data-id="${r.id}" class="${r.needs_review ? "review" : ""}">
      <td>${esc(r.effective_date)}</td>
      <td>${esc(r.amount_fmt)}</td>
      <td>${esc(r.category_emoji ?? "")} ${esc(r.category_name ?? "")}</td>
      <td>${esc(r.description)}${r.people ? " · " + esc(r.people) : ""}</td>
      <td>${esc(r.payment_method)}</td>
      <td><button class="rowbtn" data-act="edit">✎</button>
          <button class="rowbtn" data-act="del">🗑</button></td>
    </tr>`).join("");
};

function lgEditRow(tr) {
  const id = Number(tr.dataset.id);
  const cells = tr.children;
  const cur = { date: cells[0].textContent,
                amount: cells[1].textContent.replace(/[₪,]/g, ""),
                desc: cells[3].textContent.split(" · ")[0] };
  const catOpts = lgCategories.map((c) =>
    `<option value="${c.id}">${esc(c.emoji)} ${esc(c.name)}</option>`).join("");
  tr.innerHTML = `
    <td><input type="date" value="${esc(cur.date)}"></td>
    <td><input type="number" step="0.01" value="${esc(cur.amount)}"></td>
    <td><select>${catOpts}</select></td>
    <td><input value="${esc(cur.desc)}"></td>
    <td></td>
    <td><button class="rowbtn" data-act="save">✔</button></td>`;
  tr.querySelector("[data-act=save]").onclick = async () => {
    const [d, a, c, t] = tr.querySelectorAll("input, select");
    const ils = parseFloat(a.value);
    const res = await api("update_txn", id, {
      effective_date: d.value,
      amount_agorot: Math.round(ils * 100),   // sign as displayed (− = expense)
      category_id: Number(c.value),
      description: t.value,
      needs_review: 0,
    });
    if (!res.ok) { toast(res.error); return; }
    toast("saved — category rule learned if you re-categorized");
    refreshAll();
  };
}

$("#lg-body").addEventListener("click", async (e) => {
  const btn = e.target.closest("button.rowbtn");
  if (!btn) return;
  const tr = btn.closest("tr");
  const id = Number(tr.dataset.id);
  if (btn.dataset.act === "del") {
    await api("undo_txn", id);
    toast("deleted (soft) — restore from a fresh entry chip or DB if needed");
    refreshAll();
  } else if (btn.dataset.act === "edit") {
    lgEditRow(tr);
  }
});

for (const id of ["lg-month", "lg-cat", "lg-text", "lg-review"])
  $("#" + id).addEventListener("change", () => renderers.ledger());

$("#lg-export").addEventListener("click", async () => {
  const month = $("#lg-month").value ||
    new Date().toISOString().slice(0, 7);
  const res = await api("export_csv", month);
  toast(res.ok ? "exported: " + res.path : res.error);
});
```

- [ ] **Step 3: Visual verification**

Run: `.venv\Scripts\python -m app --dev`
Expected: Overview shows seeded numbers (safe-to-spend, category bars, card
charge, two goals, recent entries; briefing shows AI text or the template).
Ledger lists ~80 rows; filters narrow; ✎ turns a row into inputs and ✔ saves;
🗑 removes a row; EXPORT CSV toasts a file path that exists.

- [ ] **Step 4: Commit**

```powershell
git add -A; git commit -m "feat: overview and ledger renderers with inline edit"
```

---

### Task 19: UI — Goals, Advisor chat, Onboarding (`app/ui/app.js` append)

**Files:**
- Modify: `app/ui/app.js` (append only)

- [ ] **Step 1: Append the Goals renderer**

```javascript
/* --- GOALS ------------------------------------------------------------------ */
renderers.goals = async function renderGoals() {
  const res = await api("get_goals");
  if (!res.ok) { toast(res.error); return; }
  $("#gl-cards").innerHTML = res.goals.map((g) => {
    const verdictCls = g.verdict === "ready" ? "ready"
      : g.verdict === "behind" ? "behind" : "";
    const lines = [
      `${g.progress_fmt} / ${g.target_fmt}`,
      g.pace_needed_fmt ? `needs ${g.pace_needed_fmt}/mo` : null,
      g.projected_date ? `projected ${g.projected_date}` : null,
    ].filter(Boolean).join(" · ");
    return `<div class="panel goalcard" data-id="${g.id}">
      <div class="meta" style="display:flex;justify-content:space-between">
        <b>${esc(g.emoji)} ${esc(g.name)}</b>
        <button class="rowbtn" data-act="arch" title="archive">✕</button></div>
      <div class="bar"><div class="fill" style="width:${g.pct}%"></div></div>
      <div class="sub">${esc(lines)}</div>
      <div class="sub verdict ${verdictCls}">${esc(g.verdict)} · ${g.pct}%</div>
    </div>`;
  }).join("") || `<span class="sub">no active goals</span>`;
};

$("#gl-cards").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-act=arch]");
  if (!btn) return;
  await api("archive_goal", Number(btn.closest(".goalcard").dataset.id));
  refreshAll();
});

$("#gl-save").addEventListener("click", async () => {
  const res = await api("save_goal", {
    name: $("#gl-name").value.trim(),
    goal_type: $("#gl-type").value,
    target_ils: parseFloat($("#gl-target").value),
    target_date: $("#gl-date").value || null,
  });
  if (!res.ok) { toast(res.error); return; }
  $("#gl-name").value = $("#gl-target").value = $("#gl-date").value = "";
  refreshAll();
});
```

- [ ] **Step 2: Append the Advisor chat**

```javascript
/* --- ADVISOR -------------------------------------------------------------------- */
function chatBubble(role, text) {
  const div = document.createElement("div");
  div.className = "bubble " + role;
  div.textContent = text;
  $("#ch-thread").appendChild(div);
  $("#ch-thread").scrollTop = $("#ch-thread").scrollHeight;
  return div;
}

function chatActionCard(action) {
  const div = document.createElement("div");
  div.className = "actioncard";
  div.textContent = "⚡ proposed: " + JSON.stringify(action);
  const btn = document.createElement("button");
  btn.className = "btn primary";
  btn.textContent = "APPLY";
  btn.onclick = async () => {
    const res = await api("chat_apply_action", action);
    toast(res.ok ? res.summary : res.error);
    if (res.ok) { div.remove(); refreshAll(); }
  };
  div.appendChild(btn);
  $("#ch-thread").appendChild(div);
  $("#ch-thread").scrollTop = $("#ch-thread").scrollHeight;
}

renderers.advisor = async function renderAdvisor() {
  const res = await api("get_chat_history");
  if (!res.ok) return;
  $("#ch-thread").innerHTML = "";
  for (const m of res.messages) chatBubble(m.role, m.text);
};

async function chatSend() {
  const input = $("#ch-input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  chatBubble("user", text);
  const thinking = chatBubble("assistant", "…");
  const res = await api("chat_send", text);
  thinking.remove();
  if (!res.ok) { toast(res.error); return; }
  chatBubble("assistant", res.text);
  if (res.offline) toast("advisor offline — numbers on Overview are still live");
  if (res.action) chatActionCard(res.action);
}

$("#ch-send").addEventListener("click", chatSend);
$("#ch-input").addEventListener("keydown",
  (e) => { if (e.key === "Enter") chatSend(); });
```

- [ ] **Step 3: Append the Onboarding flow**

```javascript
/* --- ONBOARDING -------------------------------------------------------------------- */
window.startOnboarding = function startOnboarding() {
  $("#onboarding").classList.remove("hidden");
  let step = 0;
  let proposal = null;

  function show(n) {
    step = n;
    document.querySelectorAll(".ob-step").forEach((s) =>
      s.classList.toggle("hidden", Number(s.dataset.step) !== n));
    $("#ob-next").textContent = n === 4 ? "CONFIRM ✓" : "NEXT ▸";
  }

  function renderProposal(p) {
    const rows = [
      `<div class="prow">opening balance ₪
        <input id="obp-balance" value="${p.opening_balance_ils ?? 0}"></div>`,
      `<div class="prow"><b>month so far:</b></div>`,
      ...(p.transactions || []).map((t, i) =>
        `<div class="prow">${esc(t.effective_date)} · ${esc(t.category)} ·
          ${esc(t.description)} ₪<input data-pi="${i}" value="${t.amount}"></div>`),
      `<div class="prow"><b>suggested budgets (₪/mo):</b></div>`,
      ...Object.entries(p.suggested_budgets || {}).map(([name, ils]) =>
        `<div class="prow">${esc(name)} ₪
          <input data-pb="${esc(name)}" value="${ils}"></div>`),
    ];
    $("#ob-proposal").innerHTML = rows.join("");
  }

  $("#ob-next").onclick = async () => {
    if (step === 0 && !$("#ob-name").value.trim()) return;
    if (step < 3) { show(step + 1); return; }
    if (step === 3) {
      $("#ob-status").textContent = "Claude is reading your dump…";
      const res = await api("onboarding_braindump", $("#ob-dump").value);
      $("#ob-status").textContent = "";
      if (!res.ok) {
        toast("AI unreachable — starting with a blank slate. " + res.error);
        proposal = { opening_balance_ils: 0, transactions: [],
                     suggested_budgets: {} };
      } else {
        proposal = res.proposal;
      }
      renderProposal(proposal);
      show(4);
      return;
    }
    // step 4 → confirm
    proposal.opening_balance_ils = parseFloat($("#obp-balance").value) || 0;
    document.querySelectorAll("[data-pi]").forEach((inp) => {
      proposal.transactions[Number(inp.dataset.pi)].amount =
        parseFloat(inp.value) || 0;
    });
    proposal.transactions = proposal.transactions.filter((t) => t.amount > 0);
    const budgets = {};
    document.querySelectorAll("[data-pb]").forEach((inp) => {
      budgets[inp.dataset.pb] = parseFloat(inp.value) || 0;
    });
    proposal.suggested_budgets = budgets;
    const res = await api("onboarding_complete", {
      user_name: $("#ob-name").value.trim(),
      salary_day: $("#ob-salary-day").value || "1",
      salary_amount_agorot: String(
        Math.round((parseFloat($("#ob-salary").value) || 0) * 100)),
      card_charge_day: $("#ob-card-day").value || "1",
    }, proposal);
    if (!res.ok) { toast(res.error); return; }
    $("#onboarding").classList.add("hidden");
    await refreshAll();
  };

  show(0);
};
```

- [ ] **Step 4: Visual verification — dev mode**

Run: `.venv\Scripts\python -m app --dev`
Expected: Goals tab shows two seeded goal cards with bars and verdicts; creating
a goal adds a card; ✕ archives. Advisor tab: sending a message gets a reply
(AI) or a clean offline message; an action request like *"create a goal: 1000
shekels for headphones"* renders an APPLY card that works.

- [ ] **Step 5: Visual verification — real onboarding**

Run: `.venv\Scripts\python -m app` (real mode, fresh ledger)
Expected: onboarding overlay walks 5 steps; the brain-dump round-trips through
Claude into an editable proposal; CONFIRM lands on a live Overview.
(If Claude is offline it falls back to a blank slate with a toast — still passes.)

- [ ] **Step 6: Commit**

```powershell
git add -A; git commit -m "feat: goals, advisor chat with action cards, onboarding wizard"
```

---

### Task 20: One-click launcher & final verification (`scripts/setup.ps1`)

**Files:**
- Create: `scripts/setup.ps1`, `README.md`

- [ ] **Step 1: Create `scripts/setup.ps1`**

```powershell
# MoneyPilot one-time setup: venv + deps + Desktop shortcut.
# Run from the project root:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path "$root\.venv")) {
    py -3.11 -m venv "$root\.venv"
}
& "$root\.venv\Scripts\python.exe" -m pip install -r "$root\requirements.txt"

$desktop = [Environment]::GetFolderPath("Desktop")
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut("$desktop\MoneyPilot.lnk")
$lnk.TargetPath = "$root\.venv\Scripts\pythonw.exe"
$lnk.Arguments = "-m app"
$lnk.WorkingDirectory = $root
$icon = "$root\app\ui\assets\icon.ico"
if (Test-Path $icon) { $lnk.IconLocation = $icon }
$lnk.Save()

Write-Host "Done. Desktop shortcut 'MoneyPilot' created." -ForegroundColor Green
```

- [ ] **Step 2: Create `README.md`**

```markdown
# MoneyPilot

Local personal-finance cockpit. Natural-language entry → Claude categorizes →
deterministic budgets, cycles, goals, and an AI advisor. Data never leaves this
PC except compact context sent to Claude via your subscription.

## Setup (once)
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
Then double-click **MoneyPilot** on the Desktop.

## Daily use
Type into the entry bar: `45 falafel with karim` · `salary landed` ·
`put 500 in drone fund`. Everything else is tabs.

## Dev
    .venv\Scripts\python -m pytest          # all tests, offline, AI mocked
    .venv\Scripts\python -m app --dev       # seeded fake ledger + DevTools
    .venv\Scripts\python -m app --restore backups\ledger-YYYY-MM-DD.json
```

- [ ] **Step 3: Run the FULL test suite**

Run: `.venv\Scripts\python -m pytest -v`
Expected: **all tests pass** (≈95 tests across 16 files), zero skips related to
our code. Fix anything red before proceeding.

- [ ] **Step 4: Run setup and launch from the shortcut**

Run: `powershell -ExecutionPolicy Bypass -File scripts\setup.ps1`
Expected: green "Done" line; a **MoneyPilot** shortcut appears on the Desktop.
Double-click it: the app opens windowless (no console), onboarding appears on a
fresh ledger. Clicking the shortcut again while open focuses the existing
window instead of starting a second copy.

- [ ] **Step 5: End-to-end smoke checklist (manual, real Claude)**

With the real app (post-onboarding):
1. Entry bar: `45 falafel with karim` → ✓ chip, Food out, ₪45 — undo works.
2. Entry bar: `filled the tank 220 yesterday, also 30 coffee` → two entries,
   correct dates.
3. Overview numbers change after each entry; briefing reads sensibly.
4. Advisor: "can I afford a ₪500 jacket this month?" → numeric, grounded reply.
5. Advisor: "create a goal: 2000 for headphones by October" → APPLY card →
   goal appears in Goals tab.
6. Ledger: re-categorize the falafel to Fun → toast about learned rule; type
   `45 falafel` again → fast-path chip (instant, no AI delay), categorized Fun.
7. Disconnect Wi-Fi, log `50 mystery thing` → amber chip + offline toast;
   reconnect, restart app → entry upgraded by the resweep (review flag gone).
8. `backups\` contains today's `ledger-*.json`.

- [ ] **Step 6: Final commit**

```powershell
git add -A; git commit -m "feat: one-click setup script, README, v1 complete"
```

---

## Plan self-review (spec coverage)

| Spec section | Covered by |
|---|---|
| §1 decisions table | Tasks 11 (subscription transport), 9 (₪+FX), 6 (cycles), 16 (shortcut launch, no tray), 14 (briefing+chat), 5+8 (goals), 19 (onboarding brain-dump), 17–19 (dark tabbed cockpit), whole plan (pywebview stack) |
| §2 architecture & layout | Tasks 1, 15, 16 |
| §3 data model | Tasks 3, 4, 5 (all tables incl. `is_fixed` realizing "Bills excluded") |
| §4 cycle engine + edge cases | Task 6 (incl. leap/clamp/boundary/year-straddle tests) |
| §5 entry pipeline (8 numbered behaviors) | Tasks 12, 13 (fast path, repair retry, fallback+review, resweep, FX, goal contributions, salary shortcut via prompt rules); relative dates: AI path Task 11 prompt, fallback "yesterday" Task 12 |
| §6 AI integration, fact-pack, actions | Tasks 11, 10, 14 |
| §7 UI per tab + onboarding + safe-to-spend formula | Tasks 7 (formula), 17, 18, 19 |
| §8 reliability (WAL, soft delete, backups, single instance, offline) | Tasks 3 (WAL), 4 (soft delete), 5 (backup/prune/restore), 16 (single instance, --restore), 13/14 (offline paths) |
| §9 testing + dev seed | Every task's test steps; Task 16 (dev seed) |
| §10 packaging & launch | Task 20 |

Known consistency points verified: `parser._store` is reused by advisor actions
and onboarding (one write path); `renderers` registry keeps app.js append-only;
`source` values used: `ai | fallback | rule | manual | onboarding`.

**v1 scope guard (YAGNI):** no statement import, no tray, no PDF reports, no
PyInstaller — listed in spec §11 as future.






