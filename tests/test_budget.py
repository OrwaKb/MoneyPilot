import datetime as dt

from app import db
from app.engine import budget

TODAY = dt.date(2026, 6, 11)  # cycle = Jun 10 .. Jul 9 (salary_day 10)


def _spend(conn, cat, agorot, day=TODAY, method="card"):
    db.add_transaction(conn, effective_date=day, amount_agorot=-agorot,
                       direction="expense",
                       category_id=db.category_id_by_name(conn, cat),
                       payment_method=method, description="t")


def test_safe_to_spend_projects_unreceived_salary(seeded):
    # safe-to-spend = (balance + salary you'll still get this cycle) / days to
    # payday. Category budgets do NOT define it. seeded: opening ₪5,000, salary
    # ₪9,000 (setting), nothing logged yet -> the ₪9,000 is anticipated.
    s = budget.safe_to_spend(seeded, TODAY)
    assert s["available_agorot"] == 500000           # actual money now
    assert s["expected_salary_agorot"] == 900000     # not received yet
    assert s["goal_reserve_agorot"] == 0
    assert s["remaining_agorot"] == 500000 + 900000
    assert s["days_left"] == 29
    assert s["today_agorot"] == (500000 + 900000) // 29

def test_safe_to_spend_matches_user_worked_example(tmp_path):
    # balance −₪2,500, salary ₪5,000, ~27 days left -> ~₪92/day
    c = db.connect(tmp_path / "x.db"); db.init_db(c)
    db.set_setting(c, "salary_day", "10")
    db.set_setting(c, "salary_amount_agorot", "500000")     # ₪5,000
    db.set_setting(c, "opening_balance_agorot", "-250000")  # ₪−2,500
    db.set_setting(c, "opening_balance_date", "2026-06-01")
    s = budget.safe_to_spend(c, dt.date(2026, 6, 13))       # cycle Jun10..Jul9
    assert s["days_left"] == 27
    assert s["remaining_agorot"] == 250000                  # −2500 + 5000
    assert s["today_agorot"] == 250000 // 27                # ≈ ₪92.59

def test_salary_anticipated_when_payday_precedes_opening_date(seeded):
    # Real-world bug: you set your opening balance AFTER this cycle's payday, so
    # the salary income is dated before opening_date -> excluded from available.
    # It must still be anticipated (not treated as already on-hand), else a
    # payday that precedes your opening snapshot vanishes and STS reads ~0.
    db.set_setting(seeded, "opening_balance_date", "2026-06-11")   # = TODAY
    db.set_setting(seeded, "opening_balance_agorot", "-256162")    # overdrawn
    db.add_transaction(seeded, effective_date=dt.date(2026, 6, 10),  # before opening
                       amount_agorot=900000, direction="income",
                       category_id=db.category_id_by_name(seeded, "Salary"))
    s = budget.safe_to_spend(seeded, TODAY)
    assert s["available_agorot"] == -256162          # salary excluded (pre-opening)
    assert s["expected_salary_agorot"] == 900000     # ...so it's anticipated, not lost
    assert s["remaining_agorot"] == -256162 + 900000

def test_safe_to_spend_stable_once_salary_logged(seeded):
    # logging the salary must NOT double-count: remaining is unchanged
    before = budget.safe_to_spend(seeded, TODAY)["remaining_agorot"]
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=900000,
                       direction="income",
                       category_id=db.category_id_by_name(seeded, "Salary"))
    after = budget.safe_to_spend(seeded, TODAY)
    assert after["expected_salary_agorot"] == 0      # now received
    assert after["available_agorot"] == 500000 + 900000
    assert after["remaining_agorot"] == before       # same spendable

def test_category_budget_size_does_not_change_safe_to_spend(seeded):
    # a huge Fun budget does NOT raise safe-to-spend — budgets aren't the pool
    base = budget.safe_to_spend(seeded, TODAY)["today_agorot"]
    db.set_budget(seeded, db.category_id_by_name(seeded, "Fun"), 9_000_000)
    assert budget.safe_to_spend(seeded, TODAY)["today_agorot"] == base

