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


def test_widget_backs_up_under_data_dir_not_project_dir(tmp_path):
    # Regression: the widget built its Api with backup_dir=PROJECT_DIR/"backups".
    # PROJECT_DIR is the read-only bundle/extract dir when frozen, so a
    # widget-first user's daily backups silently failed (write_daily_backup
    # swallows the OSError). Both entrypoints must back up under the per-user
    # data dir, exactly like app.__main__ (ddir/"backups").
    import app.widget as w
    api = w._make_api(tmp_path)
    assert api.backup_dir == tmp_path / "backups"
    assert api.backup_dir != w.PROJECT_DIR / "backups"


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


def test_get_pin_reflects_persisted_state(wapi):
    # The UI used to hardcode the 📌 as "on" at boot, lying when the user had
    # previously unpinned (widget.py honors the persisted widget_on_top when it
    # creates the window). get_pin returns the same truth so the UI can match it.
    assert wapi.get_pin() == {"ok": True, "on": True}        # default: pinned
    db.set_setting(wapi._api.conn, "widget_on_top", 0)
    assert wapi.get_pin() == {"ok": True, "on": False}       # honors unpinned
    db.set_setting(wapi._api.conn, "widget_on_top", 1)
    assert wapi.get_pin() == {"ok": True, "on": True}


def test_set_pin_updates_window_and_setting(wapi):
    wapi._window = _FakeWindow()
    assert wapi.set_pin(False)["ok"] is True
    assert wapi._window.on_top is False
    assert db.get_setting(wapi._api.conn, "widget_on_top") == "0"


def test_set_pin_degrades_gracefully_on_error(wapi, monkeypatch):
    # a db/window failure must return {ok: False}, not throw across the bridge
    wapi._window = _FakeWindow()
    monkeypatch.setattr(db, "set_setting",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert wapi.set_pin(True) == {"ok": False}


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


# --- always-on-top ("stay on top" 📌) GUI-thread marshaling ----------------
# Regression: tapping the pin froze the widget. pywebview 6.2.1's
# winforms.set_on_top flips Form.TopMost on the *calling* thread, and JS-API
# bridge calls run on a background thread (util.js_bridge_call spawns one per
# call) -> cross-thread WinForms access blocks on a synchronous window message
# to the GUI thread and hangs. _install_on_top_fix marshals via Form.Invoke,
# exactly as pywebview's own set_title/show already do.

class _FakeForm:
    def __init__(self, invoke_required):
        self.InvokeRequired = invoke_required     # True == off the GUI thread
        self.TopMost = None
        self.invoked = 0
    def Invoke(self, delegate):                   # WinForms marshal onto GUI thread
        self.invoked += 1
        return delegate()


class _FakeGeneric:
    def __getitem__(self, item):
        return lambda fn: fn                       # CLR Func[Type](fn) -> the callable


def _fake_gui(form):
    import types
    return types.SimpleNamespace(
        BrowserView=types.SimpleNamespace(instances={"win-1": form}),
        Func=_FakeGeneric(), Type=object)


def test_on_top_marshals_to_gui_thread_when_off_thread():
    from app.widget import _install_on_top_fix
    form = _FakeForm(invoke_required=True)         # i.e. called from a bridge thread
    gui = _fake_gui(form)
    assert _install_on_top_fix(gui) is True
    gui.set_on_top("win-1", True)
    assert form.TopMost is True
    assert form.invoked == 1                        # went through Invoke -> no freeze


def test_on_top_sets_directly_when_already_on_gui_thread():
    from app.widget import _install_on_top_fix
    form = _FakeForm(invoke_required=False)
    gui = _fake_gui(form)
    _install_on_top_fix(gui)
    gui.set_on_top("win-1", False)
    assert form.TopMost is False
    assert form.invoked == 0                        # no needless marshaling


def test_on_top_fix_is_idempotent():
    from app.widget import _install_on_top_fix
    gui = _fake_gui(_FakeForm(invoke_required=True))
    assert _install_on_top_fix(gui) is True
    first = gui.set_on_top
    assert _install_on_top_fix(gui) is True
    assert gui.set_on_top is first                  # not double-wrapped


def test_on_top_missing_form_is_noop():
    from app.widget import _install_on_top_fix
    gui = _fake_gui(_FakeForm(invoke_required=True))
    _install_on_top_fix(gui)
    gui.set_on_top("nonexistent", True)             # must not raise
