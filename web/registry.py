# web/registry.py
from __future__ import annotations

import re
import threading
from pathlib import Path

from app import db
from app.api import Api

_VALID = re.compile(r"^[a-z0-9_-]{1,32}$")


def valid_username(name: str) -> bool:
    """True if `name` is safe as a per-user directory / store key: 1-32 chars
    of lowercase letters, digits, hyphen or underscore. Shared by the CLI so a
    name that can't map to a users/<name>/ dir is rejected at creation, not
    after it has been used to log in."""
    return bool(_VALID.match(name or ""))


class Registry:
    """Per-request Api/connections, each bound to users/<name>/ledger.db.

    A user's schema is created once; every request then gets its OWN sqlite
    connection (WAL + busy_timeout). That replaces the old single-shared-
    connection-plus-lock model, under which any AI-bound request (add_entry,
    chat_send, get_briefing, …) held a per-user lock through its network call
    and froze that user's other requests — the Cloudflare "context canceled"
    storm. Independent connections need no serialization lock."""

    def __init__(self, base_dir):
        self.base = Path(base_dir)
        self._initialized: set[str] = set()
        self._lock = threading.Lock()

    def user_dir(self, username: str) -> Path:
        if not valid_username(username):
            raise ValueError(f"invalid username: {username!r}")
        return self.base / username

    def _ensure_initialized(self, ud: Path, username: str) -> None:
        """Create the user's ledger schema once (idempotent), so per-request
        connections can skip the DDL and just open."""
        with self._lock:
            if username in self._initialized:
                return
            conn = db.connect(ud / "ledger.db")
            db.init_db(conn)
            conn.close()
            self._initialized.add(username)

    def fresh_api(self, username: str) -> Api:
        """An Api on its OWN connection for ONE request. Per-request connections
        let a slow AI call run without blocking this user's other requests — no
        shared connection, no serialization lock. The caller MUST close
        ``api.conn`` when the request finishes."""
        ud = self.user_dir(username)          # validates the name first
        self._ensure_initialized(ud, username)
        return Api(ud / "ledger.db", backup_dir=ud / "backups", init=False)
