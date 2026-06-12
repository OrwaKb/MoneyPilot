from __future__ import annotations

import datetime as dt
import json

from app import db
from app.ai import client, prompts
from app.engine import insights
from app.models import ParsedTxn, fmt_ils, to_agorot

_SETTING_WHITELIST = {"salary_day", "salary_amount_agorot", "card_charge_day",
                      "user_name"}


def template_briefing(fp: dict) -> str:
    """Deterministic briefing used when Claude is unreachable."""
    over = [c for c in fp["categories"]
            if not c["is_fixed"] and c["pace_ratio"] is not None
            and c["pace_ratio"] > 1.1]
    pace_line = (f" Watch {over[0]['name']} — over pace." if over
                 else " Spending pace looks fine.")
    goal_line = ""
    if fp["goals"]:
        g = fp["goals"][0]
        goal_line = f" {g['emoji']} {g['name']}: {g['pct']}%."
    return (f"Safe to spend today: {fp['safe_to_spend']['today_fmt']}."
            f"{pace_line} Card charges in {fp['card']['days_to_charge']}d"
            f" ({fp['card']['total_fmt']} so far).{goal_line}")


def get_briefing(conn, today: dt.date, force: bool = False) -> dict:
    cached = db.get_briefing(conn, today.isoformat())
    if cached and not force:
        return {"text": cached["text"], "source": "cache"}
    fp = insights.fact_pack(conn, today)
    try:
        text = client.ask_claude(
            prompts.BRIEFING_USER_TMPL.format(facts=json.dumps(fp)),
            system=prompts.BRIEFING_SYSTEM, timeout_s=45).strip()
        db.put_briefing(conn, today.isoformat(), text, json.dumps(fp))
        return {"text": text, "source": "ai"}
    except Exception:
        # template is NOT cached so a later refresh can upgrade to AI
        return {"text": template_briefing(fp), "source": "template"}


def chat(conn, text: str, today: dt.date) -> dict:
    db.add_chat(conn, "user", text)
    fp = insights.fact_pack(conn, today)
    history = "\n".join(f"{c['role'].upper()}: {c['text']}"
                        for c in db.recent_chat(conn, 20)[:-1]) or "(none)"
    try:
        reply = client.ask_claude(
            prompts.CHAT_USER_TMPL.format(facts=json.dumps(fp),
                                          history=history, question=text),
            system=prompts.CHAT_SYSTEM, timeout_s=60)
    except Exception:
        return {"text": "Advisor offline — your data is safe and the numbers on"
                        " the Overview are still live. Try again later.",
                "action": None, "offline": True}
    action = None
    while "```action" in reply:
        head, _, tail = reply.partition("```action")
        block, _, rest = tail.partition("```")
        if action is None:
            try:
                action = client.extract_json(block, opener="{")
            except ValueError:
                action = None
        reply = (head + rest).strip()
    reply = reply.strip()
    db.add_chat(conn, "assistant", reply)
    return {"text": reply, "action": action, "offline": False}


def apply_action(conn, action: dict, today: dt.date) -> dict:
    """Apply a user-confirmed advisor action. Raises ValueError on bad input."""
    kind = action.get("type")
    if kind == "create_goal":
        name = str(action["name"]).strip()
        target = to_agorot(action["target_ils"])
        if not name:
            raise ValueError("goal name must not be empty")
        if target <= 0:
            raise ValueError("goal target must be positive")
        gid = db.add_goal(conn, name=name,
                          type=("save_by_date"
                                if action.get("goal_type") == "save_by_date"
                                else "purchase_fund"),
                          target_agorot=target,
                          target_date=(dt.date.fromisoformat(action["target_date"])
                                       if action.get("target_date") else None))
        return {"summary": f"Goal '{action['name']}' created", "goal_id": gid}
    if kind == "update_budget":
        cat_id = db.category_id_by_name(conn, str(action["category"]))
        if cat_id is None:
            raise ValueError(f"unknown category {action['category']!r}")
        amount = to_agorot(action["amount_ils"])
        if amount <= 0:
            raise ValueError("budget must be positive")
        db.set_budget(conn, cat_id, amount)
        return {"summary": f"Budget for {action['category']} set to "
                           f"{fmt_ils(amount)}"}
    if kind == "add_transaction":
        from app.ai import parser  # late import avoids a cycle
        p = ParsedTxn(**action["txn"])
        tid = parser._store(conn, p, raw_text="(advisor action)", source="ai")
        return {"summary": "Transaction added", "txn_id": tid}
    if kind == "adjust_setting":
        key = str(action["key"])
        if key not in _SETTING_WHITELIST:
            raise ValueError(f"setting {key!r} is not adjustable via chat")
        value = str(action["value"]).strip()
        if key in ("salary_day", "card_charge_day"):
            if not value.isdigit() or not 1 <= int(value) <= 31:
                raise ValueError(f"{key} must be an integer 1..31")
            value = str(int(value))
        elif key == "salary_amount_agorot":
            if not value.isdigit() or int(value) <= 0:
                raise ValueError("salary_amount_agorot must be a positive"
                                 " integer (agorot = shekels x 100)")
            value = str(int(value))
        db.set_setting(conn, key, value)
        return {"summary": f"{key} updated"}
    raise ValueError(f"unknown action type {kind!r}")


def onboarding_propose(conn, braindump: str, today: dt.date) -> dict:
    cats = ", ".join(c["name"] for c in db.categories(conn) if not c["is_income"])
    salary = int(db.get_setting(conn, "salary_amount_agorot", "0")) // 100
    user = prompts.ONBOARD_USER_TMPL.format(
        today=today.isoformat(), categories=cats, salary=salary,
        salary_day=db.get_setting(conn, "salary_day", "1"), text=braindump)
    reply = client.ask_claude(user, system=prompts.ONBOARD_SYSTEM, timeout_s=90)
    proposal = client.extract_json(reply, opener="{")
    # validate transactions now so confirm can't fail later; clamp any
    # today-or-future date to yesterday (opening balance already reflects today)
    txns = []
    for t in proposal.get("transactions", []):
        p = ParsedTxn(**t)
        if p.effective_date >= today:
            p = p.model_copy(update={
                "effective_date": today - dt.timedelta(days=1)})
        txns.append(p.model_dump(mode="json"))
    proposal["transactions"] = txns
    budgets = {}
    for cat, ils in (proposal.get("suggested_budgets") or {}).items():
        if db.category_id_by_name(conn, str(cat)) is None:
            continue  # unknown category — drop
        try:
            agorot = to_agorot(ils)
        except ValueError:
            continue  # non-numeric — drop
        if agorot > 0:
            budgets[str(cat)] = round(agorot / 100)
    proposal["suggested_budgets"] = budgets
    return proposal
