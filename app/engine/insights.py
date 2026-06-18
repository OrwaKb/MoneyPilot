from __future__ import annotations

import datetime as dt

from app import db
from app.engine import budget, cycles, goals, recurring
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

    available = budget.available_balance(conn)
    report = goals.goal_report(conn, today)
    earmarked = sum(g["progress_agorot"] for g in report)
    total_pace_needed = sum(g["pace_needed_agorot"] for g in report
                            if g["pace_needed_agorot"] is not None)

    prev = cycles.salary_cycle(cyc["start"] - dt.timedelta(days=1), salary_day)
    prev_income, prev_expenses = budget.cycle_net(conn, prev["start"], prev["end"])

    rec = recurring.summary(conn, today)

    return {
        "user_name": db.get_setting(conn, "user_name", ""),
        "today": today.isoformat(),
        "weekday": today.strftime("%A"),
        "cycle": _iso_dict(cyc),
        "safe_to_spend": {
            # today_agorot is the ROLLING daily-allowance balance: it banks
            # unspent days and goes negative when you overspend (recovers as the
            # allowance accrues). daily_allowance_agorot is the per-day accrual.
            "today_agorot": sts["today_agorot"],
            "today_fmt": fmt_ils(sts["today_agorot"]),
            "daily_allowance_agorot": sts["daily_allowance_agorot"],
            "daily_allowance_fmt": fmt_ils(sts["daily_allowance_agorot"]),
            "remaining_agorot": sts["remaining_agorot"],
            "remaining_fmt": fmt_ils(sts["remaining_agorot"]),
            "cycle_spent_agorot": sts["cycle_spent_agorot"],
            "cycle_spent_fmt": fmt_ils(sts["cycle_spent_agorot"]),
            "available_agorot": sts["available_agorot"],
            "available_fmt": fmt_ils(sts["available_agorot"]),
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
        "recurring": {
            "monthly_total_agorot": rec["monthly_total_agorot"],
            "monthly_total_fmt": fmt_ils(rec["monthly_total_agorot"]),
            "count": len(rec["items"]),
            "items": [{"name": i["name"], "cadence": i["cadence"],
                       "typical_fmt": fmt_ils(i["typical_agorot"]),
                       "next_expected": i["next_expected"],
                       "monthly_equiv_fmt": fmt_ils(i["monthly_equiv_agorot"])}
                      for i in rec["items"][:5]],
            "upcoming": [{"name": i["name"],
                          "typical_fmt": fmt_ils(i["typical_agorot"]),
                          "next_expected": i["next_expected"]}
                         for i in rec["upcoming"]],
        },
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
