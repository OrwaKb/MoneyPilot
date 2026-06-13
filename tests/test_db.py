import sqlite3

import pytest

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


def test_foreign_keys_enforced(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO category_rules(pattern, category_id)"
                     " VALUES('x', 9999)")


def test_amount_sign_convention_enforced(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO transactions(created_at, effective_date,"
                     " amount_agorot, direction) VALUES('t', '2026-06-11',"
                     " 4500, 'expense')")  # expense must be negative


def test_set_setting_overwrites(conn):
    db.set_setting(conn, "k", "1")
    db.set_setting(conn, "k", "2")
    assert db.get_setting(conn, "k") == "2"


def test_connect_sets_busy_timeout(tmp_path):
    # 5000 ms lets a second process wait for the WAL writer instead of
    # failing SQLITE_BUSY immediately — required for the widget process.
    conn = db.connect(tmp_path / "x.db")
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
