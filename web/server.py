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
