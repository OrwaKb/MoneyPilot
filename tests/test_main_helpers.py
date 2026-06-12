import datetime as dt

from app import db
from app.__main__ import data_dir
from scripts.dev_seed import seed


def test_data_dir_is_localappdata(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert data_dir() == tmp_path / "MoneyPilot"

def test_seed_populates(tmp_path):
    conn = db.connect(tmp_path / "dev.db")
    db.init_db(conn)
    seed(conn, today=dt.date(2026, 6, 11))
    assert db.get_setting(conn, "salary_day") == "10"
    assert len(db.list_transactions(conn)) > 30
    assert len(db.list_goals(conn)) == 2
