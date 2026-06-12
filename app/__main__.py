"""MoneyPilot entry point.

  pythonw -m app             normal launch (real ledger in %LOCALAPPDATA%)
  python -m app --dev        seeded throwaway ledger + DevTools, console visible
  python -m app --restore F  restore ledger from a backup JSON, then exit
"""
from __future__ import annotations

import argparse
import json
import socket
import threading
from pathlib import Path

import webview

from app import db
from app.api import Api

PROJECT_DIR = Path(__file__).resolve().parent.parent
SINGLETON_PORT_FILE = "port.lock"


def data_dir() -> Path:
    import os
    return Path(os.environ["LOCALAPPDATA"]) / "MoneyPilot"


def _try_focus_running(ddir: Path) -> bool:
    """If another instance is alive, ask it to focus itself and return True."""
    pf = ddir / SINGLETON_PORT_FILE
    if not pf.exists():
        return False
    try:
        port = int(pf.read_text())
        with socket.create_connection(("127.0.0.1", port), timeout=1) as s:
            s.sendall(b"FOCUS\n")
        return True
    except (OSError, ValueError):
        pf.unlink(missing_ok=True)  # stale lock
        return False


def _serve_singleton(ddir: Path, api: Api) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    (ddir / SINGLETON_PORT_FILE).write_text(str(srv.getsockname()[1]))

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
                conn.recv(16)
                conn.close()
                if api._window:
                    api._window.restore()
                    api._window.show()
            except OSError:
                return

    threading.Thread(target=loop, daemon=True).start()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true")
    ap.add_argument("--restore", metavar="BACKUP_JSON")
    args = ap.parse_args()

    ddir = data_dir()
    ddir.mkdir(parents=True, exist_ok=True)

    if args.restore:
        conn = db.connect(ddir / "ledger.db")
        db.init_db(conn)
        db.import_json(conn, json.loads(Path(args.restore).read_text(
            encoding="utf-8")))
        print(f"restored from {args.restore}")
        return

    if _try_focus_running(ddir):
        return  # another instance took focus

    if args.dev:
        db_path = ddir / "dev.db"
        if not db_path.exists():
            import datetime as dt
            from scripts.dev_seed import seed
            c = db.connect(db_path)
            db.init_db(c)
            seed(c, dt.date.today())
            c.close()
    else:
        db_path = ddir / "ledger.db"

    api = Api(db_path, backup_dir=PROJECT_DIR / "backups")
    _serve_singleton(ddir, api)

    s = db.get_settings(api.conn)

    def _geo(key, default):
        v = s.get(key)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    window = webview.create_window(
        "MoneyPilot", str(PROJECT_DIR / "app" / "ui" / "index.html"),
        js_api=api, width=_geo("window_w", 1180), height=_geo("window_h", 760),
        x=_geo("window_x", None) if "window_x" in s else None,
        y=_geo("window_y", None) if "window_y" in s else None,
        min_size=(960, 640), background_color="#0d1117")
    api._window = window

    def _save_geometry():
        try:
            for k, v in (("window_w", window.width), ("window_h", window.height),
                         ("window_x", window.x), ("window_y", window.y)):
                db.set_setting(api.conn, k, int(v))
        except Exception:
            pass  # geometry is a nicety; never block shutdown

    window.events.closing += _save_geometry
    webview.start(debug=args.dev)
    (ddir / SINGLETON_PORT_FILE).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
