# MoneyPilot Widget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an always-on, frameless, always-on-top desktop widget that shows today's safe-to-spend on an arc gauge and lets you capture a spend, running as its own process on the same local ledger.

**Architecture:** A standalone `pythonw -m app.widget` process opens its own `sqlite` connection to the same `ledger.db` (WAL + a new `busy_timeout` makes two-process access safe) and reuses the existing `Api`/engine verbatim through a thin `WidgetApi` bridge. The widget never touches the web server. Its UI (`widget.html/css/js`) shares the cockpit palette and gauge motif and talks to `WidgetApi` over the pywebview js bridge.

**Tech Stack:** Python 3.11, pywebview, sqlite (WAL), vanilla JS/CSS/SVG, pytest, Playwright (visual gate).

**Spec:** `docs/superpowers/specs/2026-06-13-moneypilot-widget-design.md`

**Branch:** `feat/moneypilot-widget` (already rebased onto `master`; has the web build + onboarding validation fix).

---

## File Structure

- **`app/db.py`** (modify) — add `PRAGMA busy_timeout=5000` to `connect()`; hardens the cockpit and lets the second process wait for a writer instead of failing `SQLITE_BUSY`.
- **`app/widget.py`** (create) — entry point + `WidgetApi` bridge + widget-only singleton (`widget.lock`) and `open_main_app` launcher. Mirrors `app/__main__.py`.
- **`app/ui/widget.html`** (create) — the widget markup: drag-handle title strip, SVG arc gauge + hero, two rows, quick-add, confirmation chip, error dot, not-onboarded affordance.
- **`app/ui/widget.css`** (create) — ~300×300 styling reusing the cockpit `:root` palette and gauge look.
- **`app/ui/widget.js`** (create) — load/poll/refresh, quick-add + undo chip, pin/close/open-cockpit, not-onboarded + offline + error states. `esc()`/`Number()` safety contract.
- **`tests/test_widget.py`** (create) — offline pytest: delegation, geometry round-trip, single-instance focus, `open_main_app` decision, `busy_timeout` read-back.
- **`scripts/setup.ps1`** (modify) — "MoneyPilot Widget" shortcut + opt-in `-Autostart`.
- **`README.md`** (modify) — one line documenting the widget.

---

### Task 1: `db.connect` busy_timeout

**Files:**
- Modify: `app/db.py:63-72` (`connect`)
- Test: `tests/test_db.py`

- [ ] **Step 1: Write the failing test** (append to `tests/test_db.py`)

```python
def test_connect_sets_busy_timeout(tmp_path):
    from app import db
    conn = db.connect(tmp_path / "x.db")
    # 5000 ms lets a second process wait for the WAL writer instead of
    # failing SQLITE_BUSY immediately — required for the widget process.
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py::test_connect_sets_busy_timeout -v`
Expected: FAIL — `assert 0 == 5000` (default busy_timeout is 0).

- [ ] **Step 3: Implement** — add one line in `connect()` after `journal_mode=WAL`:

```python
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")   # wait for WAL writer (2-process)
    conn.execute("PRAGMA foreign_keys=ON")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_db.py -q`
Expected: PASS, no other regressions.

- [ ] **Step 5: Commit**

```bash
git add app/db.py tests/test_db.py
git commit -m "feat(widget): busy_timeout=5000 so a 2nd process waits for the WAL writer"
```

---

### Task 2: `WidgetApi` bridge

`WidgetApi` composes a real `Api` and exposes only the widget's surface. The cockpit `Api` methods it delegates to are already `@_safe` (return `{ok: …}` dicts, never raise); `is_onboarded()` returns a bare bool, so the widget wraps it in a dict.

**Files:**
- Create: `app/widget.py` (the `WidgetApi` class first; entry point added in Task 3)
- Test: `tests/test_widget.py`

- [ ] **Step 1: Write the failing tests** (create `tests/test_widget.py`)

