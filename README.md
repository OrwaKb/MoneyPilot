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
