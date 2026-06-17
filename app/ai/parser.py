from __future__ import annotations

import datetime as dt
import re

from pydantic import ValidationError

from app import db
from app.ai import client, prompts
from app.engine import fx
from app.models import ParsedTxn, to_agorot

# --- offline fallback parser --------------------------------------------------

KEYWORDS = {
    "Food out": ["falafel", "shawarma", "pizza", "burger", "restaurant", "cafe",
                 "coffee", "lunch", "dinner", "hummus", "sushi", "mcdonald"],
    "Groceries": ["groceries", "supermarket", "shufersal", "rami levy", "victory",
                  "market"],
    "Transport": ["fuel", "gas", "tank", "bus", "train", "taxi", "gett",
                  "parking", "diesel"],
    "Bills": ["rent", "electricity", "water bill", "arnona", "internet",
              "phone bill", "cellcom", "partner", "bezeq"],
    "Fun": ["movie", "cinema", "game", "beer", "bar", "party", "netflix",
            "spotify"],
    "Health": ["pharmacy", "doctor", "medicine", "dentist", "superpharm"],
    "Education": ["course", "book", "tuition", "university"],
}
CURRENCY_HINTS = {"$": "USD", "dollar": "USD", "usd": "USD",
                  "€": "EUR", "euro": "EUR", "eur": "EUR",
                  "£": "GBP", "pound": "GBP", "gbp": "GBP"}
INCOME_HINTS = ["salary", "paycheck", "income", "got paid", "received", "refund"]

_AMOUNT_RE = re.compile(r"(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+(?:[.,]\d{1,2})?)")


def _line_to_txn(line: str, today: dt.date,
                 default_method: str = "card") -> ParsedTxn | None:
    m = _AMOUNT_RE.search(line)
    if not m:
        return None
    raw = m.group(1)
    if "," in raw and "." in raw:
        raw = raw.replace(",", "")     # 1,234.56 — comma as thousands sep
    elif re.fullmatch(r"\d{1,3}(?:,\d{3})+", raw):
        raw = raw.replace(",", "")     # 12,345 — thousands only
    else:
        raw = raw.replace(",", ".")    # 12,50 — decimal comma
    amount = float(raw)
    low = line.lower()
    currency = "ILS"
    for hint, cur in CURRENCY_HINTS.items():
        if hint in low:
            currency = cur
            break
    when = today
    if "yesterday" in low:
        when = today - dt.timedelta(days=1)
    direction = "income" if any(h in low for h in INCOME_HINTS) else "expense"
    category = "Other" if direction == "expense" else "Other income"
    if direction == "expense":
        for cat, words in KEYWORDS.items():
            if any(w in low for w in words):
                category = cat
                break
    return ParsedTxn(effective_date=when, amount=amount, currency=currency,
                     direction=direction, category=category,
                     payment_method=default_method,
                     description=line.strip(), confidence=0.3)


def fallback_parse(text: str, today: dt.date,
                   default_method: str = "card") -> list[ParsedTxn]:
    """Regex-only parse used when Claude is unreachable. Low confidence by design;
    callers must store results with needs_review=1. Raises ValueError if ANY
    non-empty line has no amount — silently dropping a line would lose an entry."""
    out = []
    for line in filter(None, (ln.strip() for ln in text.splitlines())):
        txn = _line_to_txn(line, today, default_method)
        if txn is None:
            raise ValueError(f"no amount found in: {line!r}")
        out.append(txn)
    if not out:
        raise ValueError("no amount found in text")
    return out


# --- AI parse pipeline ----------------------------------------------------------

REVIEW_CONFIDENCE = 0.7
_FAST_RE = re.compile(r"^\s*(\d+(?:[.,]\d{1,2})?)\s+([^\d].*)$")
_PAY_METHODS = ("card", "cash", "transfer")


def _default_method(conn) -> str:
    """The user's configured default payment method (Settings), 'card' if unset
    or somehow invalid. The AI is told this default; the offline fast/fallback
    paths apply it directly."""
    m = db.get_setting(conn, "default_payment_method", "card")
    return m if m in _PAY_METHODS else "card"


def _fast_path(conn, text: str, today: dt.date) -> ParsedTxn | None:
    """'<amount> <words>' where a learned rule matches the words → no AI call."""
    if "\n" in text.strip():
        return None
    m = _FAST_RE.match(text)
    if not m:
        return None
    if re.search(r"\b(yesterday|monday|tuesday|wednesday|thursday|friday"
                 r"|saturday|sunday)\b", text, re.IGNORECASE):
        return None  # relative dates need the AI path
    cat_id = db.match_rule(conn, m.group(2))
    if cat_id is None:
        return None
    cat_row = conn.execute("SELECT name, is_income FROM categories WHERE id=?",
                           (cat_id,)).fetchone()
    if cat_row["is_income"]:
        return None  # income lines always go through the AI/fallback path
    cat = cat_row["name"]
    amount = float(m.group(1).replace(",", "."))
    if amount <= 0:
        return None
    return ParsedTxn(effective_date=today,
                     amount=amount, payment_method=_default_method(conn),
                     category=cat, description=m.group(2).strip(),
                     confidence=1.0)


def _build_prompt(conn, text: str, today: dt.date) -> tuple[str, str]:
    cats = ", ".join(c["name"] for c in db.categories(conn))
    rules = "; ".join(f"{r['pattern']} -> {r['category_name']}"
                      for r in db.list_rules(conn)) or "none"
    goal_names = ", ".join(g["name"] for g in db.list_goals(conn)) or "none"
    salary = int(db.get_setting(conn, "salary_amount_agorot", "0")) // 100
    user = prompts.PARSE_USER_TMPL.format(
        today=today.isoformat(), weekday=today.strftime("%A"),
        categories=cats, rules=rules, goals=goal_names, salary=salary,
        default_method=_default_method(conn), text=text)
    return prompts.PARSE_SYSTEM, user


