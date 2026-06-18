# web/server.py
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse)
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from web.auth import UserStore
from web.registry import Registry, valid_username

PROJECT_DIR = Path(__file__).resolve().parent.parent
UI_DIR = PROJECT_DIR / "app" / "ui"
log = logging.getLogger("moneypilot.web")


def _session_user(request: Request):
    """The logged-in username, but only if it's a valid per-user key. A name
    that can't map to a users/<name>/ dir (hand-edited into users.json, or a
    stale pre-validation session) is treated as unauthenticated, so it never
    reaches fresh_api and 500s every request."""
    user = request.session.get("user")
    return user if (user and valid_username(user)) else None

# The exact Api methods the cockpit UI calls through api() (app.js).
# export_csv is intentionally excluded — it has a dedicated download route.
ALLOWED = {
    "startup", "get_overview", "add_entry", "undo_txn", "restore_txn",
    "update_txn", "list_ledger", "get_goals", "save_goal", "archive_goal",
    "get_briefing", "chat_send", "chat_apply_action", "get_chat_history",
    "list_chats", "delete_chat", "get_recurring", "dismiss_recurring",
    "get_app_settings", "save_settings",
    "set_category_budget", "remove_category_budget",
    "onboarding_braindump", "onboarding_complete",
}


class LoginThrottle:
    """In-memory per-username failed-login counter. Fine for a 2-3 user app.
    Keyed by username (not IP) because behind the Cloudflare tunnel every
    request arrives from 127.0.0.1, which would make an IP key one global
    bucket — a trivial lockout.

    The login handler verifies credentials BEFORE consulting this, so a correct
    password is never refused (no lockout DoS). Once a username is over its
    failed-attempt threshold, ``penalty()`` delays each further WRONG guess so
    brute force can't run at full pbkdf2 speed — the enforcement the counter
    alone used to lack."""

    def __init__(self, *, max_fails=5, window_s=300, block_delay_s=1.0,
                 now_fn=time.monotonic, sleep_fn=None):
        self.max_fails = max_fails
        self.window_s = window_s
        self.block_delay_s = block_delay_s
        self.now = now_fn
        self._sleep = sleep_fn or asyncio.sleep
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

    async def penalty(self):
        """Rate-limit a wrong guess once over the threshold. A correct password
        is verified before this is reached, so it is never delayed."""
        if self.block_delay_s:
            await self._sleep(self.block_delay_s)


def create_app(*, base_dir, users_path, secret_key,
               login_block_delay_s=1.0) -> FastAPI:
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key=secret_key,
                       https_only=True, same_site="lax")
    store = UserStore(users_path)
    registry = Registry(base_dir)
    throttle = LoginThrottle(block_delay_s=login_block_delay_s)

    @app.get("/")
    def root(request: Request):
        if not _session_user(request):
            return RedirectResponse("/login", status_code=303)
        # Inject the web-mode flag so app.js uses fetch() instead of the
        # pywebview bridge. The desktop app serves the raw index.html (no flag).
        html = (UI_DIR / "index.html").read_text(encoding="utf-8")
        html = html.replace(
            "<head>", "<head>\n<script>window.__MP_WEB__=true;</script>", 1)
        return HTMLResponse(html)

    @app.get("/login")
    def login_page():
        return FileResponse(UI_DIR / "login.html")

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        username = (form.get("username") or "").strip()
        password = form.get("password") or ""
        # Verify FIRST so a correct password is NEVER refused: a flood of bad
        # logins cannot lock out someone who knows their password. The throttle
        # is keyed by username and only flags repeated *failed* attempts.
        # Require BOTH a correct password AND a name that maps to a safe per-user
        # dir; an invalid-named account (legacy / hand-edited users.json) can't be
        # served, so don't hand it a session it would only 500 with.
        if store.verify(username, password) and valid_username(username):
            throttle.reset(username)
            request.session["user"] = username
            return RedirectResponse("/", status_code=303)
        throttle.record_fail(username)
        if throttle.blocked(username):
            # over the limit: a correct password already returned above, so this
            # is a wrong guess — delay it to throttle brute force.
            await throttle.penalty()
            return RedirectResponse("/login?error=throttled", status_code=303)
        return RedirectResponse("/login?error=1", status_code=303)

    @app.post("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    @app.post("/api/{method}")
    async def dispatch(method: str, request: Request):
        user = _session_user(request)
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

        def _call():
            # Own connection per request (created+closed on this threadpool
            # thread): a slow AI-bound call no longer blocks the user's others.
            api = registry.fresh_api(user)
            try:
                return getattr(api, method)(*args)
            finally:
                api.conn.close()

        result = await run_in_threadpool(_call)
        # Don't leak internal failure detail (SQLite text, file paths) to the
        # remote client; log it server-side and return a generic message.
        # Deliberate validation errors (error_kind == "user") pass through.
        if (isinstance(result, dict) and result.get("ok") is False
                and result.get("error_kind") == "internal"):
            log.warning("internal error in %s for %s: %s",
                        method, user, result.get("error"))
            result = {"ok": False, "error": "Something went wrong. Try again."}
        return JSONResponse(result)

    @app.get("/api/export_csv")
    async def export_csv(request: Request, month: str):
        user = _session_user(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        out_dir = registry.user_dir(user) / "exports"

        def _export():
            api = registry.fresh_api(user)
            try:
                return api.export_csv(month, str(out_dir))
            finally:
                api.conn.close()

        res = await run_in_threadpool(_export)
        if not res.get("ok"):
            return JSONResponse(res, status_code=400)
        return FileResponse(res["path"], media_type="text/csv",
                            filename=f"moneypilot-{month}.csv")

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
