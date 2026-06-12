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

## Dev
    .venv\Scripts\python -m pytest          # all tests, offline, AI mocked
    .venv\Scripts\python -m app --dev       # seeded fake ledger + DevTools
    .venv\Scripts\python -m app --restore backups\ledger-YYYY-MM-DD.json
