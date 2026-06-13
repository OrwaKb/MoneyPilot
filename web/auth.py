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
