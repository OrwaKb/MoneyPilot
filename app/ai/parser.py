from __future__ import annotations

import datetime as dt
import re

from app.models import ParsedTxn

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

_AMOUNT_RE = re.compile(r"(\d+(?:[.,]\d{1,2})?)")


def _line_to_txn(line: str, today: dt.date) -> ParsedTxn | None:
    m = _AMOUNT_RE.search(line)
    if not m:
        return None
    amount = float(m.group(1).replace(",", "."))
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
                     description=line.strip(), confidence=0.3)


def fallback_parse(text: str, today: dt.date) -> list[ParsedTxn]:
    """Regex-only parse used when Claude is unreachable. Low confidence by design;
    callers must store results with needs_review=1."""
    out = []
    for line in filter(None, (ln.strip() for ln in text.splitlines())):
        txn = _line_to_txn(line, today)
        if txn:
            out.append(txn)
    if not out:
        raise ValueError("no amount found in text")
    return out
