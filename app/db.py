from __future__ import annotations

import datetime as dt
import json
import os
import sqlite3
from pathlib import Path

SCHEMA_VERSION = 3

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
  client_uuid TEXT,
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
CREATE TABLE IF NOT EXISTS conversations(
  id INTEGER PRIMARY KEY, title TEXT NOT NULL DEFAULT 'New chat',
  created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_history(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL,
  conversation_id INTEGER REFERENCES conversations(id));
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
    # Explicit (Python's sqlite3 already defaults this to 5000 via timeout=5.0):
    # WAL permits one writer, so the widget's second process must wait for the
    # cockpit's writer instead of failing SQLITE_BUSY. Stated here so it can't
    # silently regress if connect()'s timeout is ever changed.
    conn.execute("PRAGMA busy_timeout=5000")
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
    _migrate(conn)
    # The client_uuid index lives HERE, not in _SCHEMA: by now the column is
    # guaranteed to exist (fresh DBs get it from _SCHEMA's CREATE; upgraded DBs
    # get it from _migrate's ALTER), so this also works for legacy transactions
    # tables that predate the column. Partial → many NULLs, unique non-NULLs.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_txn_client_uuid"
                 " ON transactions(client_uuid) WHERE client_uuid IS NOT NULL")
    conn.commit()


def _adopt_orphan_chats(conn) -> None:
    """Attach chat rows with no conversation (pre-v2 backups, legacy writers)
    to an 'Earlier conversation'. Idempotent: only touches NULL rows."""
    row = conn.execute("SELECT MIN(ts) AS t FROM chat_history"
                       " WHERE conversation_id IS NULL").fetchone()
    if row["t"] is None:
        return
    cid = conn.execute(
        "SELECT id FROM conversations WHERE title='Earlier conversation'"
    ).fetchone()
    cid = cid["id"] if cid else conn.execute(
        "INSERT INTO conversations(title, created_at)"
        " VALUES('Earlier conversation', ?)", (row["t"],)).lastrowid
    conn.execute("UPDATE chat_history SET conversation_id=?"
                 " WHERE conversation_id IS NULL", (cid,))


def _migrate(conn: sqlite3.Connection) -> None:
    """Bring an existing DB up to SCHEMA_VERSION. Fresh DBs are stamped at the
    current version by init_db, so each block here is a no-op for them."""
    row = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    version = int(row["value"]) if row else SCHEMA_VERSION
    if version < 2:
        # chat_history pre-v2 lacks conversation_id (the CREATE in _SCHEMA only
        # adds it for brand-new DBs); add the column on legacy files.
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(chat_history)")}
        if "conversation_id" not in cols:
            conn.execute("ALTER TABLE chat_history ADD COLUMN"
                         " conversation_id INTEGER REFERENCES conversations(id)")
        # adopt any orphaned legacy messages into one 'Earlier conversation'
        _adopt_orphan_chats(conn)
        conn.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
        conn.commit()
    if version < 3:
        # Pocket (phone) sync dedupe key. The CREATE in _SCHEMA only adds it for
        # brand-new DBs; add it (and its partial-unique index) on legacy files.
        cols = {r["name"] for r in conn.execute(
            "PRAGMA table_info(transactions)")}
        if "client_uuid" not in cols:
            conn.execute("ALTER TABLE transactions ADD COLUMN client_uuid TEXT")
        # (the index is created in init_db after _migrate, once the column exists)
        conn.execute("UPDATE meta SET value='3' WHERE key='schema_version'")
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
               "ai_confidence", "needs_review", "client_uuid"}


def _iso(v):
    return v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else v


def add_transaction(conn, *, created_at=None, **kw) -> int:
    bad = set(kw) - _TXN_FIELDS
    if bad:
        raise ValueError(f"unknown transaction fields: {bad}")
    kw = {k: _iso(v) for k, v in kw.items()}
    kw["created_at"] = (_iso(created_at) if created_at
                        else dt.datetime.now().isoformat(timespec="seconds"))
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
    if not kw:
        return
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
    pattern = pattern.strip().lower()
    if not pattern:
        raise ValueError("empty rule pattern")
    conn.execute("INSERT INTO category_rules(pattern, category_id, created_from_txn)"
                 " VALUES(?,?,?)", (pattern, category_id, created_from_txn))
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


# --- goals -------------------------------------------------------------------

_GOAL_FIELDS = {"name", "emoji", "type", "target_agorot", "target_date", "status"}


def add_goal(conn, *, name, type, target_agorot, emoji="🎯", target_date=None) -> int:
    name = name.strip()
    if conn.execute("SELECT 1 FROM goals WHERE status='active'"
                    " AND LOWER(name)=LOWER(?)", (name,)).fetchone():
        raise ValueError(f"goal '{name}' already exists")
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
    if not kw:
        return
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
        "SELECT * FROM goals WHERE status='active' AND LOWER(name) LIKE ?"
        " ORDER BY LENGTH(name), id",
        (f"%{name.strip().lower()}%",)).fetchone()


