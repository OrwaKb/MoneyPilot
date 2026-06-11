from __future__ import annotations

import datetime as dt

from app import db
from app.engine import cycles


def _salary_day(conn) -> int:
    return int(db.get_setting(conn, "salary_day", "1"))


def _cycle(conn, today: dt.date) -> dict:
    return cycles.salary_cycle(today, _salary_day(conn))


def _expense_sum(conn, *, start, end, category_ids=None, payment_method=None) -> int:
    """Positive agorot total of (non-deleted) expenses in [start, end]."""
    q = ("SELECT COALESCE(SUM(-amount_agorot),0) AS s FROM transactions"
         " WHERE deleted_at IS NULL AND direction='expense'"
         " AND effective_date >= ? AND effective_date <= ?")
    args = [start.isoformat(), end.isoformat()]
    if category_ids is not None:
        q += f" AND category_id IN ({','.join('?'*len(category_ids))})"
        args += list(category_ids)
    if payment_method:
        q += " AND payment_method = ?"
        args.append(payment_method)
    return conn.execute(q, args).fetchone()["s"]


def _discretionary_ids(conn) -> list[int]:
    return [c["id"] for c in db.categories(conn)
            if not c["is_income"] and not c["is_fixed"]]


def safe_to_spend(conn, today: dt.date) -> dict:
    cyc = _cycle(conn, today)
    budgets = db.get_budgets(conn)
    disc = _discretionary_ids(conn)
    pool = sum(budgets.get(cid, 0) for cid in disc)
    spent = _expense_sum(conn, start=cyc["start"], end=cyc["end"],
                         category_ids=disc)
    remaining = pool - spent
    return {
        "pool_agorot": pool,
        "spent_agorot": spent,
        "remaining_agorot": remaining,
        "days_left": cyc["days_left"],
        "today_agorot": remaining // cyc["days_left"] if remaining > 0 else 0,
        "cycle": cyc,
    }


def category_status(conn, today: dt.date) -> list[dict]:
    cyc = _cycle(conn, today)
    budgets = db.get_budgets(conn)
    progress = cyc["day_index"] / cyc["length"]
    out = []
    for c in db.categories(conn):
        if c["is_income"]:
            continue
        spent = _expense_sum(conn, start=cyc["start"], end=cyc["end"],
                             category_ids=[c["id"]])
        bud = budgets.get(c["id"], 0)
        if bud == 0 and spent == 0:
            continue
        ratio = round((spent / bud) / progress, 2) if bud > 0 else None
        out.append({"category_id": c["id"], "name": c["name"],
                    "emoji": c["emoji"], "is_fixed": bool(c["is_fixed"]),
                    "spent_agorot": spent, "budget_agorot": bud,
                    "pace_ratio": ratio})
    return out


def card_accrual(conn, today: dt.date) -> dict:
    w = cycles.card_window(today, int(db.get_setting(conn, "card_charge_day", "1")))
    total = _expense_sum(conn, start=w["start"],
                         end=w["charge_date"] - dt.timedelta(days=1),
                         payment_method="card")
    return {"total_agorot": total, "charge_date": w["charge_date"],
            "days_to_charge": w["days_to_charge"]}


def cycle_net(conn, start: dt.date, end: dt.date) -> tuple[int, int]:
    """(income, expenses) as positive agorot for the window."""
    income = conn.execute(
        "SELECT COALESCE(SUM(amount_agorot),0) AS s FROM transactions"
        " WHERE deleted_at IS NULL AND direction='income'"
        " AND effective_date >= ? AND effective_date <= ?",
        (start.isoformat(), end.isoformat())).fetchone()["s"]
    expenses = _expense_sum(conn, start=start, end=end)
    return income, expenses
