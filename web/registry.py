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
        self._dispatch_locks: dict[str, threading.Lock] = {}
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

    def dispatch_lock(self, username: str) -> threading.Lock:
        """A per-user lock serializing every request that touches that user's
        single sqlite connection. FastAPI runs the sync Api methods in a
        threadpool, so without this two concurrent same-user requests would use
        one connection from two threads and raise transaction-state errors.
        Per-user, so different users still run fully in parallel."""
        with self._lock:
            lk = self._dispatch_locks.get(username)
            if lk is None:
                lk = threading.Lock()
                self._dispatch_locks[username] = lk
            return lk
