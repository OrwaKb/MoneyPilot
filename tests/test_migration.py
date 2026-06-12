import datetime as dt

from app import db

_V1_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS chat_history(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, role TEXT NOT NULL, text TEXT NOT NULL);
"""


def _build_v1(path):
    """A hand-rolled v1-shaped DB: chat_history WITHOUT conversation_id,
    schema_version stamped '1', and a couple of legacy chat rows."""
    c = db.connect(path)
    c.executescript(_V1_SCHEMA)
    c.execute("INSERT INTO meta(key, value) VALUES('schema_version', '1')")
    c.execute("INSERT INTO chat_history(ts, role, text) VALUES(?,?,?)",
              ("2026-06-01T09:00:00", "user", "first ever message"))
    c.execute("INSERT INTO chat_history(ts, role, text) VALUES(?,?,?)",
              ("2026-06-02T10:00:00", "assistant", "reply"))
    c.commit()
    return c


def test_migration_v1_to_v2(tmp_path):
    c = _build_v1(tmp_path / "legacy.db")
    db.init_db(c)  # runs schema + seed + stamp + _migrate

    # conversation_id column now exists on chat_history
    cols = {r["name"] for r in c.execute("PRAGMA table_info(chat_history)")}
    assert "conversation_id" in cols

    # exactly one legacy conversation, titled 'Earlier conversation',
    # created_at at the oldest message ts
    convs = list(c.execute("SELECT * FROM conversations"))
    assert len(convs) == 1
    assert convs[0]["title"] == "Earlier conversation"
    assert convs[0]["created_at"] == "2026-06-01T09:00:00"

    # every legacy row assigned to that conversation
    cid = convs[0]["id"]
    rows = list(c.execute("SELECT conversation_id FROM chat_history"))
    assert len(rows) == 2
    assert all(r["conversation_id"] == cid for r in rows)

    # version bumped
    v = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert v["value"] == "2"
    c.close()


def test_fresh_db_is_v2_no_legacy_conversation(conn):
    v = conn.execute(
        "SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert v["value"] == "2"
    # no chats existed → migration created no conversation
    assert list(conn.execute("SELECT * FROM conversations")) == []


def test_migration_idempotent(tmp_path):
    c = _build_v1(tmp_path / "legacy.db")
    db.init_db(c)
    db.init_db(c)  # second call must be a no-op for the migration
    convs = list(c.execute("SELECT * FROM conversations"))
    assert len(convs) == 1  # not duplicated
    c.close()


def test_migration_no_chats_no_conversation(tmp_path):
    """v1 DB with zero chat rows: column added, version bumped, no conversation."""
    c = db.connect(tmp_path / "empty.db")
    c.executescript(_V1_SCHEMA)
    c.execute("INSERT INTO meta(key, value) VALUES('schema_version', '1')")
    c.commit()
    db.init_db(c)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(chat_history)")}
    assert "conversation_id" in cols
    assert list(c.execute("SELECT * FROM conversations")) == []
    v = c.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    assert v["value"] == "2"
    c.close()
