import datetime as dt

from app import db
from app.engine import budget

TODAY = dt.date(2026, 6, 11)  # cycle = Jun 10 .. Jul 9 (salary_day 10)


def _spend(conn, cat, agorot, day=TODAY, method="card"):
    db.add_transaction(conn, effective_date=day, amount_agorot=-agorot,
                       direction="expense",
                       category_id=db.category_id_by_name(conn, cat),
                       payment_method=method, description="t")


def test_safe_to_spend_basic(seeded):
    # discretionary pool = 60000+120000+40000+40000 = 260000 (Bills excluded)
    _spend(seeded, "Food out", 4500)
    s = budget.safe_to_spend(seeded, TODAY)
    assert s["pool_agorot"] == 260000
    assert s["spent_agorot"] == 4500
    assert s["days_left"] == 29
    assert s["today_agorot"] == (260000 - 4500) // 29

def test_safe_to_spend_excludes_bills_and_cash_counts(seeded):
    _spend(seeded, "Bills", 100000)          # fixed → not discretionary
    _spend(seeded, "Food out", 1000, method="cash")  # cash still counts
    s = budget.safe_to_spend(seeded, TODAY)
    assert s["spent_agorot"] == 1000

def test_safe_to_spend_ignores_out_of_cycle(seeded):
    _spend(seeded, "Food out", 9999, day=dt.date(2026, 6, 9))  # previous cycle
    assert budget.safe_to_spend(seeded, TODAY)["spent_agorot"] == 0

def test_category_status_pace(seeded):
    _spend(seeded, "Food out", 30000)  # half the 60000 budget on day 2 of 30
    rows = {r["name"]: r for r in budget.category_status(seeded, TODAY)}
    food = rows["Food out"]
    assert food["spent_agorot"] == 30000 and food["budget_agorot"] == 60000
    assert food["pace_ratio"] > 1.0  # way over pace this early

def test_card_accrual_window_and_methods(seeded):
    _spend(seeded, "Food out", 5000)                       # card, in window
    _spend(seeded, "Fun", 2000, method="cash")             # cash → excluded
    _spend(seeded, "Food out", 7000, day=dt.date(2026, 5, 30))  # previous statement
    acc = budget.card_accrual(seeded, TODAY)
    assert acc["total_agorot"] == 5000
    assert acc["charge_date"] == dt.date(2026, 7, 2)

def test_cycle_net(seeded):
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=900000,
                       direction="income",
                       category_id=db.category_id_by_name(seeded, "Salary"))
    _spend(seeded, "Food out", 4500)
    income, expenses = budget.cycle_net(seeded, dt.date(2026, 6, 10),
                                        dt.date(2026, 7, 9))
    assert income == 900000 and expenses == 4500