```python
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
    assert wapi.undo_txn(str(tid))["ok"] is True          # accepts str id
    assert db.list_transactions(wapi._api.conn) == []      # soft-deleted


def test_is_onboarded_wrapped_in_dict(wapi, tmp_path):
    assert wapi.is_onboarded() == {"ok": True, "onboarded": True}
    # a fresh, un-onboarded ledger (no salary_day) reports False
    a2 = Api(tmp_path / "blank.db", backup_dir=tmp_path / "b2")
    assert WidgetApi(a2).is_onboarded() == {"ok": True, "onboarded": False}
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_widget.py -v`
Expected: FAIL at import — `ModuleNotFoundError: No module named 'app.widget'`.

- [ ] **Step 3: Implement `WidgetApi`** (create `app/widget.py` with just this for now)

```python
"""MoneyPilot always-on widget — standalone process on the same ledger.

  pythonw -m app.widget          normal launch (real ledger, frameless, on-top)
  python  -m app.widget --dev    seeded dev ledger + DevTools, console visible
"""
from __future__ import annotations

from app.api import Api


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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_widget.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add app/widget.py tests/test_widget.py
git commit -m "feat(widget): WidgetApi bridge composing Api (overview/add/undo/onboarded)"
```

---

### Task 3: Widget entry point — geometry, single instance, open-cockpit

