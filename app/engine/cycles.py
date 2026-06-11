from __future__ import annotations

import calendar
import datetime as dt


def clamped_date(year: int, month: int, day: int) -> dt.date:
    """The requested day, clamped to the month's last day (salary day 31 → Jun 30)."""
    return dt.date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _next_anchor(today: dt.date, day: int) -> dt.date:
    """First clamped anchor date STRICTLY after today."""
    cand = clamped_date(today.year, today.month, day)
    if cand > today:
        return cand
    y, m = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    return clamped_date(y, m, day)


def _last_anchor(today: dt.date, day: int) -> dt.date:
    """Last clamped anchor date on or before today."""
    cand = clamped_date(today.year, today.month, day)
    if cand <= today:
        return cand
    y, m = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
    return clamped_date(y, m, day)


def salary_cycle(today: dt.date, salary_day: int) -> dict:
    start = _last_anchor(today, salary_day)
    nxt = _next_anchor(today, salary_day)
    return {
        "start": start,
        "end": nxt - dt.timedelta(days=1),
        "day_index": (today - start).days + 1,
        "length": (nxt - start).days,
        "days_left": (nxt - today).days,  # includes today
    }


def card_window(today: dt.date, charge_day: int) -> dict:
    """Window of purchases accruing to the UPCOMING charge.
    A purchase ON a charge date belongs to the next statement, so:
    start = last charge date <= today, charge_date = first charge date > today,
    and accrual sums purchases with start <= date < charge_date."""
    return {
        "start": _last_anchor(today, charge_day),
        "charge_date": _next_anchor(today, charge_day),
        "days_to_charge": (_next_anchor(today, charge_day) - today).days,
    }
