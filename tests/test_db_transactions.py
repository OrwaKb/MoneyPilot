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
