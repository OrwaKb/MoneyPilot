import datetime as dt
import json

import pytest

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

def test_chat_requests_web_search(seeded, monkeypatch):
    # Chat opts into browsing so the advisor can answer live-data questions
    # (FX rates, prices). The FACTS-only briefing must NOT (kept fast + tool-free).
    seen = {}
    def fake_ask(user, system=None, timeout_s=0, web=False):
        seen.setdefault("web", web)
        return "ok"
    monkeypatch.setattr(advisor.client, "ask_claude", fake_ask)
    advisor.chat(seeded, "what's the dollar at today?", TODAY)
    assert seen["web"] is True


def test_briefing_stays_tool_free(seeded, monkeypatch):
    seen = {}
    def fake_ask(user, system=None, timeout_s=0, web=False):
        seen["web"] = web
        return "All systems nominal."
    monkeypatch.setattr(advisor.client, "ask_claude", fake_ask)
    advisor.get_briefing(seeded, TODAY)
    assert seen["web"] is False


def test_chat_extracts_action_block(seeded, monkeypatch):
    reply = ('I will create that goal.\n```action\n'
             '{"type": "create_goal", "name": "Trip", '
             '"goal_type": "save_by_date", "target_ils": 2000, '
             '"target_date": "2026-10-01"}\n```')
    monkeypatch.setattr(advisor.client, "ask_claude", lambda *a, **k: reply)
    r = advisor.chat(seeded, "make a trip goal", TODAY)
    assert r["action"]["type"] == "create_goal"
    assert "```" not in r["text"]

def test_chat_surfaces_unparseable_action_block(seeded, monkeypatch):
    # The model promised an action but emitted malformed JSON in the fence.
    # Old behavior: action stayed None and the fence was stripped, leaving the
    # user with prose promising a change, no card, and no clue nothing happened.
    # Now the dead-end must be surfaced as a short note (prose still preserved).
    reply = 'Done — budget updated!\n```action\n{oops not valid json}\n```'
    monkeypatch.setattr(advisor.client, "ask_claude", lambda *a, **k: reply)
    r = advisor.chat(seeded, "set my Fun budget to 100", TODAY)
    assert r["action"] is None
    assert "```" not in r["text"]                  # fence still stripped
    assert "Done — budget updated!" in r["text"]   # original prose preserved
    assert "couldn't" in r["text"].lower()         # the dead-end is now visible


def test_chat_offline(seeded, monkeypatch):
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("x")))
    r = advisor.chat(seeded, "hello?", TODAY)
    assert r["offline"] is True

def test_chat_propagates_unexpected_error(seeded, monkeypatch):
    # A non-AIUnavailable exception is a real bug, not an outage — it must
    # surface, not masquerade as "Advisor offline".
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    with pytest.raises(RuntimeError):
        advisor.chat(seeded, "hello?", TODAY)

def test_briefing_propagates_unexpected_error(seeded, monkeypatch):
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bug")))
    with pytest.raises(RuntimeError):
        advisor.get_briefing(seeded, TODAY)


def test_chat_creates_conversation_titled_from_message(seeded, monkeypatch):
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: "noted.")
    r = advisor.chat(seeded, "Can I afford a trip to Eilat?", TODAY)
    cid = r["conversation_id"]
    assert cid is not None
    assert r["title"] == "Can I afford a trip to Eilat?"
    convs = db.list_conversations(seeded)
    assert len(convs) == 1 and convs[0]["id"] == cid
    assert convs[0]["title"] == "Can I afford a trip to Eilat?"


def test_chat_title_truncates_at_48(seeded, monkeypatch):
    monkeypatch.setattr(advisor.client, "ask_claude", lambda *a, **k: "ok")
    text = "x" * 60  # 60 chars
    r = advisor.chat(seeded, text, TODAY)
    assert r["title"] == "x" * 48 + "…"
    assert len(r["title"]) == 49  # 48 + ellipsis


def test_chat_continues_same_conversation_with_memory(seeded, monkeypatch):
    seen = {}

    def fake_ask(user, system=None, timeout_s=0, web=False):
        seen["user"] = user
        return "second reply"

    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: "first reply")
    r1 = advisor.chat(seeded, "my first question", TODAY)
    cid = r1["conversation_id"]
    assert r1["title"] is not None

    monkeypatch.setattr(advisor.client, "ask_claude", fake_ask)
    r2 = advisor.chat(seeded, "my second question", TODAY, conversation_id=cid)
    # same conversation, no new title on a continuation
    assert r2["conversation_id"] == cid
    assert r2["title"] is None
    # the AI saw the first exchange in this conversation's history
    assert "my first question" in seen["user"]
    assert "first reply" in seen["user"]
    # both turns landed in the one conversation (2 user + 2 assistant)
    msgs = db.recent_chat(seeded, 50, cid)
    assert len(msgs) == 4
    convs = db.list_conversations(seeded)
    assert len(convs) == 1


