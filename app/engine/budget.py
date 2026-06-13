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


def available_balance(conn, as_of=None) -> int:
    """Actual money on hand: opening balance + every signed transaction since
    the opening date. Goal contributions count only for ACTIVE goals (archiving
    a goal releases its set-aside money back to available).

    ``as_of`` caps the window at a date (inclusive): money you have *as of* that
    day, so a transaction dated in the future doesn't count yet. safe_to_spend
    passes today here so a future-dated entry can't move the daily allowance;
    the balance panel calls it uncapped for the all-time net."""
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
    if as_of is not None:
        q += " AND t.effective_date <= ?"
        args.append(as_of.isoformat() if hasattr(as_of, "isoformat") else as_of)
    return opening + conn.execute(q, args).fetchone()["s"]


def safe_to_spend(conn, today: dt.date) -> dict:
    """Rolling daily allowance (envelope), NOT a divide-by-days-left figure.

    A fixed amount accrues every day of the cycle; unspent days bank forward and
    an overspent day shows a NEGATIVE rolling balance you dig out of the next
    day. ``today_agorot`` is that rolling balance::

        pool   = (balance + salary you'll still receive − goal savings) + discretionary spent
        daily  = max(0, pool) // cycle_length          # the ₪/day allowance
        today  = daily × cycle_day_index − discretionary_spent_this_cycle

    The allowance is STABLE against today's spending by construction (a
    discretionary spend raises ``discretionary spent`` and lowers ``available``
    by the same amount, so ``pool`` — and ``daily`` — don't move); a
    discretionary spend therefore lowers ``today`` by exactly its amount.

    Fixed bills (``is_fixed`` categories) are "pocket money": they're excluded
    from the discretionary-spend tally (so they never crash a single day) but
    stay subtracted inside ``available`` — so a bill quietly lowers the daily
    allowance for the rest of the cycle instead.

    When the whole cycle is overdrawn (``pool`` < 0, e.g. a bill you can't
    cover), the allowance floors at 0 but ``today`` still reports the true
    shortfall (``+ min(0, pool)``) so the hero reads honestly negative instead
    of a misleading ₪0. ``available`` is taken AS OF today, so a future-dated
    entry never moves the allowance.

    Salary anticipation and the opening-date guard are preserved: the salary you
    haven't logged yet is anticipated (so the allowance isn't ~0 mid-cycle) and
    is stable once logged; and both the salary window and the spend window start
    at the opening-balance date when that falls after the cycle start, because
    income and spend before the opening snapshot are already baked into the
    balance (counting them again would double-count). Category BUDGETS are never
    the pool — they're per-category trackers (see ``category_status``)."""
    cyc = _cycle(conn, today)
    available = available_balance(conn, as_of=today)
    salary = int(db.get_setting(conn, "salary_amount_agorot", "0"))
    # Window for both salary anticipation and discretionary spend: cycle start,
    # or the opening date when that's later (pre-opening income/spend is already
    # in the opening snapshot, not in available_balance — must not re-count it).
    spend_start = cyc["start"]
    opening_date = db.get_setting(conn, "opening_balance_date")
    if opening_date:
        od = dt.date.fromisoformat(opening_date)
        if od > spend_start:
            spend_start = od
    income_so_far, _ = cycle_net(conn, spend_start, today)
    expected_salary = max(0, salary - income_so_far)
    from app.engine import goals          # late import: goals imports budget
    goal_reserve = goals.cycle_savings_reserve(conn, today)
    disc_spent = _expense_sum(conn, start=spend_start, end=today,
                              category_ids=_discretionary_ids(conn))
    remaining = available + expected_salary - goal_reserve   # money left this cycle
    pool = remaining + disc_spent                            # cycle spending pool
    total_days = (cyc["end"] - spend_start).days + 1         # = cycle length, normally
    day_index = (today - spend_start).days + 1               # 1-based, from spend_start
    daily = max(0, pool) // total_days if total_days > 0 else 0
    # + min(0, pool): when the cycle is overdrawn the floored allowance hides the
    # shortfall, so add the negative pool back in to keep today <= remaining and
    # show the hero honestly negative (not a misleading 0).
    today_agorot = daily * day_index - disc_spent + min(0, pool)
    return {
        "available_agorot": available,
        "expected_salary_agorot": expected_salary,
        "goal_reserve_agorot": goal_reserve,
        "remaining_agorot": remaining,
        "daily_allowance_agorot": daily,
        "cycle_spent_agorot": disc_spent,
        "days_left": cyc["days_left"],
        "today_agorot": today_agorot,                        # rolling; may be < 0
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
