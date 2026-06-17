"""MoneyPilot entry point.

  pythonw -m app             normal launch (real ledger in %LOCALAPPDATA%)
  python -m app --dev        seeded throwaway ledger + DevTools, console visible
  python -m app --restore F  restore ledger from a backup JSON, then exit
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import sys
import threading
from pathlib import Path

import webview

from app import db
from app.api import Api
from app.paths import PROJECT_DIR, data_dir, resource_path

SINGLETON_PORT_FILE = "port.lock"


def _suppress_child_consoles() -> None:
    """Windowless parents (pythonw) get a visible console for every console
    child the Claude Agent SDK spawns. Inject CREATE_NO_WINDOW into every
    Popen unless the caller asked for a console explicitly."""
    import subprocess
    flag = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    visible = (getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    orig = subprocess.Popen.__init__

    def patched(self, *args, **kwargs):
        if not kwargs.get("creationflags", 0) & visible:
            kwargs["creationflags"] = kwargs.get("creationflags", 0) | flag
        orig(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched


def _selftest() -> int:
    """Headless smoke check for a packaged build: prove the bundle-critical
    pieces resolve before we hand the .exe to anyone. Prints OK/FAIL, no GUI."""
    from app.ai import client
    ok = True

    def check(label, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"  [{'OK ' if cond else 'FAIL'}] {label}: {detail}")

    idx = resource_path("app", "ui", "index.html")
    check("UI index.html", idx.exists(), str(idx))
    icon = resource_path("app", "ui", "assets", "icon.ico")
    check("icon", icon.exists(), str(icon))
    ddir = data_dir()
    try:
        ddir.mkdir(parents=True, exist_ok=True)
        probe = ddir / ".selftest"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        check("data dir writable", True, str(ddir))
    except OSError as e:
        check("data dir writable", False, f"{ddir}: {e}")
    claude = client.bundled_claude_exe()
    check("bundled Claude runtime (auth)", bool(claude), claude or "NOT FOUND")
    # Authoritative check that the AI transport will find its binary frozen:
    # resolve it exactly the way the SDK's own _find_bundled_cli does.
    try:
        from claude_agent_sdk._internal.transport import subprocess_cli as sc
        name = "claude.exe" if os.name == "nt" else "claude"
        sdk_cli = (Path(sc.__file__).parent.parent.parent / "_bundled" / name)
        check("SDK resolves its CLI (AI)", sdk_cli.exists(), str(sdk_cli))
    except Exception as e:  # noqa: BLE001
        check("SDK resolves its CLI (AI)", False, repr(e))
    print("SELFTEST", "OK" if ok else "FAILED")
    return 0 if ok else 1


def _selftest_ai() -> int:
    """Dev-only: prove the AI actually runs end-to-end from THIS (possibly
    frozen) build — finds + launches the bundled Claude and gets a reply.
    Requires a Claude login on this machine; not part of the friend verdict."""
    from app.ai import client
    try:
        out = client.ask_claude("Reply with exactly the word: pong", timeout_s=60)
    except Exception as e:  # noqa: BLE001
        print(f"  [FAIL] ask_claude raised: {e!r}")
        return 1
    ok = bool(out and out.strip())
    print(f"  [{'OK ' if ok else 'FAIL'}] ask_claude reply: {out[:80]!r}")
    print("SELFTEST-AI", "OK" if ok else "FAILED")
    return 0 if ok else 1


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
    if sys.platform == "win32":
        _suppress_child_consoles()

    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true")
    ap.add_argument("--restore", metavar="BACKUP_JSON")
    ap.add_argument("--selftest", action="store_true",
                    help="headless packaging smoke check, then exit")
    ap.add_argument("--selftest-ai", action="store_true",
                    help="dev: prove a real AI call works from this build, then exit")
    args = ap.parse_args()

    if args.selftest:
        sys.exit(_selftest())
    if args.selftest_ai:
        sys.exit(_selftest_ai())

    ddir = data_dir()
    ddir.mkdir(parents=True, exist_ok=True)

    # The GUI runs under pythonw (no console), so route warnings to a file —
    # this is where an "Advisor offline" cause is recorded for diagnosis.
    logging.basicConfig(
        filename=str(ddir / "moneypilot.log"), encoding="utf-8",
        level=logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

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

    backup_dir = ddir / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    api = Api(db_path, backup_dir=backup_dir)
    _serve_singleton(ddir, api)

    # Phone (Pocket) sync listener — bound to localhost, alive only while this
    # window is open. `tailscale serve` is what exposes it to the user's tailnet.
    sync_httpd = None
    try:
        from app import pocket, sync_server
        sync_httpd = sync_server.start(api, pocket.get_token(api.conn))
    except OSError as e:  # port busy / unavailable — phone sync just won't work
        logging.getLogger("moneypilot.sync").warning(
            "Pocket sync listener not started: %s", e)

    s = db.get_settings(api.conn)

    def _geo(key, default):
        v = s.get(key)
        try:
            return int(v)
        except (TypeError, ValueError):
            return default

    window = webview.create_window(
        "MoneyPilot", str(resource_path("app", "ui", "index.html")),
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
    if sync_httpd:
        sync_httpd.shutdown()
    (ddir / SINGLETON_PORT_FILE).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
