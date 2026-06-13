# MoneyPilot Web — PC-hosted, tunneled, multi-user (2026-06-13)

Serve the existing MoneyPilot cockpit over HTTP from the user's own PC so a
small set of hand-provisioned friends can log in from a browser, each with
their own isolated ledger. Hosted locally and exposed through a Cloudflare
Tunnel; data never leaves the host machine; the AI reuses the host's local
Claude login (no API key, no per-call cost).

## Decisions (locked in brainstorming)

- **Host:** the user's own PC, exposed via Cloudflare Tunnel. Start with the
  zero-setup **quick tunnel** (`cloudflared tunnel --url …`) — an ephemeral
  `https://<random>.trycloudflare.com` URL, no domain or Cloudflare account
  required. Named-tunnel (stable URL) documented as a later upgrade.
- **Users:** just the owner + one or two friends, accounts created by hand. No
  public signup, no email, no password reset flow.
- **AI:** unchanged. It runs on the host PC where the Claude login already
  lives, so `app/ai/client.py` (`ask_claude`: Agent SDK → `claude -p` CLI →
  caller's regex fallback) works verbatim. No `ANTHROPIC_API_KEY`, no AI code.
- **Data:** one ledger file per user, `users/<name>/ledger.db`. The existing
  single-ledger schema is unchanged; the engine and `Api` are reused as-is.

## Goals

- The full cockpit (4 tabs, entry bar, gauges, chat, onboarding) usable in a
  normal browser, visually identical to the desktop app.
- Each friend's data hard-isolated from every other user's.
- Reuse the engine, `Api`, parser, advisor, and UI essentially verbatim.

## Non-goals

- No cloud hosting, no hosted DB, no API-key path (that was the rejected
  Streamlit/cloud direction).
- No public signup, billing, RBAC, or password-reset. Accounts are provisioned
  by the owner from the command line.
- No change to the desktop app — `python -m app` keeps working untouched.

## Framework

**FastAPI + uvicorn.** Clean routing, a one-line `/api/{method}` dispatcher,
signed-cookie sessions via Starlette `SessionMiddleware`, and static serving.
FastAPI runs the existing *synchronous* `Api` methods in its threadpool, so each
user's per-instance `threading.Lock` still serializes that user's writes. Web
deps go in a separate `requirements-web.txt` so the desktop install stays lean.

Rejected: Flask (sync-only, less ergonomic for the dispatcher/sessions) and
stdlib `http.server` (would hand-roll routing, sessions, static serving).

## Components (new `web/` package; desktop entry point untouched)

- **`web/server.py`** — the FastAPI app:
  - `GET /` → serves `index.html` if authenticated, else redirects to `/login`.
  - `GET /login` → `login.html`; `POST /login` → verify hash, set session,
    redirect to `/`; `POST /logout` → clear session.
  - static mount for `app/ui/` (css/js/assets) and the new `login.*`.
  - `POST /api/{method}` → the bridge dispatcher (below).
  - `SessionMiddleware` with a strong `SECRET_KEY`; cookies `httponly`,
    `secure`, `samesite=lax`.
- **`web/auth.py`** — password hashing with stdlib `hashlib.pbkdf2_hmac`
  (per-user random salt, fixed high iteration count), constant-time compare,
  and the JSON user-store read/write (`users/users.json`:
  `{name: {salt, hash, iterations}}`). Dep-free.
- **`web/registry.py`** — `get_api(username) -> Api`: lazily build and cache one
  `Api` per user bound to `users/<name>/ledger.db` with `backup_dir =
  users/<name>/backups/`; reused across that user's requests.
- **`web/users.py`** — `python -m web.users add|passwd|list|remove <name>` CLI
  to manage accounts (prompts for password via `getpass`, never echoes).
- **`web/run.ps1`** — generate `SECRET_KEY` on first run (gitignored
  `web/secret.key`), start uvicorn, start `cloudflared tunnel --url
  http://127.0.0.1:8000`, and print the public URL for the owner to share.

## The dispatcher (`POST /api/{method}`)

The desktop UI funnels every backend call through one function
(`app.js:21`: `window.pywebview.api[method](...args)`). The web dispatcher is
its mirror:

1. Require an authenticated session; else `401` (the frontend bounces to login).
2. `method` must be in an explicit **allowlist** of the `Api` methods the UI
   calls (e.g. `startup`, `get_overview`, `add_entry`, `undo_txn`, `restore_txn`,
   `update_txn`, `list_ledger`, `get_goals`, `save_goal`, `archive_goal`,
   `get_briefing`, `chat_send`, `chat_apply_action`, `get_chat_history`,
   `list_chats`, `delete_chat`, `get_app_settings`, `save_settings`,
   `set_category_budget`, `onboarding_braindump`, `onboarding_complete`).
   Anything else → `403`. Same trust surface as the desktop bridge, with
   defense-in-depth against arbitrary attribute access. (`export_csv` is *not*
   here — it gets a dedicated download route, see Frontend changes.)
3. Body is a JSON array of positional args; call
   `getattr(get_api(user), method)(*args)` (already `_safe`-wrapped → returns a
   dict, never raises into the handler) and return it as JSON.

## Frontend changes (surgically small, and desktop stays working)

The same `app.js` must run in BOTH the desktop (pywebview bridge) and the web
(HTTP) — so `api()` becomes **dual-mode**, branching on a flag the web server
injects. The desktop path is unchanged; the web path is added beside it.

- The web server serves `index.html` with a one-line `window.__MP_WEB__ = true`
  injected before `app.js` (the desktop loads the file without it). Then in
  `app.js`:
  - `ready` = `window.__MP_WEB__ ? Promise.resolve() : new
    Promise(res => window.addEventListener("pywebviewready", res))` — desktop
    still waits for `pywebviewready`; web resolves immediately.
  - `api(method, ...args)`: `await ready`; if not web, return
    `window.pywebview.api[method](...args)` (today's behavior, untouched);
    if web, `await fetch('/api/'+method, {method:'POST',
    headers:{'Content-Type':'application/json'}, body: JSON.stringify(args)})`,
    return parsed JSON, and on `401` redirect to `/login`.
  - **Nothing else in the tabs / chat / onboarding changes** — they all go
    through `api()`.
- New `app/ui/login.html` (+ minimal inline CSS reusing the cockpit palette:
  bg `#0d1117`, panel `#141b26`, teal `#4ef0c0`): username + password, posts to
  `/login`, shows an error on failure.
- The one other UI touch — export. `Api.export_csv` writes a file on the host
  and returns a server path, useless to a remote browser. A dedicated route
  `GET /api/export_csv?month=YYYY-MM` streams the CSV back as a download
  (`Content-Disposition: attachment`). The export button handler gets a small
  web branch: navigate to that URL instead of calling `api('export_csv', …)`.
  (Desktop keeps the existing path-returning behavior.)

## Security

- Strong random `SECRET_KEY` (32 bytes, `secrets.token_hex`) persisted once to a
  gitignored `web/secret.key`; loaded at startup.
- Login throttle: per-IP exponential backoff after consecutive failures (in
  memory; fine for a 2–3 user app). Generic "invalid credentials" message.
- Cookie flags `httponly` + `secure` + `samesite=lax`; session carries only the
  username.
- Hard data isolation: a user can only ever reach their own `users/<name>/`
  directory; there is no code path that crosses users.
- The tunnel is a public URL into the host — these measures are the front door's
  lock. `cloudflared` terminates HTTPS.

## Testing

- **pytest (offline, AI mocked) — `tests/test_web.py`:**
  - auth: hash round-trip, wrong password rejected, throttle triggers after N
    failures, constant-time compare used.
  - dispatcher: unauthenticated → 401; non-allowlisted method → 403; allowlisted
    call routes to the session user's `Api` and returns its dict.
  - isolation: two users' `add_entry` writes land in separate ledgers; user A
    cannot read user B's overview.
  - `export_csv` route returns CSV bytes with an attachment header.
  - desktop unaffected: existing `python -m app` path and the 188 engine/parser
    tests stay green.
- **Browser (house technique):** run a live uvicorn instance with a mocked AI,
  drive login → cockpit with Playwright, screenshot the login page and each tab
  as **two different users** (showing isolated data), and send those as the
  visual approval gate.

## Build phases (one spec, phased implementation plan)

- **A — HTTP core:** `requirements-web.txt`, FastAPI skeleton, static serving,
  and the `/api/{method}` dispatcher bound to a *single* shared ledger (no auth
  yet). Rewrite `api()` and prove the whole cockpit works in a real browser.
  Riskiest integration — validate first.
- **B — Auth + multi-user:** `login.html`, sessions, `web/auth.py`,
  `web/users.py` CLI, `web/registry.py` per-user `Api`/ledger isolation, the
  401→login bounce.
- **C — Tunnel + hardening:** `cloudflared` quick tunnel, `web/run.ps1`,
  `SECRET_KEY` generation, login throttle, cookie flags, `export_csv` download,
  README "share with a friend" section.

## Files

- new: `web/__init__.py`, `web/server.py`, `web/auth.py`, `web/registry.py`,
  `web/users.py`, `web/run.ps1`, `app/ui/login.html`, `requirements-web.txt`,
  `tests/test_web.py`
- edit: `app/ui/app.js` (the `api()` function + `ready`), `.gitignore`
  (`web/secret.key`, `users/`), `README.md` (share-with-a-friend section)
- untouched: the entire desktop path (`app/__main__.py`, the engine, the AI
  client, the schema)
