import datetime as dt

import pytest

from app import db
from app.api import Api
from app.widget import WidgetApi

TODAY = dt.date(2026, 6, 11)

GOOD_REPLY = (
    '[{"effective_date": "2026-06-11", "amount": 45, "currency": "ILS",'
    ' "direction": "expense", "category": "Food out", "description": "falafel",'
    ' "merchant": null, "people": null, "payment_method": "card",'
    ' "goal_name": null, "confidence": 0.95}]')


@pytest.fixture
def wapi(tmp_path):
    a = Api(tmp_path / "ledger.db", backup_dir=tmp_path / "backups",
            today_fn=lambda: TODAY)
    a.save_settings({"user_name": "Tester", "salary_day": "10",
                     "salary_amount_agorot": "900000", "card_charge_day": "2",
                     "opening_balance_agorot": "500000",
                     "opening_balance_date": "2026-06-01"})
    return WidgetApi(a)


def test_get_overview_delegates_unchanged(wapi):
    assert wapi.get_overview() == wapi._api.get_overview()


def test_add_entry_and_undo_delegate(wapi, monkeypatch):
    from app.ai import parser
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: GOOD_REPLY)
    res = wapi.add_entry("45 falafel")
    assert res["ok"] is True and len(res["entries"]) == 1
    tid = res["entries"][0]["id"]
    assert wapi.undo_txn(str(tid))["ok"] is True          # accepts a str id
    assert db.list_transactions(wapi._api.conn) == []      # soft-deleted


def test_is_onboarded_wrapped_in_dict(wapi, tmp_path):
    assert wapi.is_onboarded() == {"ok": True, "onboarded": True}
    # a fresh, un-onboarded ledger (no salary_day) reports False
    a2 = Api(tmp_path / "blank.db", backup_dir=tmp_path / "b2")
    assert WidgetApi(a2).is_onboarded() == {"ok": True, "onboarded": False}


class _FakeWindow:
    def __init__(self):
        self.x, self.y, self.width, self.height = 40, 60, 300, 300
        self.on_top = True
        self.restored = self.shown = self.destroyed = False
    def restore(self): self.restored = True
    def show(self): self.shown = True
    def destroy(self): self.destroyed = True


def test_geometry_round_trips_through_settings(wapi):
    wapi._window = _FakeWindow()
    wapi._window.x, wapi._window.y, wapi._window.on_top = 111, 222, False
    assert wapi.save_geometry()["ok"] is True
    assert db.get_setting(wapi._api.conn, "widget_x") == "111"
    assert db.get_setting(wapi._api.conn, "widget_y") == "222"
    assert db.get_setting(wapi._api.conn, "widget_on_top") == "0"


def test_set_pin_updates_window_and_setting(wapi):
    wapi._window = _FakeWindow()
    assert wapi.set_pin(False)["ok"] is True
    assert wapi._window.on_top is False
    assert db.get_setting(wapi._api.conn, "widget_on_top") == "0"


def test_open_main_app_focuses_running_cockpit(wapi, monkeypatch):
    import app.widget as w
    monkeypatch.setattr(w, "_try_focus_running", lambda ddir: True)
    called = {"popen": False}
    monkeypatch.setattr(w.subprocess, "Popen",
                        lambda *a, **k: called.__setitem__("popen", True))
    res = wapi.open_main_app()
    assert res == {"ok": True, "focused": True}
    assert called["popen"] is False          # focused existing, did not spawn


def test_open_main_app_spawns_when_cockpit_absent(wapi, monkeypatch):
    import app.widget as w
    monkeypatch.setattr(w, "_try_focus_running", lambda ddir: False)
    spawned = {}
    monkeypatch.setattr(w.subprocess, "Popen",
                        lambda args, **k: spawned.update(args=args))
    res = wapi.open_main_app()
    assert res == {"ok": True, "focused": False}
    assert spawned["args"][1:] == ["-m", "app"]   # launched the cockpit module


def test_second_launch_focuses_running_widget(monkeypatch, tmp_path):
    import app.widget as w
    sent = {}
    class _Sock:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def sendall(self, b): sent["b"] = b
    (tmp_path / w.WIDGET_PORT_FILE).write_text("54321")
    monkeypatch.setattr(w.socket, "create_connection", lambda *a, **k: _Sock())
    assert w._try_focus_running_widget(tmp_path) is True
    assert sent["b"] == b"FOCUS\n"


def test_no_lockfile_means_no_running_widget(tmp_path):
    import app.widget as w
    assert w._try_focus_running_widget(tmp_path) is False