def test_spending_reduces_available_money_any_category(seeded):
    # money is money: a fixed Bills expense reduces available like any other
    _spend(seeded, "Bills", 100000)
    assert budget.safe_to_spend(seeded, TODAY)["available_agorot"] == 400000

def test_income_raises_available_money(seeded):
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=900000,
                       direction="income",
                       category_id=db.category_id_by_name(seeded, "Salary"))
    assert budget.safe_to_spend(seeded, TODAY)["available_agorot"] == 1_400_000

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

def test_safe_to_spend_clamps_to_zero_when_overdrawn(seeded):
    # spend beyond balance + the anticipated salary -> nothing safe to spend
    _spend(seeded, "Fun", 1_500_000)   # opening 500000 + salary 900000 = 1.4M
    s = budget.safe_to_spend(seeded, TODAY)
    assert s["remaining_agorot"] < 0 and s["today_agorot"] == 0

def test_soft_deleted_expense_not_counted(seeded):
    _spend(seeded, "Food out", 4500)
    tid = db.list_transactions(seeded)[0]["id"]
    db.soft_delete_transaction(seeded, tid)
    assert budget.safe_to_spend(seeded, TODAY)["available_agorot"] == 500000

def test_card_purchase_on_charge_date_rolls_to_next_statement(seeded):
    _spend(seeded, "Food out", 5000, day=dt.date(2026, 7, 2))  # ON charge day
    acc = budget.card_accrual(seeded, TODAY)  # window Jun 2 .. Jul 1
    assert acc["total_agorot"] == 0

def test_unbudgeted_spend_has_null_pace(seeded):
    _spend(seeded, "Health", 50000)  # Health has no budget in seeds
    rows = {r["name"]: r for r in budget.category_status(seeded, TODAY)}
    assert rows["Health"]["spent_agorot"] == 50000
    assert rows["Health"]["pace_ratio"] is None


def test_safe_to_spend_subtracts_goal_savings(seeded):
    # a deadline goal reserves savings out of your spendable money
    base = budget.safe_to_spend(seeded, TODAY)
    assert base["goal_reserve_agorot"] == 0           # no goals yet
    db.add_goal(seeded, name="Drone", type="save_by_date",
                target_agorot=100000, target_date=dt.date(2026, 9, 9))
    s = budget.safe_to_spend(seeded, TODAY)
    assert 0 < s["goal_reserve_agorot"] <= 100000
    assert s["remaining_agorot"] == base["remaining_agorot"] - s["goal_reserve_agorot"]
    assert s["today_agorot"] == s["remaining_agorot"] // s["days_left"]


def test_goal_reserve_capped_at_amount_remaining(seeded):
    # a near-deadline goal must NOT reserve more than what's left to save
    # (the monthly pace explodes for a sub-month deadline; cap it at remaining)
    db.add_goal(seeded, name="Rent", type="save_by_date",
                target_agorot=300000, target_date=dt.date(2026, 6, 20))  # ~9 days
    from app.engine import goals
    assert goals.cycle_savings_reserve(seeded, TODAY) == 300000  # not 10x pace


def test_deadlineless_fund_reserves_nothing(seeded):
    # an open-ended purchase fund (no target date) imposes no forced reserve
    db.add_goal(seeded, name="Someday", type="purchase_fund",
                target_agorot=500000, target_date=None)
    assert budget.safe_to_spend(seeded, TODAY)["goal_reserve_agorot"] == 0


def test_goal_reserve_shrinks_as_goal_is_funded(seeded):
    # contributing lowers remaining-to-target -> lower pace -> smaller reserve
    gid = db.add_goal(seeded, name="Drone", type="save_by_date",
                      target_agorot=100000, target_date=dt.date(2026, 9, 9))
    before = budget.safe_to_spend(seeded, TODAY)["goal_reserve_agorot"]
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-50000,
                       direction="goal_contribution", goal_id=gid,
                       description="half funded")
    after = budget.safe_to_spend(seeded, TODAY)["goal_reserve_agorot"]
    assert 0 <= after < before
