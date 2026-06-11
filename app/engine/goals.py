from __future__ import annotations

import datetime as dt

from app import db
from app.engine import budget, cycles
from app.models import fmt_ils

AVG_MONTH_DAYS = 30.44


def goal_progress(conn, goal_id: int) -> int:
    return conn.execute(
        "SELECT COALESCE(SUM(-amount_agorot),0) AS s FROM transactions"
        " WHERE deleted_at IS NULL AND direction='goal_contribution'"
        " AND goal_id=?", (goal_id,)).fetchone()["s"]


def monthly_savings_pace(conn, today: dt.date, n_cycles: int = 3) -> int:
    """Average (income − expenses) over the last n completed salary cycles;
    falls back to the current cycle's net-so-far if none are complete."""
    salary_day = int(db.get_setting(conn, "salary_day", "1"))
    cyc = cycles.salary_cycle(today, salary_day)
    nets = []
    cursor = cyc["start"]
    for _ in range(n_cycles):
        prev = cycles.salary_cycle(cursor - dt.timedelta(days=1), salary_day)
        income, expenses = budget.cycle_net(conn, prev["start"], prev["end"])
        if income or expenses:
            nets.append(income - expenses)
        cursor = prev["start"]
    if nets:
        return sum(nets) // len(nets)
    income, expenses = budget.cycle_net(conn, cyc["start"], today)
    return income - expenses


def _projection(conn, goal_id: int, remaining: int, today: dt.date):
    """Projected completion from the last 90 days of contributions."""
    since = today - dt.timedelta(days=90)
    contributed = conn.execute(
        "SELECT COALESCE(SUM(-amount_agorot),0) AS s FROM transactions"
        " WHERE deleted_at IS NULL AND direction='goal_contribution'"
        " AND goal_id=? AND effective_date >= ?",
        (goal_id, since.isoformat())).fetchone()["s"]
    if contributed <= 0 or remaining <= 0:
        return None
    per_day = contributed / 90.0
    return today + dt.timedelta(days=round(remaining / per_day))


def goal_report(conn, today: dt.date) -> list[dict]:
    out = []
    for g in db.list_goals(conn):
        progress = goal_progress(conn, g["id"])
        target = g["target_agorot"]
        remaining = max(target - progress, 0)
        pct = min(int(progress * 100 / target), 100) if target else 0
        pace_needed = None
        if g["target_date"]:
            tdate = dt.date.fromisoformat(g["target_date"])
            months_left = max((tdate - today).days / AVG_MONTH_DAYS, 0.1)
            pace_needed = int(remaining / months_left)
        if remaining == 0:
            verdict = "ready"
        elif g["type"] == "purchase_fund":
            verdict = f"{fmt_ils(remaining)} to go"
        else:
            verdict = "on track" if pace_needed is not None and \
                monthly_savings_pace(conn, today) >= pace_needed else "behind"
        out.append({
            "id": g["id"], "name": g["name"], "emoji": g["emoji"],
            "type": g["type"], "target_agorot": target,
            "target_date": g["target_date"],
            "progress_agorot": progress, "remaining_agorot": remaining,
            "pct": pct, "pace_needed_agorot": pace_needed,
            "projected_date": _projection(conn, g["id"], remaining, today),
            "verdict": verdict,
        })
    return out
