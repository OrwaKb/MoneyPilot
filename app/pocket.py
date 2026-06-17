"""Ingest queued entries from MoneyPilot Pocket (the phone capture app) into the
home ledger. Idempotent by client_uuid so re-syncs never double-count.

The phone sends raw text ("45 falafel"); the authoritative parse/categorize
happens here through the SAME pipeline as the entry bar, tagged source="pocket".
"""
from __future__ import annotations

import datetime as dt
import secrets
import shutil
import subprocess
from pathlib import Path

from app import db
from app.ai import parser


def get_token(conn) -> str:
    """The phone's pairing secret (stored in settings; created on first use)."""
    tok = db.get_setting(conn, "pocket_token")
    if not tok:
        tok = secrets.token_urlsafe(24)
        db.set_setting(conn, "pocket_token", tok)
    return tok

_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _already(conn, uuid: str) -> bool:
    return conn.execute("SELECT 1 FROM transactions WHERE client_uuid=?",
                        (uuid,)).fetchone() is not None


def _entry_date(created_at, today: dt.date) -> dt.date:
    """Date the phone logged it (its local clock), falling back to today."""
    try:
        s = str(created_at).replace("Z", "+00:00")
        return dt.datetime.fromisoformat(s).date()
    except (ValueError, TypeError):
        return today


def _store_pocket(conn, raw: str, when: dt.date, client_uuid: str) -> None:
    """Parse `raw` and store it (source=pocket), carrying client_uuid on the
    first resulting row so dedupe works even if one note yields several txns.
    Raises ValueError if no amount can be found (caller acks without storing)."""
    fast = parser._fast_path(conn, raw, when)
    if fast is not None:
        parser._store(conn, fast, raw_text=raw, source="pocket",
                      client_uuid=client_uuid)
        return
    try:
        parsed = parser._ai_parse(conn, raw, when)
    except Exception:
        parsed = parser.fallback_parse(raw, when, parser._default_method(conn))
    for i, p in enumerate(parsed):
        parser._store(conn, p, raw_text=raw, source="pocket",
                      client_uuid=(client_uuid if i == 0 else None))


def ingest(conn, entries, today: dt.date) -> list[str]:
    """entries: [{uuid, raw_text, created_at, ...}]. Returns the uuids now in the
    ledger (newly stored + already present) — the phone marks those synced.
    A transient failure leaves the uuid OUT so the phone retries it next time."""
    synced: list[str] = []
    for e in entries or []:
        uuid = str((e or {}).get("uuid") or "").strip()
        raw = str((e or {}).get("raw_text") or "").strip()
        if not uuid:
            continue
        if _already(conn, uuid):
            synced.append(uuid)          # idempotent: already ingested
            continue
        if not raw:
            synced.append(uuid)          # nothing to log; ack so it stops retrying
            continue
        try:
            _store_pocket(conn, raw, _entry_date(e.get("created_at"), today), uuid)
            synced.append(uuid)
        except ValueError:
            # no amount in the note → can't be a transaction; ack it (don't loop)
            synced.append(uuid)
        except Exception:
            # transient (DB busy, etc.) → leave unsynced; phone retries later
            pass
    return synced


def tailscale_url() -> str | None:
    """Best-effort HTTPS URL of this machine on the tailnet, for the pairing
    screen. None if Tailscale isn't installed/up."""
    exe = shutil.which("tailscale")
    if not exe:
        for c in (r"C:\Program Files\Tailscale\tailscale.exe",
                  r"C:\Program Files (x86)\Tailscale\tailscale.exe"):
            if Path(c).exists():
                exe = c
                break
    if not exe:
        return None
    try:
        import json
        res = subprocess.run([exe, "status", "--json"], capture_output=True,
                             text=True, timeout=8, creationflags=_CREATE_NO_WINDOW)
        name = (json.loads(res.stdout).get("Self") or {}).get("DNSName") or ""
    except Exception:
        return None
    name = name.rstrip(".")
    return f"https://{name}" if name else None
