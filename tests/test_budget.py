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

def test_safe_to_spend_overspent_clamps_to_zero(seeded):
    _spend(seeded, "Food out", 999999)  # blow the whole pool
    s = budget.safe_to_spend(seeded, TODAY)
    assert s["remaining_agorot"] < 0 and s["today_agorot"] == 0

def test_soft_deleted_excluded_from_spend(seeded):
    _spend(seeded, "Food out", 4500)
    tid = db.list_transactions(seeded)[0]["id"]
    db.soft_delete_transaction(seeded, tid)
    assert budget.safe_to_spend(seeded, TODAY)["spent_agorot"] == 0

def test_card_purchase_on_charge_date_rolls_to_next_statement(seeded):
    _spend(seeded, "Food out", 5000, day=dt.date(2026, 7, 2))  # ON charge day
    acc = budget.card_accrual(seeded, TODAY)  # window Jun 2 .. Jul 1
    assert acc["total_agorot"] == 0

def test_unbudgeted_spend_has_null_pace(seeded):
    _spend(seeded, "Health", 50000)  # Health has no budget in seeds
    rows = {r["name"]: r for r in budget.category_status(seeded, TODAY)}
    assert rows["Health"]["spent_agorot"] == 50000
    assert rows["Health"]["pace_ratio"] is None


def test_safe_to_spend_reserves_deadline_goal_savings(seeded):
    # money you must save this month to hit a deadline goal is NOT safe to spend
    from app.engine import goals
    base = budget.safe_to_spend(seeded, TODAY)
    assert base["goal_reserve_agorot"] == 0           # no goals yet
    db.add_goal(seeded, name="Drone", type="save_by_date",
                target_agorot=300000, target_date=dt.date(2026, 9, 9))
    pace = sum(g["pace_needed_agorot"] for g in goals.goal_report(seeded, TODAY)
               if g["pace_needed_agorot"])
    assert pace > 0
    s = budget.safe_to_spend(seeded, TODAY)
    assert s["goal_reserve_agorot"] == pace
    assert s["remaining_agorot"] == base["remaining_agorot"] - pace
    assert s["today_agorot"] == s["remaining_agorot"] // s["days_left"]


def test_safe_to_spend_ignores_deadlineless_fund(seeded):
    # an open-ended purchase fund (no target date) imposes no forced reserve
    db.add_goal(seeded, name="Someday", type="purchase_fund",
                target_agorot=500000, target_date=None)
    s = budget.safe_to_spend(seeded, TODAY)
    assert s["goal_reserve_agorot"] == 0


def test_safe_to_spend_goal_reserve_shrinks_as_goal_is_funded(seeded):
    # contributing toward the goal lowers remaining-to-target -> lower pace ->
    # smaller reserve (progress-aware, no double counting)
    gid = db.add_goal(seeded, name="Drone", type="save_by_date",
                      target_agorot=300000, target_date=dt.date(2026, 9, 9))
    before = budget.safe_to_spend(seeded, TODAY)["goal_reserve_agorot"]
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-150000,
                       direction="goal_contribution", goal_id=gid,
                       description="half funded")
    after = budget.safe_to_spend(seeded, TODAY)["goal_reserve_agorot"]
    assert 0 <= after < before
