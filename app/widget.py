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
    def set_pin(self, on):
        on = bool(on)
        if self._window is not None:
            self._window.on_top = on
        db.set_setting(self._api.conn, "widget_on_top", 1 if on else 0)
        return {"ok": True, "on": on}

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

    db_path = ddir / ("dev.db" if args.dev else "ledger.db")
    api = Api(db_path, backup_dir=PROJECT_DIR / "backups")
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
