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


def test_unknown_currency_offline_never_loses_entry(seeded, monkeypatch):
    monkeypatch.setattr(parser.fx, "_fetch",
                        lambda: (_ for _ in ()).throw(OSError()))
    reply = json.dumps([{"effective_date": "2026-06-11", "amount": 1000,
                         "currency": "JPY", "direction": "expense",
                         "category": "Fun", "description": "arcade",
                         "merchant": None, "people": None,
                         "payment_method": "card", "goal_name": None,
                         "confidence": 0.9}])
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: reply)
    res = parser.parse_and_store(seeded, "1000 yen arcade", TODAY)
    (row,) = db.list_transactions(seeded)
    assert row["needs_review"] == 1
    assert "unconverted" in row["description"]
    assert row["currency_orig"] == "JPY"

def test_resweep_skips_unconvertible_rows_without_crashing(seeded, monkeypatch):
    monkeypatch.setattr(parser.fx, "_fetch",
                        lambda: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("offline")))
    parser.parse_and_store(seeded, "45 falafel", TODAY)
    jpy = json.dumps([{"effective_date": "2026-06-11", "amount": 1000,
                       "currency": "JPY", "direction": "expense",
                       "category": "Fun", "description": "arcade",
                       "merchant": None, "people": None,
                       "payment_method": "card", "goal_name": None,
                       "confidence": 0.9}])
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: jpy)
    assert parser.resweep(seeded, TODAY) == 0  # skipped, not crashed
    assert db.list_transactions(seeded)[0]["needs_review"] == 1


def test_tiny_amount_never_crashes_entry(seeded, monkeypatch):
    tiny = json.loads(GOOD_REPLY); tiny[0]["amount"] = 0.004
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: json.dumps(tiny))
    parser.parse_and_store(seeded, "0.004 falafel", TODAY)
    (row,) = db.list_transactions(seeded)
    assert row["amount_agorot"] == -1 and row["needs_review"] == 1

def test_resweep_parses_against_entry_date(seeded, monkeypatch):
    db.add_transaction(seeded, created_at="2026-06-08T12:00:00",
                       effective_date=dt.date(2026, 6, 7),
                       amount_agorot=-4500, direction="expense",
                       category_id=db.category_id_by_name(seeded, "Other"),
                       description="45 falafel yesterday",
                       raw_text="45 falafel yesterday",
                       source="fallback", needs_review=1)
    seen = {}
    def fake_ai(conn, text, today):
        seen["today"] = today
        raise client.AIUnavailable("stop")
    monkeypatch.setattr(parser, "_ai_parse", fake_ai)
    parser.resweep(seeded, TODAY)
    assert seen["today"] == dt.date(2026, 6, 8)  # entry day, not sweep day

def test_fast_path_skips_income_categories(seeded, monkeypatch):
    sal = db.category_id_by_name(seeded, "Salary")
    db.add_rule(seeded, "salary landed", sal)
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("offline")))
    parser.parse_and_store(seeded, "9000 salary landed", TODAY)
    row = db.list_transactions(seeded)[0]
    assert row["direction"] == "income" and row["amount_agorot"] > 0


def test_resweep_resolves_goal_link(seeded, monkeypatch):
    gid = db.add_goal(seeded, name="Drone", type="purchase_fund",
                      target_agorot=450000)
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("offline")))
    parser.parse_and_store(seeded, "500 put into drone fund", TODAY)
    reply = json.dumps([{"effective_date": "2026-06-11", "amount": 500,
                         "currency": "ILS", "direction": "goal_contribution",
                         "category": "Other", "description": "drone fund",
                         "merchant": None, "people": None,
                         "payment_method": "transfer", "goal_name": "drone",
                         "confidence": 0.9}])
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: reply)
    assert parser.resweep(seeded, TODAY) == 1
    row = db.list_transactions(seeded)[0]
    assert row["goal_id"] == gid and row["needs_review"] == 0


def test_resweep_unresolved_goal_stays_in_review(seeded, monkeypatch):
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("offline")))
    parser.parse_and_store(seeded, "500 put into mystery fund", TODAY)
    reply = json.dumps([{"effective_date": "2026-06-11", "amount": 500,
                         "currency": "ILS", "direction": "goal_contribution",
                         "category": "Other", "description": "mystery fund",
                         "merchant": None, "people": None,
                         "payment_method": "transfer", "goal_name": "mystery",
                         "confidence": 0.9}])
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: reply)
    parser.resweep(seeded, TODAY)
    row = db.list_transactions(seeded)[0]
    assert row["goal_id"] is None and row["needs_review"] == 1


def test_fast_path_bails_on_date_words(seeded, monkeypatch):
    food = db.category_id_by_name(seeded, "Food out")
    db.add_rule(seeded, "falafel", food)
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: GOOD_REPLY)
    res = parser.parse_and_store(seeded, "45 falafel yesterday", TODAY)
    assert res["used_ai"] is True  # rule exists but date word forces AI path
