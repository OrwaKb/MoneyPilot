# MoneyPilot Pocket — phone capture + sync-when-home (design)

Date: 2026-06-17
Status: approved direction (via brainstorming); pending spec review before build.

## Problem

The user wants to log spending **the moment it happens, from their phone**, instead
of waiting to get to the desktop app at home. Constraints they chose explicitly:

- **No always-on server** (their PC must NOT run 24/7 as a server).
- **Data stays on their own devices** (no cloud backend holding the ledger).
- It should feel like opening an app and logging instantly, even with no signal.

## Solution shape

A small, separate, **capture-focused** Progressive Web App ("MoneyPilot Pocket")
that stores logged expenses **locally on the phone** and **syncs into the existing
home ledger** over the user's private Tailscale network, **only while the desktop
app is open** (PC on when home — not a background server).

This is deliberately NOT a port of the cockpit. The phone captures; the desktop
remains the source of truth and the home of the engine (budgets, rolling
allowance, goals) and the AI.

### Why these choices
- **PWA, not native:** no App Store, no second language; installs to the home
  screen, runs fullscreen, works offline via a service worker.
- **GitHub Pages hosting (static):** the Pocket app is pure client-side files;
  hosting them is just file delivery, not a server, and **no ledger data ever
  goes there** — entries live in the phone's IndexedDB until they sync home.
- **Tailscale for sync:** the phone (HTTPS, secure context) must reach the
  desktop without mixed-content blocking and without exposing anything publicly.
  `tailscale serve` gives a private tailnet HTTPS URL with a real cert. The
  desktop only needs to be on + app open *at sync time*, not 24/7.

## Architecture

```
 PHONE (offline-capable)                 HOME PC (only while app is open)
 ┌───────────────────────┐               ┌───────────────────────────────┐
 │ Pocket PWA            │  HTTPS POST    │ Desktop app (pywebview)        │
 │  • quick-add          │  over Tailscale│  • embedded sync listener      │
 │  • IndexedDB queue    │ ─────────────► │    (stdlib http.server thread) │
 │  • service worker     │  /pocket/sync  │  • token auth                  │
 │  • sync status badge  │ ◄───────────── │  • dedupe by client_uuid       │
 │  (served by GH Pages) │   {ingested}   │  • parser.parse_and_store      │
 └───────────────────────┘               │      → %LOCALAPPDATA% ledger    │
                                          └───────────────────────────────┘
                                tailscale serve → 127.0.0.1:<port>
```

### Component 1 — Pocket PWA (new `pocket/`)
Files: `index.html`, `pocket.css`, `pocket.js`, `manifest.webmanifest`, `sw.js`,
and PNG icons (generated from the existing cockpit icon).

- **Quick-add:** one free-text box ("45 falafel with karim"). The phone does a
  *minimal* local parse (pull a leading/!trailing number as an amount guess for
  the local total + display); the authoritative parse/categorize happens on the
  desktop at sync.
- **Local store (IndexedDB):** each entry `{uuid, raw_text, amount_guess,
  created_at (ISO), synced: bool}`. `uuid` is client-generated (crypto.randomUUID).
- **List + undo:** recent entries with sync status (`⏳ waiting` / `✓ synced`);
  delete is allowed only before sync.
- **Today total:** sum of today's `amount_guess` — rough instant feedback, not
  the real allowance.
- **Offline:** service worker caches the app shell; capture works with zero
  signal. Installable via the manifest ("Add to Home Screen").
- **Settings (one-time pairing):** the desktop's Tailscale URL + a pairing token,
  entered by scanning a QR shown on the desktop or pasting.
- **Sync loop:** on launch/online and when the endpoint is reachable, POST all
  `synced:false` entries; on 200, mark them synced. Backoff on failure. Never
  blocks capture.

