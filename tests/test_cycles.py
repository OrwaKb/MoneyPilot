import datetime as dt

from app.engine.cycles import card_window, clamped_date, salary_cycle


def test_clamp_day_31_in_short_month():
    assert clamped_date(2026, 6, 31) == dt.date(2026, 6, 30)
    assert clamped_date(2026, 2, 30) == dt.date(2026, 2, 28)
    assert clamped_date(2024, 2, 30) == dt.date(2024, 2, 29)  # leap

def test_cycle_mid_month():
    c = salary_cycle(dt.date(2026, 6, 11), salary_day=10)
    assert c["start"] == dt.date(2026, 6, 10)
    assert c["end"] == dt.date(2026, 7, 9)
    assert c["day_index"] == 2 and c["length"] == 30 and c["days_left"] == 29

def test_cycle_on_salary_day():
    c = salary_cycle(dt.date(2026, 6, 10), salary_day=10)
    assert c["start"] == dt.date(2026, 6, 10) and c["day_index"] == 1

def test_cycle_day_before_salary():
    c = salary_cycle(dt.date(2026, 6, 9), salary_day=10)
    assert c["start"] == dt.date(2026, 5, 10) and c["days_left"] == 1

def test_cycle_straddles_year():
    c = salary_cycle(dt.date(2026, 1, 5), salary_day=10)
    assert c["start"] == dt.date(2025, 12, 10)
    assert c["end"] == dt.date(2026, 1, 9)

def test_cycle_salary_day_31_clamps():
    c = salary_cycle(dt.date(2026, 6, 30), salary_day=31)
    # June's anchor clamps to Jun 30 → today IS cycle start
    assert c["start"] == dt.date(2026, 6, 30)
    assert c["end"] == dt.date(2026, 7, 30)  # day before Jul 31 anchor

def test_card_window_purchase_on_charge_day_rolls_forward():
    w = card_window(dt.date(2026, 6, 2), charge_day=2)
    # today IS charge day → today's purchases belong to the NEXT statement
    assert w["start"] == dt.date(2026, 6, 2)
    assert w["charge_date"] == dt.date(2026, 7, 2)
    assert w["days_to_charge"] == 30

def test_card_window_day_before_charge():
    w = card_window(dt.date(2026, 6, 1), charge_day=2)
    assert w["start"] == dt.date(2026, 5, 2)
    assert w["charge_date"] == dt.date(2026, 6, 2)
    assert w["days_to_charge"] == 1

def test_card_window_feb_leap():
    w = card_window(dt.date(2024, 2, 29), charge_day=30)
    # Feb 2024 anchor clamps to Feb 29 → today is a charge day
    assert w["start"] == dt.date(2024, 2, 29)
    assert w["charge_date"] == dt.date(2024, 3, 30)
