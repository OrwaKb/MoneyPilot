# MoneyPilot widget — always-on floating cockpit (2026-06-13)

A small, frameless, always-on-top desktop widget that shows the day's
safe-to-spend at a glance and lets you capture a spend without opening the
full cockpit. It runs as its own lightweight process so it stays up even when
the main window is closed — the most literal reading of "always on display."

## Goals

- One-glance answer to "how much can I spend today?" — safe-to-spend is the hero.
- Capture-anywhere: type `45 falafel with karim` into the widget; same parse
  pipeline as the cockpit (AI → CLI → regex fallback; entries never lost).
- Always resident: own process, survives the cockpit being closed, optional
  start-at-login.
- Zero new business logic: reuse `Api` / the engine verbatim.

## Non-goals

- No new financial computation, schema change, or AI behavior — the widget is a
  thin second face on the existing engine.
- No editing, ledger browsing, goals management, or chat — those stay in the
  cockpit; the widget links to it.

## Data architecture

The widget is a **standalone process** that opens its own connection to the
same ledger (`%LOCALAPPDATA%\MoneyPilot\ledger.db`). It reads through the
existing `Api.get_overview()` and writes through `Api.add_entry()` — no IPC, no
duplicated logic. WAL (already set in `db.connect`) makes concurrent reads from
two processes safe.

**One supporting change to `db.connect`:** add `PRAGMA busy_timeout=5000`. WAL
permits one writer at a time; without a busy timeout a simultaneous write from
the second process fails immediately with `SQLITE_BUSY`. Five seconds lets the
loser wait for the winner to finish. This also hardens the main app.

Rejected alternatives: *(a)* widget talks to the running cockpit over a socket
— only works while the cockpit is open, defeating "always on display"; *(b)*
widget as a second window inside the cockpit process — dies when the cockpit
closes.

## Components

- **`app/widget.py`** — `python -m app.widget` (run windowless via
  `pythonw -m app.widget`). Mirrors `app/__main__.py`: data dir, single-instance
  guard, frameless always-on-top `webview.create_window`, geometry persistence.
- **`WidgetApi`** (in `app/widget.py`) — a thin bridge that *composes* a real
  `Api` and exposes only the widget's surface:
  - delegated: `get_overview()`, `add_entry(text)`, `undo_txn(id)`
  - widget-only: `open_main_app()`, `set_pin(on: bool)`, `save_geometry()`,
    `close()`
  Small, single-purpose, fully reusing the engine. Inherits `Api`'s `_safe`
  wrapper, so every method returns `{ok: …}` and never raises into the bridge.
- **`app/ui/widget.html` / `widget.css` / `widget.js`** — the widget's own tiny
  UI, sharing the cockpit palette (`:root` from `app.css`: bg `#0d1117`, panel
  `#141b26`, teal `#4ef0c0`, amber `#ffb46b`, line `#26334a`) and the existing
  SVG arc-gauge motif. Reuses `app/ui/assets/icon.ico`.

## Layout & visual identity

A mini cockpit gauge, ~300×300, dark `#0d1117`:

- **Title strip** (the drag handle): 🧭 wordmark, 📌 pin toggle, ✕ close.
- **Hero:** the safe-to-spend-today figure (`safe_to_spend.today_fmt`) large in
  the center of the SVG arc dial; the arc fills by cycle progress
  (`days_left` / cycle length) so it reads like the cockpit gauge.
- **Two quiet rows:** Balance (`balance.available_fmt`) and Card due
  (`card.total_fmt`, with `days_to_charge`).
- **Quick-add input** at the bottom, placeholder in the cockpit's voice
  (`45 falafel with karim`).

## Data flow & refresh

`widget.js` calls `get_overview()` on load, then polls every 30s, plus
immediately after a quick-add and on `window` focus. After `add_entry`
resolves: a brief confirmation chip (category emoji · description · amount) with
a 3-second **Undo** that calls `undo_txn`, mirroring the cockpit recent-ticker;
then the numbers refresh. All dynamic strings go through an `esc()` helper and
ids through `Number()` (same XSS-safety contract as `app.js`).

## Behaviors & edge cases

- **Single instance.** Own `widget.lock` socket file in the data dir, mirroring
  the cockpit's `port.lock` / `_serve_singleton` / `_try_focus_running`. A second
  launch tells the running widget to `restore()` + `show()` and exits.
- **Open the cockpit.** `open_main_app()` first tries to focus a running cockpit
  via its `port.lock` socket (`FOCUS`); if absent/stale, spawns
  `pythonw -m app` (console suppression already handled by the patched `Popen`).
- **Not onboarded.** If `is_onboarded()` is false (no `salary_day`), the widget
  shows a single "Set up MoneyPilot →" affordance that calls `open_main_app()`
  instead of empty numbers.
- **AI offline.** Quick-add still lands via the regex fallback; the
  confirmation chip carries a subtle offline hint when `used_ai` is false and
  `source` is the fallback — same contract the cockpit uses.
- **DB busy / refresh failure.** On `{ok: false}` from a poll, keep the
  last-known values on screen and show a small error dot; never blank or crash.

## Launch & distribution

`scripts/setup.ps1` gains a **"MoneyPilot Widget"** shortcut (target
`pythonw -m app.widget`, `IconLocation` = the same `icon.ico`). An **opt-in**
"start the widget at login" creates a shortcut in the user's Startup folder
(`shell:startup`) so the widget is present from boot. Opt-in via a `-Autostart`
switch (or an interactive yes/no prompt), defaulting to off.

## Testing

- **pytest (offline, AI mocked) — `tests/test_widget.py`:**
  - `WidgetApi` delegates `get_overview` / `add_entry` / `undo_txn` to the
    underlying `Api` and returns its payload unchanged.
  - geometry round-trips through settings (`widget_x`, `widget_y`,
    `widget_on_top`).
  - single-instance focus: a second launch hits the socket and raises the first,
    without opening a second window (logic tested headless, window mocked).
  - `open_main_app()` decision: focuses via `port.lock` when present, spawns
    `pythonw -m app` when absent (socket + `subprocess` mocked).
  - `busy_timeout` is set by `db.connect` (PRAGMA read-back).
- **Browser (house technique):** serve `app/ui/widget.html` over local HTTP,
  inject a mock `window.pywebview.api` with `get_overview` / `add_entry` /
  `undo_txn` shapes from `api.py`, dispatch `pywebviewready`, drive with
  Playwright, and screenshot the **normal**, **after-add (confirmation +
  undo)**, **offline**, and **not-onboarded** states. Screenshots are the
  visual approval gate sent to the user.

## Files touched

- new: `app/widget.py`, `app/ui/widget.html`, `app/ui/widget.css`,
  `app/ui/widget.js`, `tests/test_widget.py`
- edit: `app/db.py` (`busy_timeout`), `scripts/setup.ps1` (widget shortcut +
  opt-in autostart), `README.md` (one line on the widget)
