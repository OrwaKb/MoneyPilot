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


def available_balance(conn) -> int:
    """Actual money on hand: opening balance + every signed transaction since
    the opening date. Goal contributions count only for ACTIVE goals (archiving
    a goal releases its set-aside money back to available)."""
    opening = int(db.get_setting(conn, "opening_balance_agorot", "0"))
    opening_date = db.get_setting(conn, "opening_balance_date")
    q = ("SELECT COALESCE(SUM(amount_agorot),0) AS s FROM transactions t"
         " WHERE t.deleted_at IS NULL"
         " AND (t.direction != 'goal_contribution'"
         " OR t.goal_id IN (SELECT id FROM goals WHERE status='active'))")
    args: list = []
    if opening_date:
        q += " AND t.effective_date >= ?"
        args.append(opening_date)
    return opening + conn.execute(q, args).fetchone()["s"]


def safe_to_spend(conn, today: dt.date) -> dict:
    """How much is safe to spend per day until the next salary.

    It's your ACTUAL money minus what you still need to set aside for deadline
    goals, spread over the days left in the cycle. Category budgets are NOT the
    pool — they're per-category trackers (see ``category_status``); you can
    overspend a category, it just shows there. Goals DO reduce it: a goal you're
    saving toward lowers what's spendable today."""
    cyc = _cycle(conn, today)
    available = available_balance(conn)
    # Money you still owe your deadline goals isn't safe to spend.
    from app.engine import goals          # late import: goals imports budget
    goal_reserve = goals.cycle_savings_reserve(conn, today)
    remaining = available - goal_reserve
    return {
        "available_agorot": available,
        "goal_reserve_agorot": goal_reserve,
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


def daily_expenses(conn, start: dt.date, end: dt.date) -> list[int]:
    """Positive agorot expense total per day, start..end inclusive, zero-filled."""
    rows = conn.execute(
        "SELECT effective_date AS d, COALESCE(SUM(-amount_agorot),0) AS s"
        " FROM transactions WHERE deleted_at IS NULL AND direction='expense'"
        " AND effective_date >= ? AND effective_date <= ?"
        " GROUP BY effective_date",
        (start.isoformat(), end.isoformat())).fetchall()
    by_day = {r["d"]: r["s"] for r in rows}
    return [by_day.get((start + dt.timedelta(days=i)).isoformat(), 0)
            for i in range((end - start).days + 1)]


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
