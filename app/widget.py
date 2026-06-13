"""MoneyPilot always-on widget — standalone process on the same ledger.

  pythonw -m app.widget          normal launch (real ledger, frameless, on-top)
  python  -m app.widget --dev    seeded dev ledger + DevTools, console visible
"""
from __future__ import annotations

from app.api import Api


class WidgetApi:
    """Thin widget-facing bridge: composes a real `Api` and exposes only the
    widget's surface. Delegated methods inherit `Api`'s `_safe` dict contract;
    `is_onboarded` is wrapped so every bridge call returns a dict."""

    def __init__(self, api: Api):
        self._api = api
        self._window = None      # set after the window is created
        self._ddir = None        # data dir, for open_main_app / geometry

    # --- delegated cockpit surface ------------------------------------------
    def get_overview(self):
        return self._api.get_overview()

    def add_entry(self, text: str):
        return self._api.add_entry(text)

    def undo_txn(self, txn_id):
        return self._api.undo_txn(int(txn_id))

    def is_onboarded(self):
        return {"ok": True, "onboarded": self._api.is_onboarded()}
