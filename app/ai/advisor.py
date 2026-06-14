from __future__ import annotations

import datetime as dt
import json
import logging

from pydantic import ValidationError

from app import db
from app.ai import client, prompts
from app.engine import insights
from app.models import ParsedTxn, fmt_ils, parse_iso_date, to_agorot

log = logging.getLogger("moneypilot.advisor")

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
    except client.AIUnavailable as e:
        # Genuine AI outage → deterministic template. NOT cached, so a later
        # refresh can upgrade to AI. Other (unexpected) exceptions propagate.
        log.warning("briefing falling back to template: %s", e)
        return {"text": template_briefing(fp), "source": "template"}


def _chat_title(text: str) -> str:
    """First line of `text`, whitespace-collapsed, max 48 chars (… if cut)."""
    first = (text or "").strip().splitlines()[0] if (text or "").strip() else ""
    title = " ".join(first.split())
    if len(title) > 48:
        title = title[:48] + "…"
    return title


def chat(conn, text: str, today: dt.date, conversation_id=None) -> dict:
    title = None
    if conversation_id is None:
        title = _chat_title(text)
        conversation_id = db.add_conversation(conn, title)
    db.add_chat(conn, "user", text, conversation_id=conversation_id)
    fp = insights.fact_pack(conn, today)
    history = "\n".join(
        f"{c['role'].upper()}: {c['text']}"
        for c in db.recent_chat(conn, 20, conversation_id)[:-1]) or "(none)"
    try:
        reply = client.ask_claude(
            prompts.CHAT_USER_TMPL.format(facts=json.dumps(fp),
                                          history=history, question=text),
            system=prompts.CHAT_SYSTEM, timeout_s=60)
    except client.AIUnavailable as e:
        # Genuine AI outage → graceful offline reply. Other (unexpected)
        # exceptions are bugs and must surface, not hide behind "offline".
        log.warning("chat offline: %s", e)
        return {"text": "Advisor offline — your data is safe and the numbers on"
                        " the Overview are still live. Try again later.",
                "action": None, "offline": True,
                "conversation_id": conversation_id, "title": title}
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
    db.add_chat(conn, "assistant", reply, conversation_id=conversation_id)
    return {"text": reply, "action": action, "offline": False,
            "conversation_id": conversation_id, "title": title}


# The chat model is told the add_transaction.txn schema, but — unlike the parser,
# which gets the full schema AND a repair loop — it occasionally omits
# effective_date (a live "log this" means today) or, primed by the sibling
# create_goal/update_budget actions, names the amount `amount_ils`. Those stray
# replies used to dump a raw pydantic "N validation errors" string straight into
# the chat. Fill the gaps, then turn any leftover schema error into a clean line.
_AMOUNT_ALIASES = ("amount_ils", "amount_nis", "amount_shekels")
_DATE_ALIASES = ("date", "txn_date", "when")


def _friendly_txn_error(e: ValidationError) -> str:
    fields = {str(err["loc"][0]) for err in e.errors() if err.get("loc")}
    if "amount" in fields:
        return ('I couldn\'t log that — I didn\'t catch a valid amount. Tell me'
                ' how much, e.g. "log ₪13 for water".')
    if fields:
        return ("I couldn't log that — " + ", ".join(sorted(fields))
                + " didn't look right. Mind rephrasing the transaction?")
    return "I couldn't log that transaction — the details weren't valid."


def _coerce_chat_txn(txn, today: dt.date) -> ParsedTxn:
    if not isinstance(txn, dict):
        raise ValueError('I couldn\'t log that — the transaction details were'
                         ' missing. Tell me the amount, e.g. "log ₪13 for water".')
    data = dict(txn)
    # Aliasing mirrors the amount fix: a date the model put under a stray key
    # ("date"/"when") must land on the named day, not be silently overwritten
    # with today. Only when NO date-like key is present do we default to today
    # (a live "log this" means now). A present-but-junk date stays loud (errors).
    if not data.get("effective_date"):
        for alias in _DATE_ALIASES:
            if data.get(alias):
                data["effective_date"] = data.pop(alias)
                break
        else:
            data["effective_date"] = today.isoformat()
    if data.get("amount") in (None, ""):
        for alias in _AMOUNT_ALIASES:
            if data.get(alias) not in (None, ""):
                data["amount"] = data.pop(alias)
                break
    try:
        return ParsedTxn(**data)
    except ValidationError as e:
        raise ValueError(_friendly_txn_error(e)) from e


