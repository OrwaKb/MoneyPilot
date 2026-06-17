"""Filesystem paths that resolve correctly both from source and from a frozen
PyInstaller bundle.

Two kinds of path matter:
  * read-only resources (UI html/css/js, the icon) — shipped INSIDE the bundle;
    when frozen they live under ``sys._MEIPASS``, otherwise under the repo root.
  * writable per-user data (ledger, backups, log, single-instance lock) — must
    NEVER live next to the exe (a bundle dir can be read-only / a temp extract);
    it always goes to ``%LOCALAPPDATA%\\MoneyPilot``.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

#: True when running from a PyInstaller-built exe.
FROZEN = getattr(sys, "frozen", False)

# Root that read-only resources are resolved against. In a bundle that's the
# extraction dir (sys._MEIPASS); from source it's the repo root (parent of app/).
RESOURCE_ROOT = (Path(sys._MEIPASS) if FROZEN  # type: ignore[attr-defined]
                 else Path(__file__).resolve().parent.parent)

#: Back-compat alias — older modules import PROJECT_DIR from app.__main__.
PROJECT_DIR = RESOURCE_ROOT


def resource_path(*parts: str) -> Path:
    """Absolute path to a bundled read-only resource, e.g.
    ``resource_path("app", "ui", "index.html")`` — valid in both run modes."""
    return RESOURCE_ROOT.joinpath(*parts)


def data_dir() -> Path:
    """Per-user writable directory (ledger, backups, log, locks). Created lazily
    by callers. Honours %LOCALAPPDATA% with a home-dir fallback for safety."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "MoneyPilot"
