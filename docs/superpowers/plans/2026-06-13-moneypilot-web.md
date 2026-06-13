# MoneyPilot Web Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve the existing MoneyPilot cockpit over HTTP from the owner's PC so a few hand-provisioned friends can log in from a browser, each with an isolated `users/<name>/ledger.db`, exposed via a Cloudflare quick tunnel.

**Architecture:** A new `web/` package (FastAPI + uvicorn) wraps the existing `app.api.Api`/engine unchanged. Every desktop bridge call (`app.js:21`) becomes a `POST /api/{method}` to an auth-gated dispatcher that routes to the session user's cached `Api`. The same `app.js` runs in both desktop (pywebview) and web by detecting `location.protocol`. The AI reuses the host's local Claude login — no API key. The desktop app (`python -m app`) is left fully working.

**Tech Stack:** FastAPI, uvicorn, Starlette `SessionMiddleware`, stdlib `hashlib.pbkdf2_hmac` for passwords, `cloudflared` for the tunnel. Tests: `fastapi.testclient.TestClient` (httpx) + the existing pytest suite. Browser verification via the house Playwright technique.

**Spec:** `docs/superpowers/specs/2026-06-13-moneypilot-web-design.md`

---

## File structure

- `web/__init__.py` — package marker.
- `web/auth.py` — password hashing (pbkdf2) + JSON `UserStore`. No deps.
- `web/registry.py` — `Registry.get_api(username)`: per-user cached `Api`, path-safe.
- `web/server.py` — `create_app(...)` factory, routes, dispatcher, `get_app()` for uvicorn.
- `web/users.py` — `python -m web.users add|passwd|list|remove` CLI.
- `web/run.ps1` — launch uvicorn + cloudflared, print the public URL.
- `app/ui/login.html` — login page in the cockpit palette.
- `app/ui/app.js` — *edit* the `api()`/`ready` definitions (dual-mode) + the export click handler.
- `app/api.py` — *edit* `export_csv` to accept an optional `out_dir` (non-breaking) for per-user export isolation.
- `requirements-web.txt` — web-only deps (keeps the desktop install lean).
- `tests/test_web.py` — all web tests.
- `.gitignore` — *edit* add `users/` and `web/secret.key`.
- `README.md` — *edit* add a "Share with a friend (web)" section.

Untouched: `app/__main__.py`, the engine, `app/ai/*`, the schema.

---

## Task 1: Web dependencies

**Files:**
- Create: `requirements-web.txt`
- Create: `web/__init__.py` (empty)

- [ ] **Step 1: Create `requirements-web.txt`**

```
# IMPORTANT — starlette constraint: claude-agent-sdk pulls in sse-starlette,
# which needs a modern starlette (>=0.49). An old FastAPI (e.g. 0.115) pins
# starlette down to 0.41.x and BREAKS the AI integration. Keep FastAPI modern.
fastapi==0.136.3
starlette>=1.3.1
uvicorn==0.34.0
httpx==0.28.1
python-multipart==0.0.20
itsdangerous==2.2.0
```

(`itsdangerous` is required by Starlette `SessionMiddleware`; `python-multipart` by form parsing; `httpx` by `TestClient`. FastAPI is pinned to 0.136.3 — not an older release — so it coexists with the modern starlette that `claude-agent-sdk`/`sse-starlette` require; an older FastAPI downgrades starlette and breaks the AI. **Task 1 was completed manually during setup** — these deps are already installed.)

- [ ] **Step 2: Create the package marker**

```python
# web/__init__.py
```

(empty file)

- [ ] **Step 3: Install into the existing venv**

Run: `.venv\Scripts\python.exe -m pip install -r requirements-web.txt`
Expected: installs without error.

- [ ] **Step 4: Verify imports**

Run: `.venv\Scripts\python.exe -c "import fastapi, uvicorn, multipart, itsdangerous, httpx; print('ok')"`
Expected: prints `ok`.

- [ ] **Step 5: Commit**

```bash
git add requirements-web.txt web/__init__.py
git commit -m "build: web dependencies (fastapi/uvicorn) in a separate requirements file"
```

---

## Task 2: Password hashing and user store (`web/auth.py`)

