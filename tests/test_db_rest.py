import datetime as dt
import json

import pytest

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


def test_conversation_crud(conn):
    cid = db.add_conversation(conn, "My budget chat")
    convs = db.list_conversations(conn)
    assert len(convs) == 1
    assert convs[0]["id"] == cid and convs[0]["title"] == "My budget chat"
    assert convs[0]["msg_count"] == 0
    db.delete_conversation(conn, cid)
    assert db.list_conversations(conn) == []


def test_add_conversation_strips_and_defaults_title(conn):
    a = db.add_conversation(conn, "  spaced  ")
    b = db.add_conversation(conn, "   ")  # empty after strip → 'New chat'
    convs = {c["id"]: c["title"] for c in db.list_conversations(conn)}
    assert convs[a] == "spaced"
    assert convs[b] == "New chat"


def test_list_conversations_orders_by_last_activity(conn):
    first = db.add_conversation(conn, "first")
    second = db.add_conversation(conn, "second")
    # message in `first` makes it the most recently active despite being older
    db.add_chat(conn, "user", "hi there", conversation_id=first)
    convs = db.list_conversations(conn)
    assert [c["id"] for c in convs] == [first, second]
    assert convs[0]["msg_count"] == 1
    assert convs[0]["last_ts"] >= convs[0]["created_at"]
    # empty conversation falls back to created_at for last_ts
    assert convs[1]["last_ts"] == convs[1]["created_at"]
    assert convs[1]["msg_count"] == 0


def test_recent_chat_filtered_vs_unfiltered(conn):
    a = db.add_conversation(conn, "A")
    b = db.add_conversation(conn, "B")
    db.add_chat(conn, "user", "in-a", conversation_id=a)
    db.add_chat(conn, "user", "in-b", conversation_id=b)
    # None mode: all messages (back-compat)
    allmsgs = db.recent_chat(conn, 10)
    assert [r["text"] for r in allmsgs] == ["in-a", "in-b"]
    # filtered by conversation
    assert [r["text"] for r in db.recent_chat(conn, 10, a)] == ["in-a"]
    assert [r["text"] for r in db.recent_chat(conn, 10, b)] == ["in-b"]


def test_delete_conversation_removes_its_messages(conn):
    cid = db.add_conversation(conn, "doomed")
    db.add_chat(conn, "user", "bye", conversation_id=cid)
    db.delete_conversation(conn, cid)
    assert db.list_conversations(conn) == []
    assert db.recent_chat(conn, 10, cid) == []

def test_export_import_roundtrip(conn, tmp_path):
    db.set_setting(conn, "salary_day", "10")
    db.add_goal(conn, name="G", type="purchase_fund", target_agorot=100)
    db.add_transaction(conn, effective_date=D, amount_agorot=-100,
                       direction="expense")
    cid = db.add_conversation(conn, "Roundtrip chat")
    db.add_chat(conn, "user", "carry me", conversation_id=cid)
    data = db.export_json(conn)
    c2 = db.connect(tmp_path / "restored.db")
    db.init_db(c2)
    db.import_json(c2, data)
    assert db.get_setting(c2, "salary_day") == "10"
    assert len(db.list_transactions(c2)) == 1
    assert len(db.list_goals(c2)) == 1
    convs = db.list_conversations(c2)
    assert len(convs) == 1 and convs[0]["title"] == "Roundtrip chat"
    assert [r["text"] for r in db.recent_chat(c2, 10, convs[0]["id"])] \
        == ["carry me"]

def test_daily_backup_written_once_and_pruned(conn, tmp_path):
    bdir = tmp_path / "backups"
    p1 = db.write_daily_backup(conn, bdir, D)
    p2 = db.write_daily_backup(conn, bdir, D)
    assert p1 is not None and p2 is None  # second call same day: skipped
    for i in range(35):  # fabricate old backups to test pruning
        (bdir / f"ledger-2026-04-{i:02d}.json").write_text("{}", encoding="utf-8")
    db.write_daily_backup(conn, bdir, D + dt.timedelta(days=1))
    assert len(list(bdir.glob("ledger-*.json"))) == 30


def test_import_rejects_non_backup(conn):
    db.set_setting(conn, "salary_day", "10")
    with pytest.raises(ValueError):
        db.import_json(conn, {})
    assert db.get_setting(conn, "salary_day") == "10"  # data intact


def test_import_rolls_back_on_malformed_row(conn):
    db.add_goal(conn, name="Keep", type="purchase_fund", target_agorot=100)
    data = db.export_json(conn)
    data["transactions"] = [{"bogus_col": 1}]
    with pytest.raises(Exception):
        db.import_json(conn, data)
    assert len(db.list_goals(conn)) == 1  # deletes rolled back


def test_add_goal_rejects_duplicate_active_name(conn):
    db.add_goal(conn, name="Drone", type="purchase_fund", target_agorot=100)
    with pytest.raises(ValueError):
        db.add_goal(conn, name="drone", type="purchase_fund", target_agorot=200)


def test_update_goal_empty_kw_is_noop(conn):
    gid = db.add_goal(conn, name="G", type="purchase_fund", target_agorot=100)
    db.update_goal(conn, gid)  # must not raise
