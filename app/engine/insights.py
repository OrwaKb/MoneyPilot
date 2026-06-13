from __future__ import annotations

import datetime as dt

from app import db
from app.engine import budget, cycles, goals
from app.models import fmt_ils


def _iso_dict(d: dict) -> dict:
    return {k: (v.isoformat() if isinstance(v, dt.date) else v)
            for k, v in d.items()}


def fact_pack(conn, today: dt.date) -> dict:
    salary_day = int(db.get_setting(conn, "salary_day", "1"))
    cyc = cycles.salary_cycle(today, salary_day)
    sts = budget.safe_to_spend(conn, today)
    card = budget.card_accrual(conn, today)
    income, expenses = budget.cycle_net(conn, cyc["start"], cyc["end"])

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
    signed_sum = conn.execute(q, args).fetchone()["s"]
    available = opening + signed_sum
    report = goals.goal_report(conn, today)
    earmarked = sum(g["progress_agorot"] for g in report)
    total_pace_needed = sum(g["pace_needed_agorot"] for g in report
                            if g["pace_needed_agorot"] is not None)

    prev = cycles.salary_cycle(cyc["start"] - dt.timedelta(days=1), salary_day)
    prev_income, prev_expenses = budget.cycle_net(conn, prev["start"], prev["end"])

    return {
        "user_name": db.get_setting(conn, "user_name", ""),
        "today": today.isoformat(),
        "weekday": today.strftime("%A"),
        "cycle": _iso_dict(cyc),
        "safe_to_spend": {
            "today_agorot": sts["today_agorot"],
            "today_fmt": fmt_ils(sts["today_agorot"]),
            "remaining_agorot": sts["remaining_agorot"],
            "remaining_fmt": fmt_ils(sts["remaining_agorot"]),
            "pool_agorot": sts["pool_agorot"],
            "goal_reserve_agorot": sts["goal_reserve_agorot"],
            "goal_reserve_fmt": fmt_ils(sts["goal_reserve_agorot"]),
            "days_left": sts["days_left"],
        },
        "categories": budget.category_status(conn, today),
        "card": {"total_agorot": card["total_agorot"],
                 "total_fmt": fmt_ils(card["total_agorot"]),
                 "charge_date": card["charge_date"].isoformat(),
                 "days_to_charge": card["days_to_charge"]},
        "income_this_cycle_agorot": income,
        "expenses_this_cycle_agorot": expenses,
        "balance": {"available_agorot": available,
                    "available_fmt": fmt_ils(available),
                    "earmarked_agorot": earmarked,
                    "total_agorot": available + earmarked,
                    "total_fmt": fmt_ils(available + earmarked)},
        "goals": [_iso_dict(g) for g in report],
        "monthly_savings_pace_agorot": goals.monthly_savings_pace(conn, today),
        "total_pace_needed_agorot": total_pace_needed,
        "last_cycle": {"income_agorot": prev_income,
                       "expenses_agorot": prev_expenses},
        "recent_transactions": [
            {"date": r["effective_date"], "amount_fmt": fmt_ils(r["amount_agorot"]),
             "category": r["category_name"], "description": r["description"],
             "direction": r["direction"]}
            for r in db.list_transactions(conn, limit=20)],
    }
