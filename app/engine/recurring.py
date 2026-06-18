"""Stateless recurring-charge ("subscription") detector.

Pure read over `transactions` (mirrors budget/goals): group expenses by
merchant/description, find a regular monthly/annual cadence with stable
amounts, and report next-expected date + monthly-equivalent cost. The only
persisted state is a user dismiss-list in the settings KV (no schema change).
"""
from __future__ import annotations

import datetime as dt
import json
import statistics

from app import db

_DISMISS_KEY = "recurring_dismissed"
_CONFIDENCE_FLOOR = 0.6
_AMOUNT_TOL = 0.15            # +-15% of the median counts as "stable"
_PRICE_HIKE_RATIO = 1.25
_CADENCE = {                 # name: (min_gap, max_gap, regularity_tol) in days
    "monthly": (26, 35, 7),
    "annual": (350, 380, 45),
}


def _norm(text) -> str:
    s = (text or "").strip().lower()
    s = s.split(" with ")[0]          # drop a trailing "with <people>" clause
    return " ".join(s.split())


def _dismissed(conn) -> set[str]:
    try:
        items = json.loads(db.get_setting(conn, _DISMISS_KEY, "[]"))
        return {str(k) for k in items} if isinstance(items, list) else set()
    except (ValueError, TypeError):
        return set()


def dismiss(conn, key: str) -> None:
    k = _norm(key)
    if not k:
        raise ValueError("nothing to dismiss")
    db.set_setting(conn, _DISMISS_KEY, json.dumps(sorted(_dismissed(conn) | {k})))


def undismiss(conn, key: str) -> None:
    db.set_setting(conn, _DISMISS_KEY,
                   json.dumps(sorted(_dismissed(conn) - {_norm(key)})))


def _classify(median_gap: float):
    for name, (lo, hi, _tol) in _CADENCE.items():
        if lo <= median_gap <= hi:
            return name
    return None


def _confidence(occ, gaps, median_gap, amounts, typical) -> float:
    occ_score = min(1.0, occ / 6)
    reg = (1 - max(abs(g - median_gap) for g in gaps) / median_gap
           if median_gap and gaps else 0.0)
    amt = (1 - max(abs(a - typical) for a in amounts) / typical
           if typical else 0.0)
    return 0.4 * occ_score + 0.35 * max(0.0, reg) + 0.25 * max(0.0, amt)


def detect(conn, today: dt.date, *, lookback_days: int = 760,
           min_occurrences: int = 3) -> list[dict]:
    # lookback ~25 months: long enough for 3 annual charges (and plenty of
    # monthly history); the staleness guard below drops series that stopped.
    since = (today - dt.timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        "SELECT effective_date, amount_agorot, merchant, description"
        " FROM transactions WHERE deleted_at IS NULL AND direction='expense'"
        " AND effective_date >= ? AND effective_date <= ?"
        " ORDER BY effective_date",
        (since, today.isoformat())).fetchall()

    groups: dict[str, list] = {}
    for r in rows:
        key = _norm(r["merchant"] or r["description"])
        if key:
            groups.setdefault(key, []).append(r)

    dismissed = _dismissed(conn)
    out = []
    for key, occ in groups.items():
        if key in dismissed or len(occ) < min_occurrences:
            continue
        dates = [dt.date.fromisoformat(r["effective_date"]) for r in occ]
        amounts = [-r["amount_agorot"] for r in occ]      # expenses are negative
        gaps = [(dates[i] - dates[i - 1]).days for i in range(1, len(dates))]
        median_gap = statistics.median(gaps)
        cadence = _classify(median_gap)
        if cadence is None:
            continue
        if max(abs(g - median_gap) for g in gaps) > _CADENCE[cadence][2]:
            continue                                       # irregular spacing
        if (today - dates[-1]).days > median_gap + _CADENCE[cadence][2]:
            continue   # last charge older than a full cycle + grace -> cancelled
        typical = int(statistics.median(amounts))
        if typical <= 0:
            continue
        stable = [a for a in amounts if abs(a - typical) <= _AMOUNT_TOL * typical]
        # tolerate at most ONE outlier, and only if it's the most-recent charge
        if len(stable) < len(amounts) - 1:
            continue
        if len(stable) == len(amounts) - 1 and \
                abs(amounts[-1] - typical) <= _AMOUNT_TOL * typical:
            continue                                       # the outlier isn't the latest
        confidence = _confidence(len(occ), gaps, median_gap, amounts, typical)
        if confidence < _CONFIDENCE_FLOOR:
            continue
        next_expected = dates[-1] + dt.timedelta(days=round(median_gap))
        monthly_equiv = typical if cadence == "monthly" else round(typical / 12)
        name = str(occ[-1]["merchant"] or occ[-1]["description"] or key).strip().title()
        out.append({
            "key": key, "name": name, "cadence": cadence,
            "typical_agorot": typical,
            "last_charged": dates[-1].isoformat(),
            "next_expected": next_expected.isoformat(),
            "monthly_equiv_agorot": monthly_equiv,
            "occurrences": len(occ),
            "confidence": round(confidence, 2),
            "price_hike": amounts[-1] > typical * _PRICE_HIKE_RATIO,
        })
    out.sort(key=lambda d: d["confidence"], reverse=True)
    return out