def test_chat_offline_still_returns_conversation_id(seeded, monkeypatch):
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            client.AIUnavailable("x")))
    r = advisor.chat(seeded, "anything", TODAY)
    assert r["offline"] is True
    assert r["conversation_id"] is not None
    # the conversation and the user message were still stored
    convs = db.list_conversations(seeded)
    assert len(convs) == 1 and convs[0]["id"] == r["conversation_id"]
    msgs = db.recent_chat(seeded, 50, r["conversation_id"])
    assert [m["text"] for m in msgs] == ["anything"]

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


def test_onboarding_propose_rounds_transaction_to_whole_shekels(seeded, monkeypatch):
    # First Flight uses whole shekels, so a fractional AI amount must be
    # rounded in the proposal — otherwise confirming it unedited would fail.
    proposal = {"opening_balance_ils": 5000,
                "transactions": [{"effective_date": "2026-06-05", "amount": 47.9,
                                  "currency": "ILS", "direction": "expense",
                                  "category": "Groceries", "description": "x",
                                  "merchant": None, "people": None,
                                  "payment_method": "card", "goal_name": None,
                                  "confidence": 0.8}],
                "suggested_budgets": {}}
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: json.dumps(proposal))
    p = advisor.onboarding_propose(seeded, "x", TODAY)
    amt = p["transactions"][0]["amount"]
    assert amt == 48 and amt == int(amt)

def test_onboarding_propose_drops_negligible_transaction(seeded, monkeypatch):
    # an amount that rounds to 0 can't be a valid (>0) transaction — drop it
    proposal = {"opening_balance_ils": 5000,
                "transactions": [{"effective_date": "2026-06-05", "amount": 0.3,
                                  "currency": "ILS", "direction": "expense",
                                  "category": "Groceries", "description": "x",
                                  "merchant": None, "people": None,
                                  "payment_method": "card", "goal_name": None,
                                  "confidence": 0.8}],
                "suggested_budgets": {}}
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: json.dumps(proposal))
    p = advisor.onboarding_propose(seeded, "x", TODAY)
    assert p["transactions"] == []

def test_onboarding_propose_drops_sub_shekel_budget(seeded, monkeypatch):
    # a sub-1-shekel AI budget rounds to 0; drop it (like the txn path) rather
    # than emit a 0 the confirm step would then reject as invalid.
    proposal = {"opening_balance_ils": 5000, "transactions": [],
                "suggested_budgets": {"Food out": 0.5, "Groceries": 1200}}
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: json.dumps(proposal))
    p = advisor.onboarding_propose(seeded, "x", TODAY)
    assert p["suggested_budgets"] == {"Groceries": 1200}

def test_onboarding_propose_rounds_opening_balance(seeded, monkeypatch):
    proposal = {"opening_balance_ils": 4999.6, "transactions": [],
                "suggested_budgets": {}}
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: json.dumps(proposal))
    p = advisor.onboarding_propose(seeded, "x", TODAY)
    assert p["opening_balance_ils"] == 5000
    assert p["opening_balance_ils"] == int(p["opening_balance_ils"])

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
    with pytest.raises(ValueError):       # whole shekels only (not 6000.50)
        advisor.apply_action(seeded, {"type": "adjust_setting",
                                      "key": "salary_amount_agorot",
                                      "value": "600050"}, TODAY)
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

def test_apply_action_rejects_nonpositive_amounts(seeded):
    import pytest
    with pytest.raises(ValueError):
        advisor.apply_action(seeded, {"type": "create_goal", "name": "Bad",
                                      "goal_type": "purchase_fund",
                                      "target_ils": -500}, TODAY)
    with pytest.raises(ValueError):
        advisor.apply_action(seeded, {"type": "update_budget",
                                      "category": "Food out",
                                      "amount_ils": -700}, TODAY)
    assert db.list_goals(seeded) == []