Adds the runnable process around `WidgetApi`, mirroring `app/__main__.py`. Reuses `data_dir`, `_suppress_child_consoles`, `_try_focus_running` (the cockpit's `port.lock`), and `PROJECT_DIR` from `app.__main__`. The widget gets its **own** lock file `widget.lock` so it doesn't collide with the cockpit.

**Files:**
- Modify: `app/widget.py` (add geometry/singleton/launcher methods + `main()`)
- Test: `tests/test_widget.py`

- [ ] **Step 1: Write the failing tests** (append to `tests/test_widget.py`)

```python
class _FakeWindow:
    def __init__(self):
        self.x, self.y, self.width, self.height = 40, 60, 300, 300
        self.on_top = True
        self.restored = self.shown = False
    def restore(self): self.restored = True
    def show(self): self.shown = True


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
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_widget.py -k "geometry or set_pin or open_main_app" -v`
Expected: FAIL — `AttributeError: 'WidgetApi' object has no attribute 'save_geometry'`.

- [ ] **Step 3: Implement** — extend `app/widget.py`:

Add imports at top (below `from app.api import Api`):

```python
import socket
import subprocess
import sys
import threading
from pathlib import Path

import webview

from app import db
from app.__main__ import (PROJECT_DIR, _suppress_child_consoles, data_dir,
                          _try_focus_running)

WIDGET_PORT_FILE = "widget.lock"
```

Add these methods to `WidgetApi` (after `is_onboarded`):

```python
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
```

Also add this module-level helper (used by `main()` and unit-tested above):

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_widget.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Add the runnable `main()`** (append to `app/widget.py`; `main()`/`_serve_singleton` not unit-tested — exercised in Task 5 / manual launch)

```python
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
```

- [ ] **Step 6: Smoke-import and commit**

Run: `.venv/Scripts/python.exe -c "import app.widget; print('import ok')"`
Expected: `import ok`

```bash
git add app/widget.py tests/test_widget.py
git commit -m "feat(widget): entry point — frameless on-top window, geometry, single instance, open cockpit"
```

---

### Task 4: Widget UI (html / css / js)

A ~300×300 mini-cockpit. All dynamic strings go through `esc()`; ids through `Number()` (same XSS contract as `app.js`).

**Files:**
- Create: `app/ui/widget.html`, `app/ui/widget.css`, `app/ui/widget.js`

- [ ] **Step 1: Create `app/ui/widget.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoneyPilot Widget</title>
<link rel="stylesheet" href="widget.css">
</head>
<body>
  <div id="w-titlebar" class="pywebview-drag-region">
    <span class="w-brand">🧭 MONEYPILOT</span>
    <span class="w-titleactions">
      <button id="w-pin" title="keep on top" class="w-iconbtn">📌</button>
      <button id="w-close" title="close" class="w-iconbtn">✕</button>
    </span>
  </div>

  <div id="w-setup" class="hidden">
    <button id="w-setup-btn" class="w-setup-btn">Set up MoneyPilot →</button>
  </div>

  <div id="w-main">
    <div class="w-dialwrap">
      <svg id="w-dial" viewBox="0 0 200 132" aria-hidden="true">
        <defs>
          <linearGradient id="wGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stop-color="#4ef0c0"/>
            <stop offset="1" stop-color="#2da8ff"/>
          </linearGradient>
        </defs>
        <path class="w-track" d="M 44.57 118 A 64 64 0 1 1 155.43 118"/>
        <path id="w-gauge" class="w-fill" pathLength="100"
              d="M 44.57 118 A 64 64 0 1 1 155.43 118"/>
        <text id="w-day" class="w-day" x="100" y="120">DAY —</text>
      </svg>
      <div class="w-hero-wrap">
        <div class="w-hero-cap">SAFE TODAY</div>
        <div id="w-hero" class="w-hero">—</div>
      </div>
    </div>

    <div class="w-rows">
      <div class="w-row"><span class="w-row-cap">BALANCE</span>
        <span id="w-balance" class="w-row-val">—</span></div>
      <div class="w-row"><span class="w-row-cap">CARD DUE</span>
        <span id="w-card" class="w-row-val">—</span></div>
    </div>

    <div id="w-chip" class="w-chip hidden"></div>
    <input id="w-add" class="w-add" autocomplete="off"
           placeholder="45 falafel with karim">
  </div>

  <span id="w-err" class="w-err hidden" title="couldn't refresh">●</span>
  <script src="widget.js"></script>
</body>
</html>
```

- [ ] **Step 2: Create `app/ui/widget.css`**

```css
:root {
  --bg: #0d1117; --panel: #141b26; --line: #26334a;
  --txt: #c7d6ea; --dim: #5b7290; --dim2: #8aa3c2;
  --accent: #4ef0c0; --blue: #2da8ff; --amber: #ffb46b; --red: #ff6b7a;
  --mono: "Cascadia Mono", "Cascadia Code", Consolas, monospace;
  --accent-glow: rgba(78, 240, 192, .45);
}
* { box-sizing: border-box; margin: 0; }
.hidden { display: none !important; }
html, body { height: 100%; }
body { background: var(--bg); color: var(--txt); font-family: var(--mono);
  font-size: 13px; font-variant-numeric: tabular-nums; user-select: none;
  display: flex; flex-direction: column; overflow: hidden;
  border: 1px solid var(--line); border-radius: 10px; }

#w-titlebar { display: flex; justify-content: space-between; align-items: center;
  padding: 7px 10px; cursor: move; border-bottom: 1px solid var(--line); }
.w-brand { color: var(--accent); letter-spacing: 1.5px; font-size: 11px;
  text-shadow: 0 0 12px var(--accent-glow); }
.w-titleactions { display: flex; gap: 4px; }
.w-iconbtn { background: none; border: none; color: var(--dim2); cursor: pointer;
  font: inherit; font-size: 12px; padding: 2px 4px; border-radius: 5px; }
.w-iconbtn:hover { color: var(--txt); background: #1c2638; }
#w-pin.on { color: var(--accent); }

#w-main { flex: 1; display: flex; flex-direction: column; padding: 6px 12px 12px; }
.w-dialwrap { position: relative; display: flex; justify-content: center; }
#w-dial { width: 200px; overflow: visible; }
.w-track { fill: none; stroke: #1b2433; stroke-width: 9; stroke-linecap: round; }
.w-fill { fill: none; stroke: url(#wGrad); stroke-width: 9; stroke-linecap: round;
  stroke-dasharray: 100; stroke-dashoffset: 100;
  filter: drop-shadow(0 0 5px var(--accent-glow));
  transition: stroke-dashoffset .9s cubic-bezier(.25,.8,.3,1); }
.w-day { fill: var(--dim); font-size: 11px; letter-spacing: 1.5px;
  text-anchor: middle; font-family: var(--mono); }
.w-hero-wrap { position: absolute; top: 46px; left: 0; right: 0; text-align: center; }
.w-hero-cap { color: var(--dim); font-size: 9px; letter-spacing: 1.8px; }
.w-hero { color: var(--accent); font-size: 30px; font-weight: bold;
  text-shadow: 0 0 22px var(--accent-glow); line-height: 1.1; }

.w-rows { display: flex; gap: 8px; margin: 2px 0 8px; }
.w-row { flex: 1; background: var(--panel); border: 1px solid var(--line);
  border-radius: 7px; padding: 6px 9px; }
.w-row-cap { display: block; color: var(--dim); font-size: 9px; letter-spacing: 1.3px; }
.w-row-val { color: var(--txt); font-size: 14px; font-weight: bold; }

.w-add { background: var(--panel); color: var(--txt); border: 1px dashed #3b4a61;
  border-radius: 7px; padding: 8px 10px; font: inherit; outline: none;
  transition: border-color .15s; }
.w-add::placeholder { color: #46587290; }
.w-add:focus { border-color: var(--accent); border-style: solid;
  box-shadow: 0 0 14px -7px var(--accent-glow); }

.w-chip { display: flex; align-items: center; gap: 8px; font-size: 11px;
  background: var(--panel); border: 1px solid #2a5c4d; color: var(--accent);
  border-radius: 99px; padding: 4px 10px; margin-bottom: 6px;
  animation: chipIn .18s ease-out; }
@keyframes chipIn { from { opacity: 0; transform: translateY(4px); } }
.w-chip.offline { border-color: #5c482a; color: var(--amber); }
.w-chip button { background: none; border: none; color: var(--dim2);
  cursor: pointer; font: inherit; font-size: 11px; margin-left: auto; }
.w-chip button:hover { color: var(--txt); }

#w-setup { flex: 1; display: flex; align-items: center; justify-content: center; }
.w-setup-btn { background: var(--panel); color: var(--accent);
  border: 1px solid #2a5c4d; border-radius: 8px; padding: 12px 18px;
  cursor: pointer; font: inherit; }

.w-err { position: fixed; bottom: 6px; right: 8px; color: var(--red);
  font-size: 9px; opacity: .85; }
```

- [ ] **Step 3: Create `app/ui/widget.js`**

```js
const $ = (s) => document.querySelector(s);

const ready = new Promise((resolve) => {
  if (window.pywebview && window.pywebview.api) resolve();
  else window.addEventListener("pywebviewready", resolve);
});
function api(method, ...args) {
  return ready.then(() => window.pywebview.api[method](...args));
}

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;",
              "'": "&#39;" }[c]));
}

let pinned = true;

function showSetup() {
  $("#w-setup").classList.remove("hidden");
  $("#w-main").classList.add("hidden");
}
function hideSetup() {
  $("#w-setup").classList.add("hidden");
  $("#w-main").classList.remove("hidden");
}
function setError(on) { $("#w-err").classList.toggle("hidden", !on); }

function render(ov) {
  const sts = ov.safe_to_spend, cyc = ov.cycle, card = ov.card, bal = ov.balance;
  $("#w-hero").textContent = sts.today_fmt;
  // arc fills with cycle progress: (length - days_left) / length
  const frac = cyc.length
    ? Math.min(1, Math.max(0, (cyc.length - sts.days_left) / cyc.length)) : 0;
  $("#w-gauge").style.strokeDashoffset = String(100 - frac * 100);
  $("#w-day").textContent = "DAY " + (cyc.day_index ?? "—");
  $("#w-balance").textContent = bal.available_fmt;
  $("#w-card").textContent = card.total_fmt + " · " + card.days_to_charge + "d";
}

async function refresh() {
  const ob = await api("is_onboarded");
  if (ob && ob.onboarded === false) { showSetup(); return; }
  const ov = await api("get_overview");
  if (!ov || ov.ok === false) { setError(true); return; }  // keep last-known
  setError(false);
  hideSetup();
  render(ov);
}

function chip(entry, offline) {
  const box = $("#w-chip");
  box.innerHTML = "";
  box.classList.toggle("offline", !!offline);
  const span = document.createElement("span");
  const tail = offline ? " · offline" : "";
  span.textContent = `${entry.category_emoji || "•"} `
    + `${entry.description || entry.category_name || ""} `
    + `${entry.amount_fmt}${tail}`;
  const undo = document.createElement("button");
  undo.textContent = "undo";
  undo.onclick = async () => {
    await api("undo_txn", Number(entry.id));
    box.classList.add("hidden");
    refresh();
  };
  box.appendChild(span);
  box.appendChild(undo);
  box.classList.remove("hidden");
  clearTimeout(chip._h);
  chip._h = setTimeout(() => box.classList.add("hidden"), 3000);
}

async function quickAdd() {
  const inp = $("#w-add");
  const text = inp.value.trim();
  if (!text) return;
  inp.value = "";
  const res = await api("add_entry", text);
  if (!res || res.ok === false || !res.entries || !res.entries.length) {
    setError(true); return;
  }
  chip(res.entries[0], res.used_ai === false);
  refresh();
}

$("#w-add").addEventListener("keydown",
  (e) => { if (e.key === "Enter") quickAdd(); });
$("#w-close").addEventListener("click", () => api("close"));
$("#w-setup-btn").addEventListener("click", () => api("open_main_app"));
$(".w-brand").addEventListener("dblclick", () => api("open_main_app"));
$("#w-pin").addEventListener("click", async () => {
  pinned = !pinned;
  $("#w-pin").classList.toggle("on", pinned);
  await api("set_pin", pinned);
});

ready.then(() => {
  $("#w-pin").classList.add("on");          // starts pinned
  refresh();
  setInterval(refresh, 30000);              // poll every 30s
  window.addEventListener("focus", refresh);
});
```

- [ ] **Step 4: Lint the JS**

Run: `node --check app/ui/widget.js`
Expected: no output (valid).

- [ ] **Step 5: Commit**

```bash
git add app/ui/widget.html app/ui/widget.css app/ui/widget.js
git commit -m "feat(widget): mini-cockpit UI — arc gauge, rows, quick-add + undo chip, states"
```

---

### Task 5: Visual gate — Playwright screenshots of widget states

Serve `app/ui/` over local HTTP, inject a mock `window.pywebview.api`, dispatch `pywebviewready`, and screenshot the four states. Screenshots are the approval gate sent to the user (no assertion file — visual review).

**Files:**
- Create (throwaway): a tiny harness page or inline `browser_evaluate` mock; no repo file required.

- [ ] **Step 1: Start a static server for the UI dir**

Run (background): `.venv/Scripts/python.exe -m http.server 8765 --directory app/ui`

- [ ] **Step 2: For each state, navigate + inject mock + screenshot** (Playwright MCP)

Mock payload shapes come from `app/api.py` (`get_overview`, `add_entry`) — inject before `pywebviewready`:

```js
// NORMAL
window.pywebview = { api: {
  is_onboarded: async () => ({ ok: true, onboarded: true }),
  get_overview: async () => ({
    ok: true,
    safe_to_spend: { today_fmt: "₪142", today_agorot: 14200, days_left: 12 },
    cycle: { length: 30, days_left: 12, day_index: 18 },
    card: { total_fmt: "₪1,240", days_to_charge: 5 },
    balance: { available_fmt: "₪3,820" } }),
  add_entry: async () => ({ ok: true, used_ai: true, source: "ai",
    entries: [{ id: 1, category_emoji: "🥙", category_name: "Food out",
                description: "falafel with karim", amount_fmt: "₪45" }] }),
  undo_txn: async () => ({ ok: true }),
  set_pin: async () => ({ ok: true }), open_main_app: async () => ({ ok: true }),
  close: async () => ({ ok: true }),
} };
window.dispatchEvent(new Event("pywebviewready"));
```

States to capture (resize viewport to 300×300 first):
  1. **normal** — inject above, screenshot.
  2. **after-add** — type into `#w-add`, press Enter → confirmation chip + undo visible, screenshot.
  3. **offline** — same as normal but `add_entry` returns `used_ai:false, source:"fallback"`; do a quick-add → amber "· offline" chip, screenshot.
  4. **not-onboarded** — `is_onboarded` returns `{ok:true, onboarded:false}` → "Set up MoneyPilot →", screenshot.

- [ ] **Step 3: Stop the static server.** Deliver the four screenshots to the user as the visual approval gate. Do not proceed to Task 6 until approved.

---

### Task 6: Launch & distribution — setup.ps1 shortcut + opt-in autostart, README

**Files:**
- Modify: `scripts/setup.ps1`
- Modify: `README.md`

- [ ] **Step 1: Add the widget shortcut + opt-in autostart to `scripts/setup.ps1`**

Add a `-Autostart` switch at the top (line 3 area) and, after the existing `$lnk.Save()` (line 19), append:

```powershell
# --- MoneyPilot Widget shortcut (always-on floating gauge) ---
$wlnk = $ws.CreateShortcut("$desktop\MoneyPilot Widget.lnk")
$wlnk.TargetPath = "$root\.venv\Scripts\pythonw.exe"
$wlnk.Arguments = "-m app.widget"
$wlnk.WorkingDirectory = $root
if (Test-Path $icon) { $wlnk.IconLocation = $icon }
$wlnk.Save()

# Opt-in: start the widget at login (Startup folder). Off unless -Autostart.
if ($Autostart) {
    $startup = [Environment]::GetFolderPath("Startup")
    $slnk = $ws.CreateShortcut("$startup\MoneyPilot Widget.lnk")
    $slnk.TargetPath = "$root\.venv\Scripts\pythonw.exe"
    $slnk.Arguments = "-m app.widget"
    $slnk.WorkingDirectory = $root
    if (Test-Path $icon) { $slnk.IconLocation = $icon }
    $slnk.Save()
    Write-Host "Widget will start at login." -ForegroundColor Green
}
```

And change line 3-4 to declare the param (param block must be the first statement):

```powershell
param([switch]$Autostart)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
```

- [ ] **Step 2: Verify the script parses**

Run: `powershell -NoProfile -Command "$null = [ScriptBlock]::Create((Get-Content -Raw scripts/setup.ps1)); 'parse ok'"`
Expected: `parse ok`

- [ ] **Step 3: Add one README line** (under the run/usage section)

```markdown
- **Widget:** `pythonw -m app.widget` launches an always-on floating gauge with
  safe-to-spend + quick-add. `scripts\setup.ps1 -Autostart` also starts it at login.
```

- [ ] **Step 4: Commit**

```bash
git add scripts/setup.ps1 README.md
git commit -m "feat(widget): desktop + opt-in startup shortcuts; README note"
```

---

## Final verification

- [ ] `.venv/Scripts/python.exe -m pytest -q` — all green (existing + new widget tests).
- [ ] `node --check app/ui/widget.js` — valid.
- [ ] Manual smoke (user machine): `pythonw -m app.widget` shows the gauge; quick-add lands a spend with an undo chip; pin/close/open-cockpit work; widget survives the cockpit closing.
- [ ] Screenshots (Task 5) approved by the user.