# --- budgets / briefings / chat ----------------------------------------------

def set_budget(conn, category_id: int, amount_agorot: int) -> None:
    conn.execute("INSERT OR REPLACE INTO budgets(category_id, amount_agorot)"
                 " VALUES(?,?)", (category_id, amount_agorot))
    conn.commit()


def delete_budget(conn, category_id: int) -> None:
    conn.execute("DELETE FROM budgets WHERE category_id=?", (category_id,))
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


def add_conversation(conn, title: str) -> int:
    title = (title or "").strip() or "New chat"
    cur = conn.execute(
        "INSERT INTO conversations(title, created_at) VALUES(?,?)",
        (title, dt.datetime.now().isoformat(timespec="seconds")))
    conn.commit()
    return cur.lastrowid


def list_conversations(conn) -> list:
    # last_ts orders by activity; MAX(h.id) is the tie-break so two chats
    # touched in the same wall-clock second still sort by true recency.
    rows = conn.execute(
        "SELECT c.id, c.title, c.created_at,"
        " COALESCE(MAX(h.ts), c.created_at) AS last_ts,"
        " COUNT(h.id) AS msg_count"
        " FROM conversations c"
        " LEFT JOIN chat_history h ON h.conversation_id = c.id"
        " GROUP BY c.id"
        " ORDER BY last_ts DESC, MAX(h.id) DESC, c.id DESC")
    return [dict(r) for r in rows]


def delete_conversation(conn, conversation_id: int) -> None:
    # chats aren't money — hard-delete the messages then the conversation row
    conn.execute("DELETE FROM chat_history WHERE conversation_id=?",
                 (conversation_id,))
    conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))
    conn.commit()


def add_chat(conn, role: str, text: str, conversation_id=None) -> None:
    conn.execute(
        "INSERT INTO chat_history(ts, role, text, conversation_id)"
        " VALUES(?,?,?,?)",
        (dt.datetime.now().isoformat(timespec="seconds"), role, text,
         conversation_id))
    conn.commit()


def recent_chat(conn, n: int, conversation_id=None) -> list:
    if conversation_id is None:
        rows = list(conn.execute(
            "SELECT * FROM chat_history ORDER BY id DESC LIMIT ?", (n,)))
    else:
        rows = list(conn.execute(
            "SELECT * FROM chat_history WHERE conversation_id=?"
            " ORDER BY id DESC LIMIT ?", (conversation_id, n)))
    return rows[::-1]  # oldest first


# --- backup / restore ----------------------------------------------------------

_EXPORT_TABLES = ["categories", "goals", "transactions", "category_rules",
                  "budgets", "settings", "conversations", "chat_history",
                  "briefings"]


def export_json(conn) -> dict:
    out = {"schema_version": SCHEMA_VERSION}
    # One consistent snapshot for ALL tables: under WAL a single deferred
    # transaction fixes the read view at the first SELECT, so a concurrent
    # writer (the second widget process committing an add_entry/undo) can't tear
    # the backup between two table reads. Only open one if not already inside a
    # transaction.
    started = not conn.in_transaction
    if started:
        conn.execute("BEGIN")
    try:
        for t in _EXPORT_TABLES:
            out[t] = [dict(r) for r in conn.execute(f"SELECT * FROM {t}")]
    finally:
        if started:
            conn.commit()
    return out


def import_json(conn, data: dict) -> None:
    raw_version = data.get("schema_version")
    try:
        schema_version = int(raw_version)
    except (TypeError, ValueError):
        schema_version = None
    if schema_version is None or not (1 <= schema_version <= SCHEMA_VERSION):
        raise ValueError("not a MoneyPilot backup (or schema version mismatch)")
    if not data.get("categories"):
        raise ValueError("not a MoneyPilot backup (or schema version mismatch)")
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
        # adopt orphan chat rows from pre-v2 backups (idempotent)
        _adopt_orphan_chats(conn)
    conn.commit()


def write_daily_backup(conn, backup_dir, today: dt.date):
    """Write backups/ledger-YYYY-MM-DD.json once per day; prune to 30 files."""
    bdir = Path(backup_dir)
    bdir.mkdir(parents=True, exist_ok=True)
    target = bdir / f"ledger-{today.isoformat()}.json"
    if target.exists():
        return None
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(export_json(conn), ensure_ascii=False, indent=1),
                   encoding="utf-8")
    os.replace(tmp, target)
    backups = sorted(bdir.glob("ledger-*.json"))
    for old in backups[:-30]:
        try:
            old.unlink()
        except OSError:
            pass  # OneDrive may hold a lock; retry naturally next day
    return target
