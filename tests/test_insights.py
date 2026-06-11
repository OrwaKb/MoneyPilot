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