**Files:**
- Create: `web/auth.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_web.py
from web import auth


def test_hash_roundtrip():
    rec = auth.hash_password("hunter2")
    assert auth.verify_password("hunter2", rec)
    assert not auth.verify_password("wrong", rec)


def test_hash_uses_random_salt():
    a = auth.hash_password("same")
    b = auth.hash_password("same")
    assert a["salt"] != b["salt"]
    assert a["hash"] != b["hash"]


def test_user_store_add_verify_list_remove(tmp_path):
    store = auth.UserStore(tmp_path / "users.json")
    assert store.list() == []
    store.add("alice", "pw1")
    assert store.exists("alice")
    assert store.verify("alice", "pw1")
    assert not store.verify("alice", "nope")
    assert not store.verify("ghost", "x")        # unknown user, no crash
    assert store.list() == ["alice"]
    store.remove("alice")
    assert not store.exists("alice")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.auth'`.

- [ ] **Step 3: Implement `web/auth.py`**

```python
# web/auth.py
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from pathlib import Path

ITERATIONS = 240_000


def hash_password(password: str, *, salt: str | None = None,
                  iterations: int = ITERATIONS) -> dict:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(salt), iterations)
    return {"salt": salt, "hash": dk.hex(), "iterations": iterations}


def verify_password(password: str, record: dict) -> bool:
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                             bytes.fromhex(record["salt"]),
                             int(record["iterations"]))
    return hmac.compare_digest(dk.hex(), record["hash"])


class UserStore:
    """Tiny JSON-backed credential store: {username: {salt, hash, iterations}}."""

    def __init__(self, path):
        self.path = Path(path)

    def _load(self) -> dict:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def add(self, username: str, password: str) -> None:
        data = self._load()
        data[username] = hash_password(password)
        self._save(data)

    def verify(self, username: str, password: str) -> bool:
        rec = self._load().get(username)
        if not rec:
            # constant-ish work for unknown users to blunt a timing oracle
            verify_password(password, hash_password("decoy"))
            return False
        return verify_password(password, rec)

    def exists(self, username: str) -> bool:
        return username in self._load()

    def remove(self, username: str) -> None:
        data = self._load()
        data.pop(username, None)
        self._save(data)

    def list(self) -> list[str]:
        return sorted(self._load())
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add web/auth.py tests/test_web.py
git commit -m "feat(web): pbkdf2 password hashing and JSON user store"
```

---

## Task 3: Per-user Api registry (`web/registry.py`)

**Files:**
- Create: `web/registry.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing tests (append to `tests/test_web.py`)**

```python
import pytest
from web.registry import Registry


def test_registry_creates_isolated_ledgers(tmp_path):
    reg = Registry(tmp_path)
    a = reg.get_api("alice")
    b = reg.get_api("bob")
    assert a is not b
    assert (tmp_path / "alice" / "ledger.db").exists()
    assert (tmp_path / "bob" / "ledger.db").exists()


def test_registry_caches_same_instance(tmp_path):
    reg = Registry(tmp_path)
    assert reg.get_api("alice") is reg.get_api("alice")


def test_registry_rejects_unsafe_username(tmp_path):
    reg = Registry(tmp_path)
    for bad in ["../escape", "a/b", "", "x" * 33, "Bad Name"]:
        with pytest.raises(ValueError):
            reg.get_api(bad)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py::test_registry_creates_isolated_ledgers -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'web.registry'`.

- [ ] **Step 3: Implement `web/registry.py`**

```python
# web/registry.py
from __future__ import annotations

import re
import threading
from pathlib import Path

from app.api import Api

_VALID = re.compile(r"^[a-z0-9_-]{1,32}$")