def test_onboarding_sanitizes_suggested_budgets(seeded, monkeypatch):
    proposal = {"opening_balance_ils": 5000, "transactions": [],
                "suggested_budgets": {"Food out": 600, "Groceries": "lots",
                                      "Spaceships": 100, "Fun": -50}}
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: json.dumps(proposal))
    p = advisor.onboarding_propose(seeded, "stuff", TODAY)
    assert p["suggested_budgets"] == {"Food out": 600}

def test_onboarding_propose_survives_real_world_nulls(seeded, monkeypatch):
    # Shape captured from a REAL Claude onboarding reply (2026-06-12 bug):
    # people=[] and payment_method=null crashed validation -> blank slate.
    proposal = {"opening_balance_ils": 5000,
                "transactions": [{"effective_date": "2026-06-05", "amount": 800,
                                  "currency": "ILS", "direction": "expense",
                                  "category": "Groceries",
                                  "description": "groceries so far",
                                  "merchant": None, "people": [],
                                  "payment_method": None, "goal_name": None,
                                  "confidence": 0.8}],
                "suggested_budgets": {"Groceries": 1200}}
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: json.dumps(proposal))
    p = advisor.onboarding_propose(seeded, "I have 5000, spent 800", TODAY)
    assert p["opening_balance_ils"] == 5000
    assert p["transactions"][0]["payment_method"] == "card"
    assert p["transactions"][0]["people"] is None

def test_onboarding_propose_repairs_bad_reply(seeded, monkeypatch):
    # Real Claude once replied direction:"out" — one repair retry must fix it,
    # mirroring the parser pipeline's repair loop.
    bad = {"opening_balance_ils": 5000,
           "transactions": [{"effective_date": "2026-06-05", "amount": 800,
                             "currency": "ILS", "direction": "out",
                             "category": "Groceries", "description": "x",
                             "merchant": None, "people": None,
                             "payment_method": "card", "goal_name": None,
                             "confidence": 0.8}],
           "suggested_budgets": {}}
    good = json.loads(json.dumps(bad))
    good["transactions"][0]["direction"] = "expense"
    replies = iter([json.dumps(bad), json.dumps(good)])
    calls = []
    monkeypatch.setattr(advisor.client, "ask_claude",
                        lambda *a, **k: calls.append(1) or next(replies))
    p = advisor.onboarding_propose(seeded, "dump", TODAY)
    assert len(calls) == 2
    assert p["transactions"][0]["direction"] == "expense"

def test_onboarding_propose_uses_wizard_profile(seeded, monkeypatch):
    seen = {}
    def fake_ask(user, system=None, timeout_s=0):
        seen["user"] = user
        return json.dumps({"opening_balance_ils": 0, "transactions": [],
                           "suggested_budgets": {}})
    monkeypatch.setattr(advisor.client, "ask_claude", fake_ask)
    advisor.onboarding_propose(seeded, "dump", TODAY,
                               profile={"salary_amount_agorot": "1230000",
                                        "salary_day": "7"})
    assert "SALARY: 12300 ILS on day 7" in seen["user"]

def test_chat_strips_every_action_fence(seeded, monkeypatch):
    reply = ('One.\n```action\n{"type": "update_budget", "category": "Fun",'
             ' "amount_ils": 100}\n```\nTwo.\n```action\n'
             '{"type": "create_goal", "name": "X"}\n```\nEnd.')
    monkeypatch.setattr(advisor.client, "ask_claude", lambda *a, **k: reply)
    r = advisor.chat(seeded, "do things", TODAY)
    assert r["action"]["type"] == "update_budget"  # first action only
    assert "```" not in r["text"] and "create_goal" not in r["text"]


def test_apply_action_add_transaction_real_world_chat_reply(seeded):
    # Shape captured from a REAL Claude chat reply (2026-06-14 bug): primed by the
    # sibling _ils actions, the model emitted the amount as `amount_ils` and
    # omitted `effective_date`. ParsedTxn(**txn) then dumped a raw pydantic
    # "2 validation errors" string straight into the chat. The confirm must now
    # log the txn, dated today, instead of crashing.
    res = advisor.apply_action(seeded, {"type": "add_transaction",
        "txn": {"amount_ils": 13, "currency": "ILS", "direction": "expense",
                "category": "Other", "description": "water"}}, TODAY)
    assert res["txn_id"]
    row = db.list_transactions(seeded, limit=1)[0]
    assert row["amount_agorot"] == -1300                 # ₪13, expense → negative
    assert row["effective_date"] == TODAY.isoformat()    # date defaulted to today


