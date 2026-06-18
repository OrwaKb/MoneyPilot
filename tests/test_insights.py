import datetime as dt
import json

from app import db
from app.engine import insights

TODAY = dt.date(2026, 6, 11)


def test_fact_pack_shape_and_json_safe(seeded):
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-4500,
                       direction="expense",
                       category_id=db.category_id_by_name(seeded, "Food out"),
                       description="falafel")
    db.add_transaction(seeded, effective_date=dt.date(2026, 6, 10),
                       amount_agorot=900000, direction="income",
                       category_id=db.category_id_by_name(seeded, "Salary"))
    gid = db.add_goal(seeded, name="Drone", type="purchase_fund",
                      target_agorot=450000)
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-171000,
                       direction="goal_contribution", goal_id=gid)
    fp = insights.fact_pack(seeded, TODAY)
    for key in ("user_name", "today", "cycle", "safe_to_spend", "categories",
                "card", "goals", "balance", "last_cycle",
                "total_pace_needed_agorot"):
        assert key in fp, key
    json.dumps(fp)  # must be JSON-serializable as-is

def test_safe_to_spend_surfaces_rolling_allowance_fields(seeded):
    # the cockpit/widget read the per-day accrual and discretionary spend so they
    # can render "+₪X/day" alongside the rolling hero
    from app.models import fmt_ils
    sts = insights.fact_pack(seeded, TODAY)["safe_to_spend"]
    for k in ("today_agorot", "daily_allowance_agorot", "cycle_spent_agorot",
              "remaining_agorot"):
        assert k in sts, k
    assert sts["daily_allowance_fmt"] == fmt_ils(sts["daily_allowance_agorot"])
    assert sts["cycle_spent_fmt"] == fmt_ils(sts["cycle_spent_agorot"])


def test_safe_to_spend_surfaces_goal_reserve(seeded):
    # the deadline-goal savings reserve is exposed (agorot + formatted) so the
    # cockpit/widget can show why safe-to-spend is lower
    from app.models import fmt_ils
    db.add_goal(seeded, name="Drone", type="save_by_date",
                target_agorot=300000, target_date=dt.date(2026, 9, 9))
    sts = insights.fact_pack(seeded, TODAY)["safe_to_spend"]
    assert sts["goal_reserve_agorot"] > 0
    assert sts["goal_reserve_fmt"] == fmt_ils(sts["goal_reserve_agorot"])


def test_balance_math(seeded):
    # opening 500000 + income 900000 − expense 4500 − earmarked 171000
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=900000,
                       direction="income")
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-4500,
                       direction="expense",
                       category_id=db.category_id_by_name(seeded, "Food out"))
    gid = db.add_goal(seeded, name="D", type="purchase_fund", target_agorot=450000)
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-171000,
                       direction="goal_contribution", goal_id=gid)
    fp = insights.fact_pack(seeded, TODAY)
    assert fp["balance"]["available_agorot"] == 500000 + 900000 - 4500 - 171000
    assert fp["balance"]["earmarked_agorot"] == 171000
    assert fp["balance"]["total_agorot"] == 500000 + 900000 - 4500


def test_archived_goal_money_released_to_available(seeded):
    gid = db.add_goal(seeded, name="Drone", type="purchase_fund",
                      target_agorot=450000)
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-171000,
                       direction="goal_contribution", goal_id=gid)
    fp = insights.fact_pack(seeded, TODAY)
    assert fp["balance"]["available_agorot"] == 500000 - 171000
    assert fp["balance"]["total_agorot"] == 500000
    db.update_goal(seeded, gid, status="archived")
    fp = insights.fact_pack(seeded, TODAY)
    assert fp["balance"]["available_agorot"] == 500000  # released
    assert fp["balance"]["earmarked_agorot"] == 0
    assert fp["balance"]["total_agorot"] == 500000      # invariant holds


def test_balance_ignores_pre_opening_transactions(seeded):
    # seeded opening_balance_date = 2026-06-01; this spend predates it
    db.add_transaction(seeded, effective_date=dt.date(2026, 5, 20),
                       amount_agorot=-9900, direction="expense",
                       category_id=db.category_id_by_name(seeded, "Food out"))
    fp = insights.fact_pack(seeded, TODAY)
    assert fp["balance"]["available_agorot"] == 500000


def test_recent_transactions_minimal_fields(seeded):
    db.add_transaction(seeded, effective_date=TODAY, amount_agorot=-4500,
                       direction="expense", merchant="Falafel King",
                       people="karim", raw_text="45 falafel with karim",
                       description="falafel",
                       category_id=db.category_id_by_name(seeded, "Food out"))
    (row,) = insights.fact_pack(seeded, TODAY)["recent_transactions"]
    assert set(row) == {"date", "amount_fmt", "category", "description",
                        "direction"}  # merchant/people/raw_text must NOT leak


def test_fact_pack_includes_recurring(conn):
    import datetime as dt
    from app import db
    from app.engine import insights
    for d in ("2026-03-21", "2026-04-20", "2026-05-20"):
        db.add_transaction(conn, effective_date=d, amount_agorot=-4500,
                           direction="expense", merchant="Netflix",
                           description="netflix")
    fp = insights.fact_pack(conn, dt.date(2026, 6, 20))
    assert fp["recurring"]["count"] == 1
    assert fp["recurring"]["monthly_total_fmt"].startswith("₪")
    assert fp["recurring"]["items"][0]["name"] == "Netflix"
