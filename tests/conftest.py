import datetime as dt

import pytest

TODAY = dt.date(2026, 6, 11)  # Thursday; salary_day=10 → cycle started 2026-06-10


@pytest.fixture
def conn(tmp_path):
    from app import db
    c = db.connect(tmp_path / "test.db")
    db.init_db(c)
    yield c
    c.close()


@pytest.fixture
def seeded(conn):
    from app import db
    db.set_setting(conn, "user_name", "Tester")
    db.set_setting(conn, "salary_day", "10")
    db.set_setting(conn, "salary_amount_agorot", "900000")   # ₪9,000
    db.set_setting(conn, "card_charge_day", "2")
    db.set_setting(conn, "opening_balance_agorot", "500000") # ₪5,000
    db.set_setting(conn, "opening_balance_date", "2026-06-01")
    for name, amt in [("Food out", 60000), ("Groceries", 120000),
                      ("Transport", 40000), ("Bills", 250000), ("Fun", 40000)]:
        db.set_budget(conn, db.category_id_by_name(conn, name), amt)
    return conn
