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


def test_apply_action_validates_day_settings(seeded):
    import pytest
    with pytest.raises(ValueError):
        advisor.apply_action(seeded, {"type": "adjust_setting",
                                      "key": "salary_day", "value": "0"}, TODAY)
    with pytest.raises(ValueError):
        advisor.apply_action(seeded, {"type": "adjust_setting",
                                      "key": "card_charge_day",
                                      "value": "32"}, TODAY)
    advisor.apply_action(seeded, {"type": "adjust_setting",
                                  "key": "salary_day", "value": "15"}, TODAY)
    assert db.get_setting(seeded, "salary_day") == "15"

def test_apply_action_validates_salary_amount(seeded):
    import pytest
    with pytest.raises(ValueError):
        advisor.apply_action(seeded, {"type": "adjust_setting",
                                      "key": "salary_amount_agorot",
                                      "value": "6000 shekels"}, TODAY)
    advisor.apply_action(seeded, {"type": "adjust_setting",
                                  "key": "salary_amount_agorot",
                                  "value": "600000"}, TODAY)
    assert db.get_setting(seeded, "salary_amount_agorot") == "600000"

def test_template_briefing_survives_unbudgeted_spend(seeded, monkeypatch):
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-50000,
                       direction="expense",
                       category_id=db.category_id_by_name(seeded, "Health"))
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("x")))
    b = advisor.get_briefing(seeded, TODAY)  # Health has pace_ratio None
    assert b["source"] == "template" and "₪" in b["text"]

def test_onboarding_clamps_today_dates_to_yesterday(seeded, monkeypatch):
    proposal = {"opening_balance_ils": 5000,
                "transactions": [{"effective_date": TODAY.isoformat(),
                                  "amount": 100, "currency": "ILS",
                                  "direction": "expense", "category": "Fun",
                                  "description": "today thing",
                                  "merchant": None, "people": None,
                                  "payment_method": "card", "goal_name": None,
                                  "confidence": 0.8}],
                "suggested_budgets": {}}
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: json.dumps(proposal))
    p = advisor.onboarding_propose(seeded, "spent 100 today", TODAY)
    assert p["transactions"][0]["effective_date"] == \
        (TODAY - dt.timedelta(days=1)).isoformat()