class Registry:
    """One cached Api per user, each bound to users/<name>/ledger.db."""

    def __init__(self, base_dir):
        self.base = Path(base_dir)
        self._apis: dict[str, Api] = {}
        self._lock = threading.Lock()

    def user_dir(self, username: str) -> Path:
        if not _VALID.match(username or ""):
            raise ValueError(f"invalid username: {username!r}")
        return self.base / username

    def get_api(self, username: str) -> Api:
        ud = self.user_dir(username)          # validates before any caching
        with self._lock:
            api = self._apis.get(username)
            if api is None:
                api = Api(ud / "ledger.db", backup_dir=ud / "backups")
                self._apis[username] = api
            return api
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py -v`
Expected: PASS (all tests so far).

- [ ] **Step 5: Commit**

```bash
git add web/registry.py tests/test_web.py
git commit -m "feat(web): per-user Api registry with path-safe usernames"
```

---

## Task 4: FastAPI app — sessions, login, dispatcher (`web/server.py`)

**Files:**
- Create: `web/server.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing tests (append to `tests/test_web.py`)**

```python
from fastapi.testclient import TestClient
from app.ai import client as ai_client


def _make_app(tmp_path):
    from web.server import create_app
    store = auth.UserStore(tmp_path / "users.json")
    store.add("alice", "pw1")
    store.add("bob", "pw2")
    return create_app(base_dir=tmp_path, users_path=tmp_path / "users.json",
                      secret_key="test-secret")


def _login(c, user, pw):
    return c.post("/login", data={"username": user, "password": pw},
                  follow_redirects=False)


def test_dispatch_requires_auth(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        r = c.post("/api/get_overview", json=[])
        assert r.status_code == 401


def test_login_then_overview(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        assert _login(c, "alice", "pw1").status_code == 303
        r = c.post("/api/get_overview", json=[])
        assert r.status_code == 200 and r.json()["ok"] is True


def test_bad_login_rejected(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        r = _login(c, "alice", "WRONG")
        assert r.status_code == 303 and "error" in r.headers["location"]
        assert c.post("/api/get_overview", json=[]).status_code == 401


def test_non_allowlisted_method_forbidden(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        _login(c, "alice", "pw1")
        # a real Api attribute that is NOT in the allowlist
        assert c.post("/api/export_csv", json=["2026-06"]).status_code != 200
        assert c.post("/api/__init__", json=[]).status_code == 403


def test_root_redirects_when_logged_out(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        r = c.get("/", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("/login")


def test_two_users_isolated(tmp_path, monkeypatch):
    # force the offline regex parser so add_entry is deterministic + network-free
    monkeypatch.setattr(ai_client, "ask_claude",
                        lambda *a, **k: (_ for _ in ()).throw(
                            ai_client.AIUnavailable("offline")))
    app = _make_app(tmp_path)
    with TestClient(app) as ca, TestClient(app) as cb:
        _login(ca, "alice", "pw1")
        _login(cb, "bob", "pw2")
        ca.post("/api/add_entry", json=["45 coffee"])
        a_recent = ca.post("/api/get_overview", json=[]).json()["recent"]
        b_recent = cb.post("/api/get_overview", json=[]).json()["recent"]
        assert len(a_recent) == 1 and len(b_recent) == 0
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py::test_login_then_overview -v`
Expected: FAIL — cannot import `create_app`.

- [ ] **Step 3: Implement `web/server.py`**