def _ai_parse(conn, text: str, today: dt.date) -> list[ParsedTxn]:
    system, user = _build_prompt(conn, text, today)
    reply = client.ask_claude(user, system=system, timeout_s=60)
    for attempt in range(2):
        try:
            items = client.extract_json(reply)
            if not items:
                raise ValueError("AI returned no transactions")
            return [ParsedTxn(**item) for item in items]
        except (ValueError, ValidationError, TypeError) as e:
            if attempt == 1:
                raise
            reply = client.ask_claude(
                prompts.REPAIR_TMPL.format(error=str(e)[:300], previous=reply[:2000]),
                system=system, timeout_s=60)
    raise client.AIUnavailable("unreachable")  # pragma: no cover


def _store(conn, p: ParsedTxn, *, raw_text: str, source: str,
           client_uuid: str | None = None) -> int:
    try:
        rates = fx.get_rates(conn, p.effective_date) if p.currency != "ILS" else {}
        agorot, rate = fx.to_ils(p.amount, p.currency, rates)
    except ValueError:
        # No rate for this currency (offline + exotic): store the raw amount
        # as ILS-magnitude and flag for review — entries are never lost.
        agorot, rate = to_agorot(p.amount), None
        p = p.model_copy(update={
            "description": f"{p.description} [unconverted {p.amount} {p.currency}]",
            "confidence": 0.0})
    if agorot == 0:
        # sub-agora amount (e.g. 0.004) — store the minimum unit and flag
        agorot = 1
        p = p.model_copy(update={"confidence": 0.0})
    needs_review = 1 if (source == "fallback"
                         or p.confidence < REVIEW_CONFIDENCE) else 0
    cat_id = db.category_id_by_name(conn, p.category)
    if cat_id is None:
        fallback_cat = "Other income" if p.direction == "income" else "Other"
        cat_id = db.category_id_by_name(conn, fallback_cat)
        needs_review = 1
    goal_id = None
    if p.direction == "goal_contribution":
        g = db.get_goal_by_name(conn, p.goal_name or p.description)
        if g is None:
            needs_review = 1
        else:
            goal_id = g["id"]
    signed = agorot if p.direction == "income" else -agorot
    return db.add_transaction(
        conn, effective_date=p.effective_date, amount_agorot=signed,
        direction=p.direction, currency_orig=p.currency,
        amount_orig=(p.amount if p.currency != "ILS" else None),
        fx_rate=rate, category_id=cat_id, description=p.description,
        merchant=p.merchant, people=p.people, payment_method=p.payment_method,
        goal_id=goal_id, raw_text=raw_text, source=source,
        ai_confidence=p.confidence, needs_review=needs_review,
        client_uuid=client_uuid)


def parse_and_store(conn, text: str, today: dt.date) -> dict:
    """Entry point used by the UI. Never raises for AI problems; the only
    user-visible error is 'I could not find an amount in that'."""
    fast = _fast_path(conn, text, today)
    if fast is not None:
        ids = [_store(conn, fast, raw_text=text, source="rule")]
        return {"entries": ids, "used_ai": False, "source": "rule"}
    try:
        parsed = _ai_parse(conn, text, today)
        source, used_ai = "ai", True
    except Exception:
        # raises ValueError if no amount; applies the configured default method
        parsed = fallback_parse(text, today, _default_method(conn))
        source, used_ai = "fallback", False
    ids = [_store(conn, p, raw_text=text, source=source) for p in parsed]
    return {"entries": ids, "used_ai": used_ai, "source": source}


def resweep(conn, today: dt.date) -> int:
    """Re-parse fallback-sourced rows once AI is reachable again. Only rows whose
    raw_text yields exactly one transaction are upgraded in place."""
    rows = db.list_transactions(conn, needs_review=True)
    upgraded = 0
    for row in rows:
        if row["source"] != "fallback" or not row["raw_text"]:
            continue
        try:
            entry_day = dt.date.fromisoformat(row["created_at"][:10])
            parsed = _ai_parse(conn, row["raw_text"], entry_day)
        except Exception:
            break  # still offline — stop trying
        if len(parsed) != 1:
            continue  # ambiguous → leave for manual review
        p = parsed[0]
        try:
            rates = fx.get_rates(conn, p.effective_date) if p.currency != "ILS" else {}
            agorot, rate = fx.to_ils(p.amount, p.currency, rates)
        except ValueError:
            continue  # unconvertible currency → leave in the review queue
        if agorot == 0:
            continue  # sub-agora — leave in the review queue
        needs_review = 1 if p.confidence < REVIEW_CONFIDENCE else 0
        goal_id = row["goal_id"]
        if p.direction == "goal_contribution":
            g = db.get_goal_by_name(conn, p.goal_name or p.description)
            if g is None:
                goal_id = None
                needs_review = 1  # unresolved goal: keep it in the queue
            else:
                goal_id = g["id"]
        cat_id = db.category_id_by_name(conn, p.category)
        if cat_id is None:
            cat_id = row["category_id"]
            needs_review = 1  # unknown category: keep it in the queue
        db.update_transaction(
            conn, row["id"], effective_date=p.effective_date,
            amount_agorot=(agorot if p.direction == "income" else -agorot),
            direction=p.direction, category_id=cat_id, goal_id=goal_id,
            description=p.description,
            merchant=p.merchant, people=p.people,
            payment_method=p.payment_method, fx_rate=rate,
            currency_orig=p.currency,
            amount_orig=(p.amount if p.currency != "ILS" else None),
            ai_confidence=p.confidence, source="ai",
            needs_review=needs_review)
        upgraded += 1
    return upgraded
