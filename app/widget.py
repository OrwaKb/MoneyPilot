"""MoneyPilot always-on widget — standalone process on the same ledger.

  pythonw -m app.widget          normal launch (real ledger, frameless, on-top)
  python  -m app.widget --dev    seeded dev ledger + DevTools, console visible
"""
from __future__ import annotations

import socket
import subprocess
import sys
import threading
from pathlib import Path

import webview

from app import db
from app.__main__ import (PROJECT_DIR, _suppress_child_consoles,
                          _try_focus_running, data_dir)
from app.api import Api

WIDGET_PORT_FILE = "widget.lock"


def _install_on_top_fix(gui=None) -> bool:
    """Make pywebview's always-on-top change safe to call off the GUI thread.

    pywebview 6.2.1's ``winforms.set_on_top`` flips the WinForms ``Form.TopMost``
    on whatever thread calls it (unlike its own ``set_title``/``show``, which
    marshal via ``Form.Invoke``). JS-API bridge calls run on a *background*
    thread — ``util.js_bridge_call`` spawns one per call — so when the 📌 button
    calls ``set_pin`` -> ``window.on_top = on``, ``Form.TopMost`` is poked
    cross-thread. That blocks on a synchronous ``SetWindowPos`` window message to
    the GUI thread and the widget freezes (it hangs, so ``set_pin``'s ``except``
    never fires). We replace ``set_on_top`` with a version that marshals onto the
    GUI thread when needed. Idempotent; a no-op off Windows. ``gui`` is injectable
    for tests; in production it resolves to ``webview.platforms.winforms``.
    """
    if gui is None:
        if sys.platform != "win32":
            return False
        try:
            from webview.platforms import winforms as gui
        except Exception:
            return False
    if getattr(gui, "_moneypilot_ontop_patched", False):
        return True

    def _set_on_top(uid, on_top):
        form = gui.BrowserView.instances.get(uid)
        if form is None:
            return
        def _apply():
            form.TopMost = on_top
        if getattr(form, "InvokeRequired", False):
            form.Invoke(gui.Func[gui.Type](_apply))   # hop to the GUI thread
        else:
            _apply()

    gui.set_on_top = _set_on_top
    gui._moneypilot_ontop_patched = True
    return True


class WidgetApi:
    """Thin widget-facing bridge: composes a real `Api` and exposes only the
    widget's surface. Delegated methods inherit `Api`'s `_safe` dict contract;
    `is_onboarded` is wrapped so every bridge call returns a dict."""

    def __init__(self, api: Api):
        self._api = api
        self._window = None      # set after the window is created
        self._ddir = None        # data dir, for open_main_app / geometry

    # --- delegated cockpit surface ------------------------------------------
    def get_overview(self):
        return self._api.get_overview()

    def add_entry(self, text: str):
        return self._api.add_entry(text)

    def undo_txn(self, txn_id):
        return self._api.undo_txn(int(txn_id))

    def is_onboarded(self):
        return {"ok": True, "onboarded": self._api.is_onboarded()}

    # --- widget-only surface -------------------------------------------------
    def get_pin(self):
        """The real persisted pin state, so the UI can paint 📌 from truth at
        boot instead of assuming "on" (widget.py honors this same setting when
        it creates the window; default pinned)."""
        try:
            on = db.get_setting(self._api.conn, "widget_on_top", "1") == "1"
            return {"ok": True, "on": on}
        except Exception:
            return {"ok": False}        # never throw across the bridge

    def set_pin(self, on):
        try:
            on = bool(on)
            if self._window is not None:
                self._window.on_top = on
            db.set_setting(self._api.conn, "widget_on_top", 1 if on else 0)
            return {"ok": True, "on": on}
        except Exception:
            return {"ok": False}        # never throw across the bridge

    def save_geometry(self):
        try:
            w = self._window
            if w is not None:
                db.set_setting(self._api.conn, "widget_x", int(w.x))
                db.set_setting(self._api.conn, "widget_y", int(w.y))
                db.set_setting(self._api.conn, "widget_on_top",
                               1 if w.on_top else 0)
            return {"ok": True}
        except Exception:
            return {"ok": False}        # geometry is a nicety; never crash

    def open_main_app(self):
        ddir = self._ddir or data_dir()
        if _try_focus_running(ddir):
            return {"ok": True, "focused": True}
        pyw = Path(sys.executable).with_name("pythonw.exe")
        exe = str(pyw) if pyw.exists() else sys.executable
        subprocess.Popen([exe, "-m", "app"], cwd=str(PROJECT_DIR))
        return {"ok": True, "focused": False}

    def close(self):
        if self._window is not None:
            self._window.destroy()
        return {"ok": True}


def _make_api(ddir: Path, dev: bool = False) -> Api:
    """Build the widget's `Api` on the per-user data dir — same ledger AND same
    backup dir as the main app (both under ``data_dir()``), never PROJECT_DIR.
    When frozen, PROJECT_DIR is the read-only bundle/extract dir, so backups
    written there silently fail (write_daily_backup swallows the OSError),
    leaving a widget-first user with no durable backups at all."""
    db_path = ddir / ("dev.db" if dev else "ledger.db")
    backup_dir = ddir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return Api(db_path, backup_dir=backup_dir)


def _try_focus_running_widget(ddir: Path) -> bool:
    """If a widget is already alive, raise it and return True; else False."""
    pf = ddir / WIDGET_PORT_FILE
    if not pf.exists():
        return False
    try:
        port = int(pf.read_text())
        with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
            s.sendall(b"FOCUS\n")
        return True
    except (OSError, ValueError):
        pf.unlink(missing_ok=True)        # stale lock
        return False


def _serve_singleton(ddir: Path, wapi: "WidgetApi") -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    (ddir / WIDGET_PORT_FILE).write_text(str(srv.getsockname()[1]))

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
                conn.recv(16)
                conn.close()
                if wapi._window:
                    wapi._window.restore()
                    wapi._window.show()
            except OSError:
                return

    threading.Thread(target=loop, daemon=True).start()


def main() -> None:
    import argparse
    import logging

    if sys.platform == "win32":
        _suppress_child_consoles()
        _install_on_top_fix()   # else tapping 📌 freezes the widget (see fn docstring)

    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true")
    args = ap.parse_args()

    ddir = data_dir()
    ddir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=str(ddir / "moneypilot.log"), encoding="utf-8",
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if _try_focus_running_widget(ddir):
        return

    api = _make_api(ddir, dev=args.dev)
    wapi = WidgetApi(api)
    wapi._ddir = ddir
    _serve_singleton(ddir, wapi)

    s = db.get_settings(api.conn)

    def _geo(key, default):
        try:
            return int(s[key])
        except (KeyError, TypeError, ValueError):
            return default

    on_top = _geo("widget_on_top", 1) == 1
    window = webview.create_window(
        "MoneyPilot Widget", str(PROJECT_DIR / "app" / "ui" / "widget.html"),
        js_api=wapi, width=300, height=300,
        x=_geo("widget_x", None) if "widget_x" in s else None,
        y=_geo("widget_y", None) if "widget_y" in s else None,
        frameless=True, easy_drag=False, on_top=on_top,
        background_color="#0d1117")
    wapi._window = window
    window.events.closing += lambda: wapi.save_geometry()
    webview.start(debug=args.dev)
    (ddir / WIDGET_PORT_FILE).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
