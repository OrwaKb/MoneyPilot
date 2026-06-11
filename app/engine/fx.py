from __future__ import annotations

import datetime as dt
import json
from decimal import Decimal

import requests

from app import db
from app.models import to_agorot

RATES_URL = "https://api.frankfurter.app/latest?base=ILS"
FALLBACK_RATES = {"USD": 3.7, "EUR": 4.0, "GBP": 4.7}  # 1 unit → ILS
MAX_AGE_DAYS = 7


def _fetch() -> dict:
    """Fetch ILS-base rates and invert them to '1 foreign unit = X ILS'."""
    resp = requests.get(RATES_URL, timeout=5)
    resp.raise_for_status()
    data = resp.json()["rates"]  # e.g. {"USD": 0.27} meaning 1 ILS = 0.27 USD
    return {cur: round(1.0 / v, 4) for cur, v in data.items() if v}


def get_rates(conn, today: dt.date) -> dict:
    raw = db.get_setting(conn, "fx_rates_json")
    stored_rates = None
    if raw:
        try:
            stored = json.loads(raw)
            stored_rates = stored["rates"]
            if not isinstance(stored_rates, dict):
                raise ValueError("rates not a dict")
            age = (today - dt.date.fromisoformat(stored["fetched"])).days
            if age <= MAX_AGE_DAYS:
                return stored_rates
        except (ValueError, KeyError, TypeError):
            stored_rates = None  # corrupt cache: refetch below
    try:
        rates = _fetch()
        db.set_setting(conn, "fx_rates_json",
                       json.dumps({"fetched": today.isoformat(), "rates": rates}))
        return rates
    except Exception:
        if stored_rates is not None:
            return stored_rates  # stale beats nothing
        return dict(FALLBACK_RATES)


def to_ils(amount: float, currency: str, rates: dict):
    """→ (agorot, fx_rate_used). ILS passes through with rate None."""
    if currency == "ILS":
        return to_agorot(amount), None
    rate = rates.get(currency) or FALLBACK_RATES.get(currency)
    if rate is None:
        raise ValueError(f"no FX rate for {currency}")
    return to_agorot(Decimal(str(amount)) * Decimal(str(rate))), rate
