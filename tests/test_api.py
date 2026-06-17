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


def test_startup_reports_version(api):
    from app import version
    assert api.startup()["version"] == version.__version__


def test_check_update_bridges(api, monkeypatch):
    from app import update
    monkeypatch.setattr(update, "check_for_update",
                        lambda *a, **k: {"update_available": True, "version": "9.9"})
    out = api.check_update()
    assert out["ok"] is True and out["update_available"] is True


def test_open_external_allows_https(api, monkeypatch):
    seen = {}
    import webbrowser
    monkeypatch.setattr(webbrowser, "open", lambda u: seen.setdefault("u", u))
    assert api.open_external("https://github.com/x/y/releases")["ok"] is True
    assert seen["u"].startswith("https://")


def test_open_external_rejects_non_web_scheme(api, monkeypatch):
    import webbrowser
    monkeypatch.setattr(webbrowser, "open",
                        lambda u: (_ for _ in ()).throw(AssertionError("opened!")))
    out = api.open_external("file:///C:/Windows/System32")
    assert out["ok"] is False and out["error_kind"] == "user"


def test_ai_status_bridges_client(api, monkeypatch):
    from app.ai import client
    monkeypatch.setattr(client, "ai_auth_status",
                        lambda *a, **k: {"connected": True, "plan": "pro"})
    out = api.ai_status()
    assert out["ok"] is True and out["connected"] is True and out["plan"] == "pro"


def test_connect_ai_invokes_login(api, monkeypatch):
    from app.ai import client
    called = {}
    monkeypatch.setattr(client, "start_ai_login",
                        lambda: called.setdefault("hit", True))
    assert api.connect_ai()["ok"] is True
    assert called.get("hit") is True


def test_connect_ai_reports_error_cleanly(api, monkeypatch):
    from app.ai import client
    def boom():
        raise client.AIUnavailable("no claude")
    monkeypatch.setattr(client, "start_ai_login", boom)
    out = api.connect_ai()
    assert out["ok"] is False and "claude" in out["error"].lower()


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

def test_save_settings_validates_known_keys(api):
    bad_day = api.save_settings({"salary_day": "40"})
    assert bad_day["ok"] is False and bad_day["error_kind"] == "user"
    bad_amt = api.save_settings({"salary_amount_agorot": "-5"})
    assert bad_amt["ok"] is False
    bad_pm = api.save_settings({"default_payment_method": "bitcoin"})
    assert bad_pm["ok"] is False
    # salary must be WHOLE shekels (agorot divisible by 100) — the convention the
    # whole-shekel UI relies on, so the Settings round-trip is lossless
    bad_frac = api.save_settings({"salary_amount_agorot": "600050"})
    assert bad_frac["ok"] is False and bad_frac["error_kind"] == "user"
    # the bad values must NOT have been written
    assert db.get_setting(api.conn, "salary_day") == "10"        # seeded value
    assert db.get_setting(api.conn, "salary_amount_agorot") == "900000"


def test_save_settings_accepts_valid(api):
    res = api.save_settings({"user_name": "Orwa", "salary_day": "15",
                             "card_charge_day": "3",
                             "salary_amount_agorot": "600000",
                             "default_payment_method": "cash"})
    assert res["ok"] is True
    assert db.get_setting(api.conn, "salary_day") == "15"
    assert db.get_setting(api.conn, "default_payment_method") == "cash"
    assert db.get_setting(api.conn, "user_name") == "Orwa"


def test_remove_category_budget(api):
    cid = db.category_id_by_name(api.conn, "Food out")
    assert db.get_budgets(api.conn).get(cid) == 60000       # seeded
    assert api.remove_category_budget(cid)["ok"] is True
    assert cid not in db.get_budgets(api.conn)


def test_update_txn_bad_category_message(api):
    # a FK violation (unknown category) must NOT be mislabeled as an amount-sign
    # error — the user edited the category, not the amount
    tid = db.add_transaction(api.conn, effective_date=TODAY, amount_agorot=-4500,
                             direction="expense",
                             category_id=db.category_id_by_name(api.conn, "Food out"),
                             description="x")
    res = api.update_txn(tid, {"category_id": 999999})
    assert res["ok"] is False
    assert "sign" not in res["error"].lower()
    assert "categ" in res["error"].lower()


def test_safe_tags_user_vs_internal_error_kind(api, monkeypatch):
    # a validation ValueError is user-facing; an unexpected error is internal
    # (so the web layer can hide internal detail from a remote client)
    user = api.save_goal({"name": "", "target_ils": 100})
    assert user["ok"] is False and user["error_kind"] == "user"
    from app.engine import insights
    monkeypatch.setattr(insights, "fact_pack",
                        lambda *a, **k: (_ for _ in ()).throw(
                            RuntimeError("boom /secret/path")))
    internal = api.get_overview()
    assert internal["ok"] is False and internal["error_kind"] == "internal"


def test_get_overview_shape(api):
    res = api.get_overview()
    assert res["ok"] is True
    for key in ("safe_to_spend", "categories", "card", "goals", "recent",
                "cycle", "balance"):
        assert key in res, key

