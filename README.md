# MoneyPilot

Local personal-finance cockpit. Natural-language entry → Claude categorizes →
deterministic budgets, cycles, goals, and an AI advisor. Data never leaves this
PC except compact context sent to Claude via your subscription.

## Setup (once)
    powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
Then double-click **MoneyPilot** on the Desktop.

## Daily use
Type into the entry bar: `45 falafel with karim` · `salary landed` ·
`put 500 in drone fund`. Everything else is tabs.

## Widget
`pythonw -m app.widget` floats an always-on-top gauge with today's safe-to-spend
and a quick-add box; it runs as its own process on the same ledger, so it stays
up even when the cockpit is closed. `setup.ps1` adds a **MoneyPilot Widget**
shortcut; pass `-Autostart` to also launch it at login.

## Dev
    .venv\Scripts\python -m pytest          # all tests, offline, AI mocked
    .venv\Scripts\python -m app --dev       # seeded fake ledger + DevTools
    .venv\Scripts\python -m app --restore backups\ledger-YYYY-MM-DD.json

## Send a friend the app (.exe)

Build a standalone Windows app a friend downloads and runs on their own PC —
their data lives on their machine, no tunnel, no PC-of-yours-staying-on.

    powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1

Produces `dist\MoneyPilot-windows.zip` (~300 MB — it bundles the Claude
runtime). Send that; the friend unzips and runs `MoneyPilot.exe`. Smoke-test
the build headlessly with `dist\MoneyPilot\MoneyPilot.exe --selftest`.

The whole tracker works offline. The **AI** (advisor, briefing, auto-categorize)
needs the friend's *own* Claude login: they open the **Advisor** tab and click
**Connect AI** (a Claude Pro/Max account). Friend-facing notes live in
`packaging\READ-ME-FIRST.txt`, which the build drops into the zip.

The packaging is bundle-aware via `app/paths.py` (`resource_path()` for UI
assets, `data_dir()` for the ledger/backups/log under `%LOCALAPPDATA%`); the
PyInstaller recipe is `MoneyPilot.spec`, entry point `run_app.py`.

## Share with a friend (web)

Run MoneyPilot from your PC so a friend can log in from a browser. Data stays
on your machine and the AI uses your local Claude login (no API key).

1. One-time setup:
   - `.venv\Scripts\python.exe -m pip install -r requirements-web.txt`
   - `winget install --id Cloudflare.cloudflared`
   - Create accounts: `.venv\Scripts\python.exe -m web.users add <name>`
     (run once per person; prompts for a password).
2. Start it: `powershell -ExecutionPolicy Bypass -File web\run.ps1`
3. Share the `https://<random>.trycloudflare.com` URL it prints. Each person
   logs in and gets their own isolated ledger under `users\<name>\`.

The URL changes each run (the free quick tunnel). Your PC must be on and the
script running for your friend to reach it. Stop with Ctrl+C.
