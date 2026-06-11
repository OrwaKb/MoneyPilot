from app import db


def test_init_seeds_categories(conn):
    cats = db.categories(conn)
    names = {c["name"] for c in cats}
    assert "Food out" in names and "Salary" in names
    assert len(cats) == 12

def test_bills_is_fixed(conn):
    bills = [c for c in db.categories(conn) if c["name"] == "Bills"][0]
    assert bills["is_fixed"] == 1

def test_init_is_idempotent(conn):
    db.init_db(conn)  # second call must not duplicate seeds
    assert len(db.categories(conn)) == 12

def test_settings_roundtrip(conn):
    db.set_setting(conn, "salary_day", "10")
    assert db.get_setting(conn, "salary_day") == "10"
    assert db.get_setting(conn, "missing", "dflt") == "dflt"
    assert db.get_settings(conn)["salary_day"] == "10"

def test_category_id_by_name_case_insensitive(conn):
    assert db.category_id_by_name(conn, "food OUT") is not None
    assert db.category_id_by_name(conn, "nope") is None