### Component 2 — Desktop sync listener (new, embedded)
- A **stdlib `http.server`** running on a background thread, started in
  `app.__main__.main()` while the GUI is open (mirrors the existing singleton
  socket server). No new bundle deps (FastAPI/uvicorn stay excluded from the
  PyInstaller build).
- Endpoint `POST /pocket/sync` (JSON: list of entries). Auth: `Authorization:
  Bearer <token>` checked against a locally stored secret. Bound to 127.0.0.1;
  exposed to the tailnet only via `tailscale serve`.
- For each entry: if `client_uuid` already in the ledger → skip (idempotent).
  Else run `raw_text` through the existing `parser.parse_and_store` (AI +
  regex fallback) dated `created_at`, tagged `source="pocket"`, storing
  `client_uuid`. Return `{ingested: [...], skipped: [...]}`.
- Concurrency: the listener uses its own short-lived connection(s); WAL +
  busy_timeout already make this safe alongside the GUI's connection.

### Component 3 — Schema
- Add nullable `client_uuid TEXT` to `transactions` with a UNIQUE index
  (migration v_next). Used only for Pocket dedupe; existing rows are NULL.

### Component 4 — Auth & pairing
- The desktop generates a random secret token (stored in `%LOCALAPPDATA%`),
  shown in a "Pocket setup" view as text + QR. The phone stores it and sends it
  as a Bearer on every sync. Over the already-private tailnet this is sufficient;
  it also protects against a stray device on the tailnet.

### Component 5 — Tailscale transport (user setup, documented)
1. Install Tailscale on PC + phone (same account); enable MagicDNS + HTTPS certs.
2. `tailscale serve` forwards `https://<pc>.<tailnet>.ts.net` → `127.0.0.1:<port>`
   (one-time; a helper script prints the command).
3. PC on + desktop app open at sync time (i.e., when home). Phone on the tailnet.

## One-time setup (user)
Install Tailscale both ends → enable HTTPS certs → run the serve helper → open
the desktop "Pocket setup" (shows URL + QR) → on the phone open the GitHub Pages
Pocket URL, Add to Home Screen, scan the QR. Then: tap icon → log → it syncs
whenever you're home with the app open.

## Phasing
- **Phase 1 (this build):** Pocket PWA (offline capture, IndexedDB, installable)
  + desktop embedded sync listener + dedupe + token auth + ingestion + the
  `client_uuid` migration + a manual "import a sync file" fallback + setup docs.
  Tailscale auto-sync wired and working end-to-end.
- **Later:** background sync (best-effort on mobile), a live "today's allowance"
  readout pulled from the desktop when reachable, smarter on-phone categorization
  hints, richer history on the phone.

## Non-goals
- Running the full engine / AI on the phone.
- A 24/7 server or any cloud-hosted ledger.
- Multi-user phone access (this is the owner's own device).

## Testing
- PWA + sync UI: load in a simulated mobile browser (Playwright mobile viewport),
  exercise offline capture (IndexedDB), the pending/synced states, and a mocked
  sync POST.
- Desktop listener: unit tests for auth (reject bad/missing token), dedupe
  (same `client_uuid` twice → one row), and ingestion (raw_text → parsed txn in
  the ledger), mocking the AI like the existing advisor/parser tests.
- Migration test for `client_uuid`.
- Real phone install + Tailscale sync: user-verified (can't be automated here).

## Risks / honest caveats
- **Biggest build yet:** a second app + a sync channel + schema + transport. Phase 1
  is scoped to a working minimum.
- **Tailscale dependency:** auto-sync needs Tailscale on both devices and the
  desktop app open at sync time. (A manual share-file import is included as a
  no-Tailscale fallback.)
- **iOS PWA limits:** background sync is restricted on iOS; sync triggers on app
  open/foreground rather than truly in the background. Acceptable for "sync when
  I'm home and open it."
- **Mixed content:** solved by Tailscale HTTPS; a plain-HTTP LAN endpoint would be
  blocked from the HTTPS PWA, which is why Tailscale (not bare LAN) is required.
```