```python
# web/server.py
from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import (FileResponse, JSONResponse, RedirectResponse)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from web.auth import UserStore
from web.registry import Registry

PROJECT_DIR = Path(__file__).resolve().parent.parent
UI_DIR = PROJECT_DIR / "app" / "ui"

# The exact Api methods the cockpit UI calls through api() (app.js).
# export_csv is intentionally excluded — it has a dedicated download route.
ALLOWED = {
    "startup", "get_overview", "add_entry", "undo_txn", "restore_txn",
    "update_txn", "list_ledger", "get_goals", "save_goal", "archive_goal",
    "get_briefing", "chat_send", "chat_apply_action", "get_chat_history",
    "list_chats", "delete_chat", "get_app_settings", "save_settings",
    "set_category_budget", "onboarding_braindump", "onboarding_complete",
}


def create_app(*, base_dir, users_path, secret_key) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key=secret_key,
                       https_only=True, same_site="lax")
    store = UserStore(users_path)
    registry = Registry(base_dir)

    @app.get("/")
    def root(request: Request):
        if not request.session.get("user"):
            return RedirectResponse("/login", status_code=303)
        return FileResponse(UI_DIR / "index.html")

    @app.get("/login")
    def login_page():
        return FileResponse(UI_DIR / "login.html")

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        if store.verify(username, password):
            request.session["user"] = username
            return RedirectResponse("/", status_code=303)
        return RedirectResponse("/login?error=1", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.post("/api/{method}")
    async def dispatch(method: str, request: Request):
        user = request.session.get("user")
        if not user:
            return JSONResponse({"ok": False, "error": "auth"}, status_code=401)
        if method not in ALLOWED:
            return JSONResponse({"ok": False, "error": "forbidden"},
                                status_code=403)
        try:
            args = await request.json()
        except Exception:
            args = []
        if not isinstance(args, list):
            args = [] if args is None else [args]
        api = registry.get_api(user)
        result = await run_in_threadpool(getattr(api, method), *args)
        return JSONResponse(result)

    # Static assets (app.css, app.js, assets/*). Added LAST so the explicit
    # routes above win; html=False so it never auto-serves index.html for "/".
    app.mount("/", StaticFiles(directory=str(UI_DIR), html=False), name="ui")
    return app


def _load_or_create_secret(path: Path) -> str:
    path = Path(path)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(32)
    path.write_text(token, encoding="utf-8")
    return token


def get_app() -> FastAPI:
    """uvicorn entry: `uvicorn web.server:get_app --factory`."""
    base = Path(os.environ.get("MP_USERS_DIR", str(PROJECT_DIR / "users")))
    secret = _load_or_create_secret(PROJECT_DIR / "web" / "secret.key")
    return create_app(base_dir=base, users_path=base / "users.json",
                      secret_key=secret)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py -v`
