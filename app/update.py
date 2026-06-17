"""In-app update check against GitHub Releases.

Fail-silent by design: a placeholder repo, no network, an HTTP/JSON error, or a
release that isn't newer all return {"update_available": False}. The caller (the
launch-time banner) must never see an exception or a blocked startup.
"""
from __future__ import annotations

import logging

import requests

from app import version

log = logging.getLogger("moneypilot.update")

_PLACEHOLDER = "YOUR_GITHUB_USERNAME"


def _repo_configured() -> bool:
    repo = version.GITHUB_REPO or ""
    return "/" in repo and _PLACEHOLDER not in repo


def _parse_version(s: str) -> tuple[int, ...]:
    """'v1.2.3' / '1.2.3' / '1.2' -> tuple of ints. Anything non-numeric -> ()."""
    s = (s or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    parts = []
    for p in s.split("."):
        if not p.isdigit():
            return ()
        parts.append(int(p))
    return tuple(parts)


def _is_newer(latest: str, current: str) -> bool:
    lv, cv = _parse_version(latest), _parse_version(current)
    if not lv:                       # unparseable remote tag -> never prompt
        return False
    if not cv:                       # unparseable local -> treat remote as newer
        return True
    n = max(len(lv), len(cv))
    return lv + (0,) * (n - len(lv)) > cv + (0,) * (n - len(cv))


def check_for_update(current: str | None = None, timeout_s: int = 5) -> dict:
    """Return {"update_available": bool, [version, url, notes]}. Never raises."""
    current = current or version.__version__
    if not _repo_configured():
        return {"update_available": False}
    try:
        r = requests.get(version.UPDATE_API_URL, timeout=timeout_s,
                         headers={"Accept": "application/vnd.github+json",
                                  "User-Agent": "MoneyPilot"})
        r.raise_for_status()
        data = r.json()
    except Exception as e:           # network / HTTP / JSON — stay quiet
        log.info("update check skipped: %s", e)
        return {"update_available": False}
    latest = str(data.get("tag_name") or "")
    if not _is_newer(latest, current):
        return {"update_available": False}
    # Prefer a direct .zip asset; fall back to the release page.
    url = data.get("html_url")
    for a in (data.get("assets") or []):
        if str(a.get("name", "")).lower().endswith(".zip"):
            url = a.get("browser_download_url") or url
            break
    return {"update_available": True,
            "version": latest.lstrip("vV"),
            "url": url,
            "notes": (data.get("body") or "").strip()[:500]}
