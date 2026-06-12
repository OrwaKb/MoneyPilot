"""Seed a ledger with two months of plausible data for UI work: `--dev` mode."""
from __future__ import annotations

import datetime as dt
import random

from app import db

ITEMS = [  # (category, description, lo_ils, hi_ils, weight)
    ("Food out", "falafel", 18, 45, 5), ("Food out", "coffee", 10, 18, 6),
    ("Food out", "shawarma with karim", 35, 55, 2),
    ("Groceries", "supermarket run", 80, 350, 3),
    ("Transport", "fuel", 180, 280, 1), ("Transport", "bus", 6, 12, 4),
    ("Fun", "cinema", 40, 60, 1), ("Fun", "steam game", 30, 90, 1),
    ("Health", "pharmacy", 20, 80, 1), ("Shopping", "stuff", 40, 200, 1),
]


def seed(conn, today: dt.date) -> None:
    rng = random.Random(42)  # deterministic seed data
    db.set_setting(conn, "user_name", "Orwa")
    db.set_setting(conn, "salary_day", "10")
    db.set_setting(conn, "salary_amount_agorot", "900000")
    db.set_setting(conn, "card_charge_day", "2")
    db.set_setting(conn, "opening_balance_agorot", "650000")
    db.set_setting(conn, "opening_balance_date",
                   (today - dt.timedelta(days=70)).isoformat())
    budgets = {"Food out": 70000, "Groceries": 140000, "Transport": 50000,
               "Bills": 280000, "Fun": 45000, "Health": 30000,
               "Shopping": 40000}
    for name, amt in budgets.items():
        db.set_budget(conn, db.category_id_by_name(conn, name), amt)
    g1 = db.add_goal(conn, name="Drone", emoji="🚁", type="purchase_fund",
                     target_agorot=450000)
    g2 = db.add_goal(conn, name="Summer trip", emoji="✈️", type="save_by_date",
                     target_agorot=1000000,
                     target_date=today + dt.timedelta(days=80))
    for back in range(70, -1, -1):
        day = today - dt.timedelta(days=back)
        if day.day == 10:
            db.add_transaction(conn, effective_date=day, amount_agorot=900000,
                               direction="income",
                               category_id=db.category_id_by_name(conn, "Salary"),
                               description="salary", source="onboarding")
        if day.day == 1:
            db.add_transaction(conn, effective_date=day, amount_agorot=-250000,
                               direction="expense",
                               category_id=db.category_id_by_name(conn, "Bills"),
                               description="rent + bills", source="onboarding")
        if day.day == 12:
            db.add_transaction(conn, effective_date=day, amount_agorot=-60000,
                               direction="goal_contribution",
                               goal_id=(g1 if day.month % 2 else g2),
                               description="monthly saving", source="onboarding")
        for _ in range(rng.choice([0, 1, 1, 2])):
            cat, desc, lo, hi, _w = rng.choices(ITEMS,
                                                weights=[i[4] for i in ITEMS])[0]
            db.add_transaction(
                conn, effective_date=day,
                amount_agorot=-rng.randint(lo * 100, hi * 100),
                direction="expense",
                category_id=db.category_id_by_name(conn, cat),
                description=desc, source="ai", ai_confidence=0.9)


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "dev.db"
    c = db.connect(target)
    db.init_db(c)
    seed(c, dt.date.today())
    print(f"seeded {target}")