def test_overview_spark_daily_expenses(api, monkeypatch):
    # salary_day=10, TODAY=2026-06-11 -> cycle start 2026-06-10, day_index=2
    from app.ai import parser
    reply = json.dumps([
        {"effective_date": "2026-06-10", "amount": 30, "currency": "ILS",
         "direction": "expense", "category": "Food out",
         "description": "shawarma", "merchant": None, "people": None,
         "payment_method": "card", "goal_name": None, "confidence": 0.95},
        {"effective_date": "2026-06-11", "amount": 45, "currency": "ILS",
         "direction": "expense", "category": "Food out",
         "description": "falafel", "merchant": None, "people": None,
         "payment_method": "card", "goal_name": None, "confidence": 0.95}])
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: reply)
    api.add_entry("30 shawarma yesterday, 45 falafel")
    res = api.get_overview()
    assert res["ok"] is True
    assert res["spark"] == [3000, 4500]   # agorot per day, zero-filled
    assert len(res["spark"]) == res["cycle"]["day_index"]

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

def _txn(amount, *, date="2026-06-05", cat="Groceries", desc="month so far"):
    return {"effective_date": date, "amount": amount, "currency": "ILS",
            "direction": "expense", "category": cat, "description": desc,
            "merchant": None, "people": None, "payment_method": "card",
            "goal_name": None, "confidence": 0.8}

def test_onboarding_rejects_fractional_transaction_without_writing(api):
    res = api.onboarding_complete(
        {"user_name": "X", "salary_day": "10",
         "salary_amount_agorot": "900000", "card_charge_day": "2"},
        {"opening_balance_ils": 1000, "transactions": [_txn(47.9)],
         "suggested_budgets": {}})
    assert res["ok"] is False
    assert db.list_transactions(api.conn) == []                 # nothing written
    assert db.get_setting(api.conn, "opening_balance_agorot") == "500000"

def test_onboarding_rejects_fractional_budget_without_writing(api):
    res = api.onboarding_complete(
        {"user_name": "X", "salary_day": "10",
         "salary_amount_agorot": "900000", "card_charge_day": "2"},
        {"opening_balance_ils": 1000, "transactions": [],
         "suggested_budgets": {"Groceries": 1200.5}})
    assert res["ok"] is False
    groceries = db.category_id_by_name(api.conn, "Groceries")
    assert groceries not in db.get_budgets(api.conn)             # nothing written

def test_onboarding_rejects_zero_budget_without_writing(api):
    # was silently dropped (ok=True) — now an explicit, all-or-nothing failure
    res = api.onboarding_complete(
        {"user_name": "X", "salary_day": "10",
         "salary_amount_agorot": "900000", "card_charge_day": "2"},
        {"opening_balance_ils": 1000, "transactions": [],
         "suggested_budgets": {"Groceries": 0}})
    assert res["ok"] is False
    assert db.get_setting(api.conn, "opening_balance_agorot") == "500000"

def test_onboarding_rejects_fractional_opening_balance_without_writing(api):
    res = api.onboarding_complete(
        {"user_name": "X", "salary_day": "10",
         "salary_amount_agorot": "900000", "card_charge_day": "2"},
        {"opening_balance_ils": 999.6, "transactions": [],
         "suggested_budgets": {}})
    assert res["ok"] is False
    assert db.get_setting(api.conn, "opening_balance_agorot") == "500000"

def test_onboarding_allows_zero_opening_balance(api):
    # "I'm broke right now" is valid: 0 opening balance must onboard cleanly
    res = api.onboarding_complete(
        {"user_name": "Broke", "salary_day": "10",
         "salary_amount_agorot": "900000", "card_charge_day": "2"},
        {"opening_balance_ils": 0, "transactions": [_txn(800)],
         "suggested_budgets": {"Groceries": 1200}})
    assert res["ok"] is True
    assert db.get_setting(api.conn, "opening_balance_agorot") == "0"
    assert len(db.list_transactions(api.conn)) == 1

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


def test_save_goal_missing_target_is_user_facing(api):
    # A form post without target_ils used to raise a raw KeyError tagged
    # "internal" (a dead-end generic error on web); it must be clean + "user".
    res = api.save_goal({"name": "Trip", "goal_type": "purchase_fund"})
    assert res["ok"] is False and res["error_kind"] == "user"
    assert "target_ils" not in res["error"] and "not a money amount" not in res["error"]

def test_save_goal_bad_date_no_raw_leak(api):
    res = api.save_goal({"name": "Trip", "goal_type": "save_by_date",
                         "target_ils": 2000, "target_date": "next friday"})
    assert res["ok"] is False
    assert "isoformat" not in res["error"]

def test_set_category_budget_non_numeric_no_raw_leak(api):
    food = db.category_id_by_name(api.conn, "Food out")
    res = api.set_category_budget(food, "lots")
    assert res["ok"] is False
    assert "not a money amount" not in res["error"]

def test_export_csv_rejects_malformed_month(api):
    res = api.export_csv("bogus")
    assert res["ok"] is False
    assert "invalid literal" not in res["error"]  # no raw int() ValueError

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
