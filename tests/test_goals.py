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
