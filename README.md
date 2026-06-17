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

    powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1        # 1. the app folder + zip
    powershell -ExecutionPolicy Bypass -File scripts\build_installer.ps1  # 2. the installer

Step 1 produces `dist\MoneyPilot-windows.zip`; step 2 wraps the built folder
into `dist\MoneyPilot-Setup.exe` (~70 MB, Inno Setup — `winget install
JRSoftware.InnoSetup` once). **Send the installer** — it installs to
`%LOCALAPPDATA%\Programs\MoneyPilot` (no admin, and a path OneDrive never
syncs), which avoids the pythonnet/.NET launch crash that hits zips unpacked
into OneDrive. The zip still works as a fallback **if** unpacked to a plain
local folder like `C:\MoneyPilot` (never inside OneDrive/Desktop). Smoke-test
headlessly with `dist\MoneyPilot\MoneyPilot.exe --selftest`.

The whole tracker works offline. The **AI** (advisor, briefing, auto-categorize)
needs the friend's *own* Claude login: they open the **Advisor** tab and click
**Connect AI** (a Claude Pro/Max account). Friend-facing notes live in
`packaging\READ-ME-FIRST.txt`, which the build drops into the zip.

The packaging is bundle-aware via `app/paths.py` (`resource_path()` for UI
assets, `data_dir()` for the ledger/backups/log under `%LOCALAPPDATA%`); the
PyInstaller recipe is `MoneyPilot.spec`, entry point `run_app.py`.

### Releasing updates

The app checks GitHub for a newer release on launch and shows a "Download"
banner (data is safe across updates — it lives in `%LOCALAPPDATA%`, separate
from the app folder, and schema migrations run automatically). Setup is once;
each release is three steps.

One-time:
1. Create a GitHub repo and push this project.
2. Set `GITHUB_REPO = "<you>/<repo>"` in `app/version.py` (until then the
   update check is dormant — zero network calls).

Each release:
1. Bump `__version__` in `app/version.py` (e.g. `1.1.0`).
2. `scripts\build_exe.ps1` then `scripts\build_installer.ps1`
3. Publish a GitHub Release tagged `v<version>` (matching `__version__`) with
   `dist\MoneyPilot-Setup.exe` attached (and the zip if you like). Friends get
   the in-app banner next launch; it links to the release page so they grab the
   installer.

## Use it on your phone (MoneyPilot Pocket)

Log spending from your phone on the go; it syncs into this same ledger when
you're home and the desktop app is open. No always-on server, no cloud — data
stays on your devices. The phone is capture-only (budgets/allowance/AI stay here).

How it fits together: the phone runs a tiny capture PWA (`pocket/`, hosted on
GitHub Pages) that queues entries offline in the browser; the desktop app runs a
localhost sync listener (`app/sync_server.py`, port 8788) only while it's open;
your private Tailscale network carries phone → desktop over HTTPS.

One-time setup:
1. Install **Tailscale** on this PC and your phone, sign into the same account on
   both, and enable **HTTPS certificates** in the Tailscale admin console.
2. `powershell -ExecutionPolicy Bypass -File scripts\pocket-serve.ps1`
   (forwards your tailnet HTTPS → the localhost listener).
3. Open the desktop app → **Settings → PHONE**, and on your phone open the
   one-tap **pairing link** shown there. Then open the Pocket page
   (`https://<you>.github.io/MoneyPilot/pocket/`) and **Add to Home Screen**.

After that: tap the Pocket icon, log an expense (works offline), and it lands in
your ledger next time you're home with the app open. Entries dedupe by a
client id, so re-syncs never double-count.

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
