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

def test_save_settings_skips_none(api):
    api.save_settings({"user_name": None})
    assert db.get_setting(api.conn, "user_name") == "Tester"  # unchanged

def test_onboarding_rejects_bad_day_without_writing(api):
    res = api.onboarding_complete(
        {"user_name": "X", "salary_day": "0",
         "salary_amount_agorot": "900000", "card_charge_day": "2"},
        {"opening_balance_ils": 100, "transactions": [],
         "suggested_budgets": {}})
    assert res["ok"] is False and "1..31" in res["error"]
    assert db.get_setting(api.conn, "user_name") == "Tester"  # nothing written

def test_recategorize_to_income_does_not_learn_rule(api, monkeypatch):
    from app.ai import parser
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: GOOD_REPLY)
    tid = api.add_entry("45 falafel")["entries"][0]["id"]
    salary = db.category_id_by_name(api.conn, "Salary")
    api.update_txn(tid, {"category_id": salary})
    assert db.match_rule(api.conn, "more falafel please") is None

def test_save_goal_rejects_nonpositive_target(api):
    res = api.save_goal({"name": "Bad", "goal_type": "purchase_fund",
                         "target_ils": -10})
    assert res["ok"] is False
    assert api.get_goals()["goals"] == []

def test_set_category_budget_rejects_nonpositive(api):
    food = db.category_id_by_name(api.conn, "Food out")
    res = api.set_category_budget(food, 0)
    assert res["ok"] is False
    assert db.get_budgets(api.conn)[food] == 60000  # unchanged

def test_add_entry_empty_ai_reply_engages_fallback(api, monkeypatch):
    from app.ai import parser
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: "[]")
    res = api.add_entry("45 falafel")
    assert res["ok"] is True and len(res["entries"]) == 1
    assert res["entries"][0]["needs_review"] == 1  # fallback-flagged, not lost

def test_save_goal_update_rejects_junk_date(api):
    api.save_goal({"name": "Trip", "goal_type": "save_by_date",
                   "target_ils": 2000, "target_date": "2026-10-01"})
    gid = api.get_goals()["goals"][0]["id"]
    res = api.save_goal({"id": gid, "name": "Trip", "target_ils": 2000,
                         "target_date": "junk"})
    assert res["ok"] is False
    assert api.get_goals()["ok"] is True  # view still renders

def test_update_txn_wrong_sign_clean_error(api, monkeypatch):
    from app.ai import parser
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: GOOD_REPLY)
    tid = api.add_entry("45 falafel")["entries"][0]["id"]
    res = api.update_txn(tid, {"amount_agorot": 4500})  # positive expense
    assert res["ok"] is False and "sign" in res["error"]
    assert api.list_ledger({})["rows"][0]["amount_agorot"] == -4500

def test_chat_send_offline(api, monkeypatch):
    from app.ai import advisor as adv
    monkeypatch.setattr(adv.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            __import__("app.ai.client", fromlist=["AIUnavailable"]
                                       ).AIUnavailable("x")))
    res = api.chat_send("hello?")
    assert res["ok"] is True and res["offline"] is True

def test_chat_send_returns_conversation_id(api, monkeypatch):
    from app.ai import advisor as adv
    monkeypatch.setattr(adv.client, "ask_claude", lambda *a, **k: "sure thing")
    res = api.chat_send("how am I doing?")
    assert res["ok"] is True
    assert res["conversation_id"] is not None
    assert res["title"] == "how am I doing?"


def test_list_chats_shape(api, monkeypatch):
    from app.ai import advisor as adv
    monkeypatch.setattr(adv.client, "ask_claude", lambda *a, **k: "ok")
    cid = api.chat_send("first chat")["conversation_id"]
    res = api.list_chats()
    assert res["ok"] is True
    assert isinstance(res["chats"], list) and len(res["chats"]) == 1
    row = res["chats"][0]
    for key in ("id", "title", "created_at", "last_ts", "msg_count"):
        assert key in row, key
    assert row["id"] == cid


def test_get_chat_history_filters_by_conversation(api, monkeypatch):
    from app.ai import advisor as adv
    monkeypatch.setattr(adv.client, "ask_claude", lambda *a, **k: "reply-a")
    a_id = api.chat_send("alpha")["conversation_id"]
    monkeypatch.setattr(adv.client, "ask_claude", lambda *a, **k: "reply-b")
    b_id = api.chat_send("bravo")["conversation_id"]
    msgs_a = api.get_chat_history(a_id)["messages"]
    assert [m["text"] for m in msgs_a] == ["alpha", "reply-a"]
    msgs_b = api.get_chat_history(b_id)["messages"]
    assert [m["text"] for m in msgs_b] == ["bravo", "reply-b"]
    # unfiltered still returns everything (back-compat)
    assert len(api.get_chat_history()["messages"]) == 4


def test_delete_chat_removes_conversation_and_messages(api, monkeypatch):
    from app.ai import advisor as adv
    monkeypatch.setattr(adv.client, "ask_claude", lambda *a, **k: "ok")
    cid = api.chat_send("to be deleted")["conversation_id"]
    res = api.delete_chat(cid)
    assert res["ok"] is True
    assert api.list_chats()["chats"] == []
    assert api.get_chat_history(cid)["messages"] == []


def test_startup_smoke(api, monkeypatch):
    from app.ai import client, parser
    monkeypatch.setattr(parser.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("x")))
    res = api.startup()
    assert res["ok"] is True and res["onboarded"] is True
    assert list(api.backup_dir.glob("ledger-*.json"))  # backup written
