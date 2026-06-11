import datetime as dt
import json

from app import db
from app.engine import fx

TODAY = dt.date(2026, 6, 11)


def test_to_ils():
    agorot, rate = fx.to_ils(15, "USD", {"USD": 3.65})
    assert agorot == 5475 and rate == 3.65

def test_to_ils_ils_passthrough():
    agorot, rate = fx.to_ils(45, "ILS", {})
    assert agorot == 4500 and rate is None

def test_get_rates_uses_fallback_when_offline(conn, monkeypatch):
    monkeypatch.setattr(fx, "_fetch", lambda: (_ for _ in ()).throw(OSError()))
    rates = fx.get_rates(conn, TODAY)
    assert rates["USD"] > 0  # fallback table

def test_get_rates_caches_and_respects_age(conn, monkeypatch):
    calls = []
    monkeypatch.setattr(fx, "_fetch", lambda: calls.append(1) or {"USD": 3.5})
    fx.get_rates(conn, TODAY)
    fx.get_rates(conn, TODAY)          # fresh cache → no second fetch
    assert len(calls) == 1
    stored = json.loads(db.get_setting(conn, "fx_rates_json"))
    stored["fetched"] = (TODAY - dt.timedelta(days=8)).isoformat()
    db.set_setting(conn, "fx_rates_json", json.dumps(stored))
    fx.get_rates(conn, TODAY)          # stale → refetch
    assert len(calls) == 2

def test_get_rates_corrupt_cache_self_heals(conn, monkeypatch):
    db.set_setting(conn, "fx_rates_json", "{not json")
    monkeypatch.setattr(fx, "_fetch", lambda: {"USD": 3.5})
    assert fx.get_rates(conn, TODAY) == {"USD": 3.5}  # refetched + healed
    assert json.loads(db.get_setting(conn, "fx_rates_json"))["rates"] == {"USD": 3.5}

def test_get_rates_stale_beats_fallback(conn, monkeypatch):
    db.set_setting(conn, "fx_rates_json", json.dumps(
        {"fetched": (TODAY - dt.timedelta(days=30)).isoformat(),
         "rates": {"USD": 9.99}}))
    monkeypatch.setattr(fx, "_fetch", lambda: (_ for _ in ()).throw(OSError()))
    assert fx.get_rates(conn, TODAY) == {"USD": 9.99}  # stale, not FALLBACK

def test_get_rates_offline_cold_returns_fallback_table(conn, monkeypatch):
    monkeypatch.setattr(fx, "_fetch", lambda: (_ for _ in ()).throw(OSError()))
    assert fx.get_rates(conn, TODAY) == fx.FALLBACK_RATES

def test_get_rates_wrong_typed_rates_self_heals(conn, monkeypatch):
    db.set_setting(conn, "fx_rates_json", json.dumps(
        {"fetched": TODAY.isoformat(), "rates": 5}))
    monkeypatch.setattr(fx, "_fetch", lambda: {"USD": 3.5})
    assert fx.get_rates(conn, TODAY) == {"USD": 3.5}
