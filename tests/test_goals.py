import datetime as dt

from app import db
from app.engine import goals

TODAY = dt.date(2026, 6, 11)


def _contribute(conn, gid, agorot, day):
    db.add_transaction(conn, effective_date=day, amount_agorot=-agorot,
                       direction="goal_contribution", goal_id=gid,
                       description="saving")


def test_progress_and_pct(seeded):
    gid = db.add_goal(seeded, name="Drone", type="purchase_fund",
                      target_agorot=450000)
    _contribute(seeded, gid, 100000, dt.date(2026, 5, 20))
    _contribute(seeded, gid, 71000, dt.date(2026, 6, 5))
    (r,) = goals.goal_report(seeded, TODAY)
    assert r["progress_agorot"] == 171000
    assert r["pct"] == 38  # 171000/450000

def test_pace_needed_for_dated_goal(seeded):
    gid = db.add_goal(seeded, name="Trip", type="save_by_date",
                      target_agorot=300000, target_date=dt.date(2026, 9, 11))
    _contribute(seeded, gid, 60000, dt.date(2026, 6, 1))
    (r,) = goals.goal_report(seeded, TODAY)
    # 240000 remaining over ~3.02 months → ≈ 79500/mo; allow rounding window
    assert 75000 <= r["pace_needed_agorot"] <= 82000

def test_projection_from_recent_contributions(seeded):
    gid = db.add_goal(seeded, name="Drone", type="purchase_fund",
                      target_agorot=200000)
    _contribute(seeded, gid, 50000, TODAY - dt.timedelta(days=60))
    _contribute(seeded, gid, 50000, TODAY - dt.timedelta(days=30))
    (r,) = goals.goal_report(seeded, TODAY)
    assert r["projected_date"] is not None  # ~2 months away at 50k/30d

def test_fund_verdict_ready(seeded):
    gid = db.add_goal(seeded, name="Game", type="purchase_fund",
                      target_agorot=10000)
    _contribute(seeded, gid, 10000, TODAY)
    (r,) = goals.goal_report(seeded, TODAY)
    assert r["verdict"] == "ready"

def test_no_contributions_no_projection(seeded):
    db.add_goal(seeded, name="Empty", type="purchase_fund", target_agorot=5000)
    (r,) = goals.goal_report(seeded, TODAY)
    assert r["progress_agorot"] == 0 and r["projected_date"] is None


def _cycle_income_and_spend(conn, month, income=900000, spend=50000):
    # salary lands on the 10th; one expense mid-cycle
    db.add_transaction(conn, effective_date=dt.date(2026, month, 10),
                       amount_agorot=income, direction="income",
                       category_id=db.category_id_by_name(conn, "Salary"))
    db.add_transaction(conn, effective_date=dt.date(2026, month, 15),
                       amount_agorot=-spend, direction="expense",
                       category_id=db.category_id_by_name(conn, "Food out"))


def test_monthly_savings_pace_averages_completed_cycles(seeded):
    for m in (3, 4, 5):  # cycles Mar10-Apr9, Apr10-May9, May10-Jun9
        _cycle_income_and_spend(seeded, m)
    assert goals.monthly_savings_pace(seeded, TODAY) == 850000

def test_monthly_savings_pace_skips_empty_cycles(seeded):
    _cycle_income_and_spend(seeded, 5)  # only one active prior cycle
    assert goals.monthly_savings_pace(seeded, TODAY) == 850000

def test_monthly_savings_pace_fallback_current_cycle(seeded):
    db.add_transaction(seeded, effective_date=dt.date(2026, 6, 10),
                       amount_agorot=900000, direction="income",
                       category_id=db.category_id_by_name(seeded, "Salary"))
    assert goals.monthly_savings_pace(seeded, TODAY) == 900000

def test_dated_goal_verdicts(seeded):
    for m in (3, 4, 5):
        _cycle_income_and_spend(seeded, m)  # pace 850000
    db.add_goal(seeded, name="Easy", type="save_by_date",
                target_agorot=100000, target_date=dt.date(2026, 12, 1))
    db.add_goal(seeded, name="Hard", type="save_by_date",
                target_agorot=99000000, target_date=dt.date(2026, 12, 1))
    rows = {r["name"]: r for r in goals.goal_report(seeded, TODAY)}
    assert rows["Easy"]["verdict"] == "on track"
    assert rows["Hard"]["verdict"] == "behind"

def test_dateless_save_by_date_falls_back_to_amount_verdict(seeded):
    db.add_goal(seeded, name="Someday", type="save_by_date",
                target_agorot=100000)  # no target_date
    (r,) = goals.goal_report(seeded, TODAY)
    assert r["verdict"].endswith("to go")