Expected: PASS (all web tests). Note: `test_non_allowlisted_method_forbidden` checks `export_csv` is not 200 here — its dedicated route arrives in Task 8; for now it returns 403, which satisfies `!= 200`.

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_web.py
git commit -m "feat(web): FastAPI app with sessions, login, and the api dispatcher"
```

---

## Task 5: Login page (`app/ui/login.html`)

**Files:**
- Create: `app/ui/login.html`

- [ ] **Step 1: Create `app/ui/login.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>MoneyPilot — sign in</title>
<link rel="icon" type="image/png" href="assets/favicon-32.png">
<style>
  :root { --bg:#0d1117; --panel:#141b26; --accent:#4ef0c0; --line:#26334a;
          --text:#e6edf3; --muted:#8b97a7; }
  * { box-sizing: border-box; }
  body { margin:0; min-height:100vh; display:flex; align-items:center;
         justify-content:center; background:var(--bg); color:var(--text);
         font-family:'Segoe UI',system-ui,sans-serif; }
  .card { background:var(--panel); border:1px solid var(--line);
          border-radius:14px; padding:32px 28px; width:300px;
          box-shadow:0 10px 40px rgba(0,0,0,.5); }
  h1 { font-size:20px; margin:0 0 4px; letter-spacing:.5px; }
  p.sub { margin:0 0 20px; color:var(--muted); font-size:13px; }
  label { display:block; font-size:12px; color:var(--muted); margin:12px 0 4px; }
  input { width:100%; padding:10px; border-radius:8px; border:1px solid var(--line);
          background:#0d1420; color:var(--text); font-size:14px; }
  input:focus { outline:none; border-color:var(--accent); }
  button { width:100%; margin-top:20px; padding:11px; border:none;
           border-radius:8px; background:var(--accent); color:#06231c;
           font-weight:600; font-size:14px; cursor:pointer; }
  .err { display:none; margin-top:14px; color:#ff8c8c; font-size:13px; }
</style>
</head>
<body>
  <form class="card" method="post" action="/login">
    <h1>🧭 MoneyPilot</h1>
    <p class="sub">Sign in to your ledger</p>
    <label for="u">Username</label>
    <input id="u" name="username" autocomplete="username" autofocus required>
    <label for="p">Password</label>
    <input id="p" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Sign in</button>
    <div class="err" id="err">Incorrect username or password.</div>
  </form>
  <script>
    if (new URLSearchParams(location.search).has("error")) {
      document.getElementById("err").style.display = "block";
    }
  </script>
</body>
</html>
```

- [ ] **Step 2: Manual smoke (optional now; full browser check in Task 11)**

Run: `.venv\Scripts\python.exe -m uvicorn web.server:get_app --factory --port 8000` then open `http://127.0.0.1:8000/` — expect a redirect to the styled login page. Stop the server (Ctrl+C).

- [ ] **Step 3: Commit**

```bash
git add app/ui/login.html
git commit -m "feat(web): login page in the cockpit palette"
```

---

## Task 6: Dual-mode `api()` and export branch (`app/ui/app.js`)

**Files:**
- Modify: `app/ui/app.js:7` (the `ready` constant) and `app/ui/app.js:21-24` (the `api()` function); the export click handler near `app/ui/app.js:396`.

- [ ] **Step 1: Replace the `ready` definition (line 7)**

Old:
```js
const ready = new Promise((res) => window.addEventListener("pywebviewready", res));
```
New:
```js
// Desktop (pywebview) loads index.html via file://; the web server serves it
// over http(s). Detect the mode once and branch the bridge accordingly.
const WEB = location.protocol === "http:" || location.protocol === "https:";
const ready = WEB
  ? Promise.resolve()
  : new Promise((res) => window.addEventListener("pywebviewready", res));
```

- [ ] **Step 2: Replace the `api()` function (lines 21-24)**

Old:
```js
async function api(method, ...args) {
  await ready;
  return window.pywebview.api[method](...args);
}
```
New:
```js
async function api(method, ...args) {
  await ready;
  if (!WEB) return window.pywebview.api[method](...args);
  const r = await fetch("/api/" + method, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(args),
  });
  if (r.status === 401) {            // session expired -> back to login
    location.href = "/login";
    return new Promise(() => {});    // never resolves; navigation takes over
  }
  return r.json();
}
```

- [ ] **Step 3: Branch the export handler (near line 396)**

Old:
```js
  const res = await api("export_csv", month);
  toast(res.ok ? "exported: " + res.path : res.error);
```
New:
```js
  if (WEB) {                          // browser download via the dedicated route
    location.href = "/api/export_csv?month=" + encodeURIComponent(month);
    return;
  }
  const res = await api("export_csv", month);
  toast(res.ok ? "exported: " + res.path : res.error);
```

- [ ] **Step 4: Verify the desktop app still imports/launches**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all existing tests still PASS (Python untouched; this step guards against accidental edits elsewhere).
Run (manual, optional): `.venv\Scripts\python.exe -m app --dev` — desktop cockpit still loads (file:// → `WEB` is false → uses the pywebview bridge). Close it.

- [ ] **Step 5: Commit**

```bash
git add app/ui/app.js
git commit -m "feat(web): dual-mode api() (desktop bridge or http) + export download"
```

---

## Task 7: User-management CLI (`web/users.py`)

**Files:**
- Create: `web/users.py`
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing tests (append to `tests/test_web.py`)**

```python
from web import users as users_cli


def test_cli_add_and_list(tmp_path, monkeypatch, capsys):
    upath = tmp_path / "users.json"
    monkeypatch.setattr(users_cli.getpass, "getpass", lambda *a, **k: "secret")
    assert users_cli.main(["add", "alice", "--users", str(upath)]) == 0
    store = auth.UserStore(upath)
    assert store.verify("alice", "secret")
    assert users_cli.main(["list", "--users", str(upath)]) == 0
    assert "alice" in capsys.readouterr().out


def test_cli_remove(tmp_path, monkeypatch):
    upath = tmp_path / "users.json"
    monkeypatch.setattr(users_cli.getpass, "getpass", lambda *a, **k: "secret")
    users_cli.main(["add", "bob", "--users", str(upath)])
    assert users_cli.main(["remove", "bob", "--users", str(upath)]) == 0
    assert not auth.UserStore(upath).exists("bob")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py::test_cli_add_and_list -v`
Expected: FAIL — cannot import `web.users`.

- [ ] **Step 3: Implement `web/users.py`**

```python
# web/users.py
"""Account admin:  python -m web.users add|passwd|list|remove <name>"""
from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from web.auth import UserStore

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_USERS = Path(os.environ.get("MP_USERS_DIR",
                                    str(PROJECT_DIR / "users"))) / "users.json"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m web.users")
    ap.add_argument("action", choices=["add", "passwd", "list", "remove"])
    ap.add_argument("username", nargs="?")
    ap.add_argument("--users", default=str(DEFAULT_USERS),
                    help="path to users.json")
    args = ap.parse_args(argv)
    store = UserStore(args.users)

    if args.action == "list":
        for name in store.list():
            print(name)
        return 0

    if not args.username:
        ap.error(f"{args.action} requires a username")

    if args.action == "remove":
        store.remove(args.username)
        print(f"removed {args.username}")
        return 0

    if args.action == "add" and store.exists(args.username):
        ap.error(f"user {args.username!r} already exists (use passwd to reset)")

    pw = getpass.getpass(f"password for {args.username}: ")
    if not pw:
        ap.error("empty password")
    confirm = getpass.getpass("confirm: ")
    if pw != confirm:
        ap.error("passwords do not match")
    store.add(args.username, pw)
    print(f"{'updated' if args.action == 'passwd' else 'added'} {args.username}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/users.py tests/test_web.py
git commit -m "feat(web): user-management CLI (add/passwd/list/remove)"
```

---

## Task 8: Per-user CSV export download

**Files:**
- Modify: `app/api.py` (`export_csv` gains an optional `out_dir`)
- Modify: `web/server.py` (add `GET /api/export_csv`)
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing test (append to `tests/test_web.py`)**

```python
def test_export_csv_downloads(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        _login(c, "alice", "pw1")
        r = c.get("/api/export_csv?month=2026-06")
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]
        assert r.text.splitlines()[0].startswith("date,amount_ils")
        # the file was written under alice's dir, not the shared repo exports/
        assert (tmp_path / "alice" / "exports" / "moneypilot-2026-06.csv").exists()


def test_export_csv_requires_auth(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        r = c.get("/api/export_csv?month=2026-06", follow_redirects=False)
        assert r.status_code == 303
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py::test_export_csv_downloads -v`
Expected: FAIL — currently 403 (export_csv not allowlisted, no dedicated route).

- [ ] **Step 3: Make `Api.export_csv` accept an optional `out_dir`**

In `app/api.py`, change the signature and the output-dir line. Old:
```python
    @_safe
    def export_csv(self, month: str):
        y, m = map(int, month.split("-"))
        start = dt.date(y, m, 1)
        end = (dt.date(y + 1, 1, 1) if m == 12
               else dt.date(y, m + 1, 1)) - dt.timedelta(days=1)
        rows = db.list_transactions(self.conn, start=start, end=end)
        out_dir = Path(__file__).resolve().parent.parent / "exports"
```
New:
```python
    @_safe
    def export_csv(self, month: str, out_dir=None):
        y, m = map(int, month.split("-"))
        start = dt.date(y, m, 1)
        end = (dt.date(y + 1, 1, 1) if m == 12
               else dt.date(y, m + 1, 1)) - dt.timedelta(days=1)
        rows = db.list_transactions(self.conn, start=start, end=end)
        out_dir = (Path(out_dir) if out_dir is not None
                   else Path(__file__).resolve().parent.parent / "exports")
```

(Desktop callers pass no `out_dir`, so behavior is unchanged.)

- [ ] **Step 4: Add the download route in `web/server.py`**

Add this route inside `create_app`, just before the `app.mount(...)` line:
```python
    @app.get("/api/export_csv")
    async def export_csv(request: Request, month: str):
        user = request.session.get("user")
        if not user:
            return RedirectResponse("/login", status_code=303)
        api = registry.get_api(user)
        out_dir = registry.user_dir(user) / "exports"
        res = await run_in_threadpool(api.export_csv, month, str(out_dir))
        if not res.get("ok"):
            return JSONResponse(res, status_code=400)
        return FileResponse(res["path"], media_type="text/csv",
                            filename=f"moneypilot-{month}.csv")
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py -v`
Expected: PASS. Also run `.venv\Scripts\python.exe -m pytest tests/test_api.py -v` to confirm the desktop `export_csv` is unaffected.

- [ ] **Step 6: Commit**

```bash
git add app/api.py web/server.py tests/test_web.py
git commit -m "feat(web): per-user CSV export download route"
```

---

## Task 9: Login throttle

**Files:**
- Modify: `web/server.py` (add `LoginThrottle`, wire into `/login`)
- Test: `tests/test_web.py`

- [ ] **Step 1: Write the failing tests (append to `tests/test_web.py`)**

```python
def test_login_throttle_unit():
    from web.server import LoginThrottle
    clock = {"t": 0.0}
    th = LoginThrottle(max_fails=3, window_s=100, now_fn=lambda: clock["t"])
    for _ in range(3):
        assert not th.blocked("1.2.3.4")
        th.record_fail("1.2.3.4")
    assert th.blocked("1.2.3.4")
    clock["t"] = 101.0                     # window elapsed
    assert not th.blocked("1.2.3.4")


def test_login_throttle_integration(tmp_path):
    with TestClient(_make_app(tmp_path)) as c:
        for _ in range(5):
            _login(c, "alice", "WRONG")
        r = _login(c, "alice", "pw1")      # correct, but should be blocked now
        assert "throttled" in r.headers["location"]
        assert c.post("/api/get_overview", json=[]).status_code == 401
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py::test_login_throttle_unit -v`
Expected: FAIL — `cannot import name 'LoginThrottle'`.

- [ ] **Step 3: Implement and wire the throttle in `web/server.py`**

Add the import at the top: `import time`.

Add the class near the top (after the constants):
```python
class LoginThrottle:
    """In-memory per-IP failure counter. Fine for a 2-3 user app."""

    def __init__(self, *, max_fails=5, window_s=300, now_fn=time.monotonic):
        self.max_fails = max_fails
        self.window_s = window_s
        self.now = now_fn
        self._fails: dict[str, list[float]] = {}

    def _recent(self, ip):
        cutoff = self.now() - self.window_s
        ts = [t for t in self._fails.get(ip, []) if t >= cutoff]
        self._fails[ip] = ts
        return ts

    def blocked(self, ip):
        return len(self._recent(ip)) >= self.max_fails

    def record_fail(self, ip):
        self._recent(ip).append(self.now())

    def reset(self, ip):
        self._fails.pop(ip, None)
```

In `create_app`, after `registry = Registry(base_dir)` add:
```python
    throttle = LoginThrottle()
```

Replace the `login` POST handler body with:
```python
    @app.post("/login")
    async def login(request: Request):
        ip = request.client.host if request.client else "?"
        if throttle.blocked(ip):
            return RedirectResponse("/login?error=throttled", status_code=303)
        form = await request.form()
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        if store.verify(username, password):
            throttle.reset(ip)
            request.session["user"] = username
            return RedirectResponse("/", status_code=303)
        throttle.record_fail(ip)
        return RedirectResponse("/login?error=1", status_code=303)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_web.py -v`
Expected: PASS (all web tests).

- [ ] **Step 5: Commit**

```bash
git add web/server.py tests/test_web.py
git commit -m "feat(web): per-IP login throttle"
```

---

## Task 10: Launcher, gitignore, README

**Files:**
- Create: `web/run.ps1`
- Modify: `.gitignore`
- Modify: `README.md`

- [ ] **Step 1: Append to `.gitignore`**

```
# web build — never commit credentials or per-user ledgers
users/
web/secret.key
```

- [ ] **Step 2: Create `web/run.ps1`**

```powershell
#requires -Version 5
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"
$port = 8000

if (-not (Get-Command cloudflared -ErrorAction SilentlyContinue)) {
  Write-Host "cloudflared not found. Install it once with:" -ForegroundColor Yellow
  Write-Host "  winget install --id Cloudflare.cloudflared" -ForegroundColor Yellow
  exit 1
}

Write-Host "Starting MoneyPilot Web on http://127.0.0.1:$port ..."
$uv = Start-Process -PassThru -NoNewWindow $py -ArgumentList `
  "-m","uvicorn","web.server:get_app","--factory","--host","127.0.0.1","--port","$port"
try {
  Write-Host "Opening Cloudflare tunnel — share the https://<random>.trycloudflare.com URL it prints below."
  cloudflared tunnel --url "http://127.0.0.1:$port"
} finally {
  if ($uv -and -not $uv.HasExited) { Stop-Process -Id $uv.Id -Force }
  Write-Host "Server stopped."
}
```

- [ ] **Step 3: Add a README section**

Append to `README.md`:
```markdown
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
```

- [ ] **Step 4: Verify launcher syntax (no tunnel needed)**

Run: `powershell -NoProfile -Command "Get-Command cloudflared -ErrorAction SilentlyContinue | Out-Null; echo 'parse-ok'"`
And confirm the venv can import the app: `.venv\Scripts\python.exe -c "from web.server import get_app; get_app(); print('app ok')"`
Expected: `app ok` (creates `web/secret.key` + `users/` on first run — both gitignored).

- [ ] **Step 5: Commit**

```bash
git add web/run.ps1 .gitignore README.md
git commit -m "feat(web): run.ps1 launcher, gitignore secrets, README share section"
```

---

## Task 11: Full regression + browser verification

**Files:** none (verification only)

- [ ] **Step 1: Run the entire suite**

Run: `.venv\Scripts\python.exe -m pytest -q`
Expected: all tests PASS (the original 188 + the new web tests). Record the count.

- [ ] **Step 2: Confirm the desktop app is unaffected**

Run (manual): `.venv\Scripts\python.exe -m app --dev`
Expected: the cockpit loads and works exactly as before (it's `file://`, so `WEB` is false and it uses the pywebview bridge). Close it.

- [ ] **Step 3: Browser verification (house technique)**

Start the server with the AI mocked off (offline parser) so no real Claude calls fire:
Run: `.venv\Scripts\python.exe -m web.users add demo` (password `demo`), then
`.venv\Scripts\python.exe -m uvicorn web.server:get_app --factory --port 8000`.
Using the Playwright MCP browser:
- Navigate to `http://127.0.0.1:8000/` → expect redirect to `/login`; screenshot the login page.
- Log in as `demo`/`demo` → expect the cockpit; complete onboarding (or seed a couple entries) and screenshot each tab.
- Open a second browser context, add a `demo2` user, log in, confirm its ledger is empty/independent; screenshot.
- Click Export on the ledger tab → confirm a `moneypilot-YYYY-MM.csv` download starts.
Stop the server.

- [ ] **Step 4: Send screenshots to the user as the visual approval gate**

Deliver: login page, each cockpit tab over HTTP, and the two-user isolation shot.

- [ ] **Step 5: Commit any verification fixes**

```bash
git add -A
git commit -m "test(web): full regression green; browser-verified login + isolation"
```

---

## Self-review notes (author check against the spec)

- **Spec coverage:** framework (Task 1,4) · `web/server.py` dispatcher + allowlist (4) · `web/auth.py` (2) · `web/registry.py` per-user ledger (3) · `web/users.py` CLI (7) · `web/run.ps1` + tunnel (10) · dual-mode `api()` + login page (5,6) · export download (8) · security: secret key (4), cookie flags (4), throttle (9), method allowlist (4), per-user isolation (3, tested in 4 and 8) · tests pytest + browser (all tasks + 11) · README (10) · gitignore (10). All spec sections map to a task.
- **As-built deviations from the spec (intentional, justified):**
  1. Web-mode detection uses `location.protocol` rather than an injected `window.__MP_WEB__` flag — simpler, no serve-time HTML rewrite, and harmless if raw `index.html` is fetched. Same goal (desktop path untouched).
  2. `app/api.py:export_csv` gains an optional `out_dir` (non-breaking default) so each user's export lands under `users/<name>/exports/` instead of a shared repo dir — this is required to honor the data-isolation goal, so the "api.py untouched" line in the spec is relaxed by one safe parameter.
- **Placeholder scan:** none — every code/test step contains complete content.
- **Type/name consistency:** `create_app(base_dir, users_path, secret_key)`, `Registry.get_api/user_dir`, `UserStore.add/verify/exists/remove/list`, `LoginThrottle.blocked/record_fail/reset`, and `get_app()` are referenced identically across tasks 2-11.
```