def test_apply_action_add_transaction_defaults_missing_date(seeded):
    # amount is fine, but the model left out the date — a live "log this" is today.
    advisor.apply_action(seeded, {"type": "add_transaction",
        "txn": {"amount": 50, "category": "Food out"}}, TODAY)
    row = db.list_transactions(seeded, limit=1)[0]
    assert row["amount_agorot"] == -5000
    assert row["effective_date"] == TODAY.isoformat()


def test_apply_action_add_transaction_full_schema_still_works(seeded):
    advisor.apply_action(seeded, {"type": "add_transaction",
        "txn": {"effective_date": "2026-06-10", "amount": 80, "currency": "ILS",
                "direction": "expense", "category": "Groceries",
                "description": "milk"}}, TODAY)
    row = db.list_transactions(seeded, limit=1)[0]
    assert row["amount_agorot"] == -8000
    assert row["effective_date"] == "2026-06-10"


def test_apply_action_add_transaction_clean_error_when_unsalvageable(seeded):
    # No amount anywhere → genuinely unloggable. The user must get a clean,
    # human message, never the raw "N validation errors for ParsedTxn ..." dump.
    import pytest
    with pytest.raises(ValueError) as ei:
        advisor.apply_action(seeded, {"type": "add_transaction",
            "txn": {"category": "Other", "description": "mystery"}}, TODAY)
    msg = str(ei.value)
    assert "ParsedTxn" not in msg and "pydantic.dev" not in msg
    assert db.list_transactions(seeded, limit=5) == []  # nothing was written


def test_apply_action_add_transaction_honours_mis_keyed_date(seeded):
    # The model named the date `date` instead of `effective_date` (the same
    # wrong-key slip that caused the original amount bug). It must land on the
    # named day, not be silently overwritten with today.
    advisor.apply_action(seeded, {"type": "add_transaction",
        "txn": {"amount": 20, "category": "Other", "date": "2026-06-05"}}, TODAY)
    row = db.list_transactions(seeded, limit=1)[0]
    assert row["effective_date"] == "2026-06-05"


# --- sibling action branches: a malformed AI action must never dump a raw
#     KeyError / "not a money amount" / "Invalid isoformat string" at the user.

def test_apply_action_create_goal_missing_target_clean_error(seeded):
    import pytest
    with pytest.raises(ValueError) as ei:
        advisor.apply_action(seeded, {"type": "create_goal", "name": "Trip",
                                      "goal_type": "purchase_fund"}, TODAY)
    msg = str(ei.value)
    assert "target_ils" not in msg and "not a money amount" not in msg
    assert db.list_goals(seeded) == []

def test_apply_action_create_goal_null_target_clean_error(seeded):
    import pytest
    with pytest.raises(ValueError) as ei:
        advisor.apply_action(seeded, {"type": "create_goal", "name": "Trip",
                                      "goal_type": "purchase_fund",
                                      "target_ils": None}, TODAY)
    assert "not a money amount" not in str(ei.value)
    assert db.list_goals(seeded) == []

def test_apply_action_create_goal_bad_date_clean_error(seeded):
    import pytest
    with pytest.raises(ValueError) as ei:
        advisor.apply_action(seeded, {"type": "create_goal", "name": "Trip",
                                      "goal_type": "save_by_date",
                                      "target_ils": 2000,
                                      "target_date": "October 1st"}, TODAY)
    assert "isoformat" not in str(ei.value)
    assert db.list_goals(seeded) == []

def test_apply_action_update_budget_missing_amount_clean_error(seeded):
    import pytest
    with pytest.raises(ValueError) as ei:
        advisor.apply_action(seeded, {"type": "update_budget",
                                      "category": "Food out"}, TODAY)
    assert "amount_ils" not in str(ei.value)

def test_apply_action_adjust_setting_missing_key_clean_error(seeded):
    import pytest
    with pytest.raises(ValueError) as ei:
        advisor.apply_action(seeded, {"type": "adjust_setting",
                                      "value": "7"}, TODAY)
    assert "'key'" not in str(ei.value)  # no raw KeyError('key')


def test_briefing_can_mention_recurring(seeded, monkeypatch):
    # Prove the prompts now carry recurring guidance (so the model is allowed to
    # mention an imminent charge / the monthly burn). FACTS-only is unchanged.
    from app.ai import prompts
    assert "recurring" in prompts.BRIEFING_SYSTEM.lower()
    assert "recurring" in prompts.CHAT_SYSTEM.lower()
