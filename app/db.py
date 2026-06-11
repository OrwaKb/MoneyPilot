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


# --- budgets --------------------------------------------------------------

def set_budget(conn, category_id, amount_agorot) -> None:
    conn.execute("INSERT OR REPLACE INTO budgets(category_id, amount_agorot) VALUES(?,?)",
                 (category_id, amount_agorot))
    conn.commit()
