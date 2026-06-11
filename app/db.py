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
  needs_review INTEGER NOT NULL DEFAULT 0, deleted_at TEXT,
  CHECK((direction='income' AND amount_agorot>0)
        OR (direction!='income' AND amount_agorot<0)));
CREATE INDEX IF NOT EXISTS idx_txn_date ON transactions(effective_date);
CREATE TABLE IF NOT EXISTS category_rules(
  id INTEGER PRIMARY KEY, pattern TEXT NOT NULL,
  category_id INTEGER NOT NULL REFERENCES categories(id), created_from_txn INTEGER REFERENCES transactions(id));
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
    conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)",
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
