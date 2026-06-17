import datetime as dt

import pytest

from app import db, pocket
from app.ai import parser

TODAY = dt.date(2026, 6, 11)


@pytest.fixture(autouse=True)
def offline_ai(monkeypatch):
    # Force the deterministic offline fallback parser (no real Claude in tests).
    monkeypatch.setattr(parser, "_ai_parse",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("offline")))


def _entry(uuid, raw, when="2026-06-11T10:00:00"):
    return {"uuid": uuid, "raw_text": raw, "created_at": when}


def test_ingest_stores_new_entry(conn):
    out = pocket.ingest(conn, [_entry("u1", "45 falafel")], TODAY)
    assert out == ["u1"]
    rows = db.list_transactions(conn)
    assert len(rows) == 1
    assert rows[0]["client_uuid"] == "u1"
    assert rows[0]["source"] == "pocket"
    assert rows[0]["amount_agorot"] == -4500     # ₪45 expense → negative


def test_ingest_is_idempotent(conn):
    e = [_entry("u1", "45 falafel")]
    pocket.ingest(conn, e, TODAY)
    out2 = pocket.ingest(conn, e, TODAY)          # same uuid again
    assert out2 == ["u1"]                          # acked
    assert len(db.list_transactions(conn)) == 1    # but NOT double-counted


def test_ingest_uses_the_phone_date(conn):
    pocket.ingest(conn, [_entry("u1", "20 bus", "2026-06-08T09:00:00")], TODAY)
    assert db.list_transactions(conn)[0]["effective_date"] == "2026-06-08"


def test_ingest_bad_date_falls_back_to_today(conn):
    pocket.ingest(conn, [_entry("u1", "20 bus", "not-a-date")], TODAY)
    assert db.list_transactions(conn)[0]["effective_date"] == TODAY.isoformat()


def test_ingest_no_amount_is_acked_not_stored(conn):
    out = pocket.ingest(conn, [_entry("u9", "hello there")], TODAY)
    assert out == ["u9"]                            # acked so the phone stops retrying
    assert db.list_transactions(conn) == []         # nothing logged


def test_ingest_skips_entry_without_uuid(conn):
    out = pocket.ingest(conn, [{"raw_text": "45 falafel"}], TODAY)
    assert out == []
    assert db.list_transactions(conn) == []


def test_ingest_multi_txn_keeps_uuid_on_first_and_dedups(conn, monkeypatch):
    from app.models import ParsedTxn
    two = [ParsedTxn(effective_date=TODAY, amount=45, currency="ILS",
                     direction="expense", category="Food out", description="falafel",
                     merchant=None, people=None, payment_method="card",
                     goal_name=None, confidence=0.9),
           ParsedTxn(effective_date=TODAY, amount=20, currency="ILS",
                     direction="expense", category="Transport", description="bus",
                     merchant=None, people=None, payment_method="card",
                     goal_name=None, confidence=0.9)]
    monkeypatch.setattr(parser, "_ai_parse", lambda *a, **k: two)
    e = [_entry("m1", "45 falafel and 20 bus")]
    assert pocket.ingest(conn, e, TODAY) == ["m1"]
    rows = db.list_transactions(conn)
    assert len(rows) == 2
    uuids = sorted((r["client_uuid"] for r in rows), key=lambda x: (x is None, x))
    assert uuids == ["m1", None]                    # only the first row carries it
    pocket.ingest(conn, e, TODAY)                    # re-sync
    assert len(db.list_transactions(conn)) == 2      # still not doubled


def test_get_token_is_stable(conn):
    assert pocket.get_token(conn) and pocket.get_token(conn) == pocket.get_token(conn)