def apply_action(conn, action: dict, today: dt.date) -> dict:
    """Apply a user-confirmed advisor action. Raises ValueError on bad input."""
    kind = action.get("type")
    if kind == "create_goal":
        # The action is AI-shaped: read every field defensively so an omitted or
        # malformed key degrades to a clean, user-facing line — not a raw
        # KeyError / "not a money amount: None" / "Invalid isoformat string".
        name = str(action.get("name") or "").strip()
        if not name:
            raise ValueError("I need a name for that goal.")
        try:
            target = to_agorot(action.get("target_ils"))
        except ValueError:
            raise ValueError("I couldn't read the goal's target — tell me how"
                             " much to save, e.g. ₪2000.")
        if target <= 0:
            raise ValueError("goal target must be positive")
        target_date = (parse_iso_date(action["target_date"],
                                      label="the goal's target date")
                       if action.get("target_date") else None)
        gid = db.add_goal(conn, name=name,
                          type=("save_by_date"
                                if action.get("goal_type") == "save_by_date"
                                else "purchase_fund"),
                          target_agorot=target, target_date=target_date)
        return {"summary": f"Goal '{name}' created", "goal_id": gid}
    if kind == "update_budget":
        cat_name = str(action.get("category") or "").strip()
        if not cat_name:
            raise ValueError("Which category should I set the budget for?")
        cat_id = db.category_id_by_name(conn, cat_name)
        if cat_id is None:
            raise ValueError(f"unknown category {cat_name!r}")
        try:
            amount = to_agorot(action.get("amount_ils"))
        except ValueError:
            raise ValueError("I couldn't read that budget — give me a number of"
                             " shekels.")
        if amount <= 0:
            raise ValueError("budget must be positive")
        db.set_budget(conn, cat_id, amount)
        return {"summary": f"Budget for {cat_name} set to {fmt_ils(amount)}"}
    if kind == "add_transaction":
        from app.ai import parser  # late import avoids a cycle
        p = _coerce_chat_txn(action.get("txn"), today)
        tid = parser._store(conn, p, raw_text="(advisor action)", source="ai")
        # Echo the amount + date so a scale/date slip is visible in the toast.
        shown = (fmt_ils(to_agorot(p.amount)) if p.currency == "ILS"
                 else f"{p.amount:g} {p.currency}")
        return {"summary": f"Logged {shown} on {p.effective_date.isoformat()}",
                "txn_id": tid}
    if kind == "adjust_setting":
        key = str(action.get("key") or "").strip()
        if not key:
            raise ValueError("I couldn't tell which setting to change.")
        if key not in _SETTING_WHITELIST:
            raise ValueError(f"setting {key!r} is not adjustable via chat")
        value = str(action.get("value") or "").strip()
        if key in ("salary_day", "card_charge_day"):
            if not value.isdigit() or not 1 <= int(value) <= 31:
                raise ValueError(f"{key} must be an integer 1..31")
            value = str(int(value))
        elif key == "salary_amount_agorot":
            if not value.isdigit() or int(value) <= 0 or int(value) % 100 != 0:
                raise ValueError("salary_amount_agorot must be a positive whole"
                                 " number of shekels (agorot = shekels x 100,"
                                 " divisible by 100)")
            value = str(int(value))
        db.set_setting(conn, key, value)
        return {"summary": f"{key} updated"}
    raise ValueError(f"unknown action type {kind!r}")


def onboarding_propose(conn, braindump: str, today: dt.date,
                       profile: dict | None = None) -> dict:
    """`profile` carries the wizard's not-yet-saved salary fields — settings
    are only written at confirm, so without it Claude would see SALARY: 0."""
    profile = profile or {}
    cats = ", ".join(c["name"] for c in db.categories(conn) if not c["is_income"])
    try:
        salary = int(str(profile.get("salary_amount_agorot")
                         or db.get_setting(conn, "salary_amount_agorot", "0"))) // 100
    except ValueError:
        salary = 0
    salary_day = str(profile.get("salary_day")
                     or db.get_setting(conn, "salary_day", "1"))
    user = prompts.ONBOARD_USER_TMPL.format(
        today=today.isoformat(), categories=cats, salary=salary,
        salary_day=salary_day, text=braindump)
    reply = client.ask_claude(user, system=prompts.ONBOARD_SYSTEM, timeout_s=90)
    # validate transactions now so confirm can't fail later; one repair retry
    # (mirrors the parser pipeline — real replies are often schema-adjacent);
    # clamp today-or-future dates to yesterday (opening balance reflects today)
    for attempt in range(2):
        try:
            proposal = client.extract_json(reply, opener="{")
            txns = []
            for t in proposal.get("transactions", []):
                p = ParsedTxn(**t)
                # First Flight uses whole shekels — round so the proposal the
                # user confirms can't fail validation; drop what rounds to 0.
                amt = round(p.amount)
                if amt < 1:
                    continue
                p = p.model_copy(update={"amount": float(amt)})
                if p.effective_date >= today:
                    p = p.model_copy(update={
                        "effective_date": today - dt.timedelta(days=1)})
                txns.append(p.model_dump(mode="json"))
            break
        except (ValueError, ValidationError, TypeError) as e:
            if attempt == 1:
                raise
            reply = client.ask_claude(
                prompts.REPAIR_TMPL.format(error=str(e)[:300],
                                           previous=reply[:2000]),
                system=prompts.ONBOARD_SYSTEM, timeout_s=90)
    proposal["transactions"] = txns
    budgets = {}
    for cat, ils in (proposal.get("suggested_budgets") or {}).items():
        if db.category_id_by_name(conn, str(cat)) is None:
            continue  # unknown category — drop
        try:
            agorot = to_agorot(ils)
        except ValueError:
            continue  # non-numeric — drop
        whole = round(agorot / 100)            # whole shekels, like the txns
        if whole >= 1:                         # drop sub-1, never emit a 0
            budgets[str(cat)] = whole
    proposal["suggested_budgets"] = budgets
    # opening balance is also whole shekels in First Flight (0 is allowed)
    try:
        ob = round(float(proposal.get("opening_balance_ils") or 0))
    except (TypeError, ValueError):
        ob = 0
    proposal["opening_balance_ils"] = max(ob, 0)
    return proposal
