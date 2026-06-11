import datetime as dt
import json

from app import db

D = dt.date(2026, 6, 11)


def test_goals_crud(conn):
    gid = db.add_goal(conn, name="Drone", emoji="🚁", type="purchase_fund",
                      target_agorot=450000)
    goals = db.list_goals(conn)
    assert goals[0]["id"] == gid and goals[0]["status"] == "active"
    db.update_goal(conn, gid, status="archived")
    assert db.list_goals(conn) == []  # default: active only
    assert len(db.list_goals(conn, include_archived=True)) == 1

def test_get_goal_by_name(conn):
    db.add_goal(conn, name="Summer Trip", type="save_by_date",
                target_agorot=1000000, target_date=dt.date(2026, 8, 1))
    assert db.get_goal_by_name(conn, "summer trip") is not None
    assert db.get_goal_by_name(conn, "drone") is None

def test_budgets(conn):
    food = db.category_id_by_name(conn, "Food out")
    db.set_budget(conn, food, 60000)
    db.set_budget(conn, food, 70000)  # upsert
    assert db.get_budgets(conn)[food] == 70000

def test_briefing_cache(conn):
    assert db.get_briefing(conn, "2026-06-11") is None
    db.put_briefing(conn, "2026-06-11", "all good", "{}")
    assert db.get_briefing(conn, "2026-06-11")["text"] == "all good"

def test_chat(conn):
    db.add_chat(conn, "user", "hello")
    db.add_chat(conn, "assistant", "hi")
    rows = db.recent_chat(conn, 10)
    assert [r["role"] for r in rows] == ["user", "assistant"]  # oldest first

def test_export_import_roundtrip(conn, tmp_path):
    db.set_setting(conn, "salary_day", "10")
    db.add_goal(conn, name="G", type="purchase_fund", target_agorot=100)
    db.add_transaction(conn, effective_date=D, amount_agorot=-100,
                       direction="expense")
    data = db.export_json(conn)
    c2 = db.connect(tmp_path / "restored.db")
    db.init_db(c2)
    db.import_json(c2, data)
    assert db.get_setting(c2, "salary_day") == "10"
    assert len(db.list_transactions(c2)) == 1
    assert len(db.list_goals(c2)) == 1

def test_daily_backup_written_once_and_pruned(conn, tmp_path):
    bdir = tmp_path / "backups"
    p1 = db.write_daily_backup(conn, bdir, D)
    p2 = db.write_daily_backup(conn, bdir, D)
    assert p1 is not None and p2 is None  # second call same day: skipped
    for i in range(35):  # fabricate old backups to test pruning
        (bdir / f"ledger-2026-04-{i:02d}.json").write_text("{}", encoding="utf-8")
    db.write_daily_backup(conn, bdir, D + dt.timedelta(days=1))
    assert len(list(bdir.glob("ledger-*.json"))) == 30
