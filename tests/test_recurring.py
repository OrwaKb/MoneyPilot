import datetime as dt
import json

from app import db
from app.engine import recurring

TODAY = dt.date(2026, 6, 20)


def _add(conn, date, ils, merchant="Netflix", description="netflix sub"):
    db.add_transaction(conn, effective_date=date, amount_agorot=-int(ils * 100),
                       direction="expense", merchant=merchant, description=description)


def _series(conn, dates, ils=45.0, **kw):
    for d in dates:
        _add(conn, d, ils, **kw)


def test_detects_three_monthly_charges(conn):
    _series(conn, [dt.date(2026, 3, 21), dt.date(2026, 4, 20), dt.date(2026, 5, 20)])
    (r,) = recurring.detect(conn, TODAY)
    assert r["cadence"] == "monthly"
    assert r["typical_agorot"] == 4500
    assert r["monthly_equiv_agorot"] == 4500
    assert r["last_charged"] == "2026-05-20"
    assert r["next_expected"] == "2026-06-19"   # 2026-05-20 + 30 days
    assert r["occurrences"] == 3
    assert r["price_hike"] is False


def test_two_occurrences_not_recurring(conn):
    _series(conn, [dt.date(2026, 4, 20), dt.date(2026, 5, 20)])
    assert recurring.detect(conn, TODAY) == []


def test_irregular_gaps_rejected(conn):
    # median gap lands in the monthly window but spacing is wildly uneven
    _series(conn, [dt.date(2026, 3, 1), dt.date(2026, 3, 31), dt.date(2026, 5, 25)])
    assert recurring.detect(conn, TODAY) == []


def test_variable_amounts_rejected(conn):
    # same merchant, monthly-ish spacing, but amounts swing like a grocery run
    for d, ils in [(dt.date(2026, 3, 20), 40), (dt.date(2026, 4, 20), 230),
                   (dt.date(2026, 5, 20), 95)]:
        _add(conn, d, ils, merchant="Shufersal", description="groceries")
    assert recurring.detect(conn, TODAY) == []


def test_small_amount_variance_still_detected(conn):
    _series(conn, [dt.date(2026, 3, 21), dt.date(2026, 4, 20), dt.date(2026, 5, 20)],
            ils=45.0)
    # add a 4th within +-10%
    _add(conn, dt.date(2026, 2, 19), 48.0)
    (r,) = recurring.detect(conn, TODAY)
    assert r["cadence"] == "monthly" and r["occurrences"] == 4


def test_price_hike_flagged(conn):
    _add(conn, dt.date(2026, 3, 21), 30.0)
    _add(conn, dt.date(2026, 4, 20), 30.0)
    _add(conn, dt.date(2026, 5, 20), 45.0)   # latest is +50%
    (r,) = recurring.detect(conn, TODAY)
    assert r["price_hike"] is True
    assert r["typical_agorot"] == 3000       # median of 30/30/45


def test_annual_cadence(conn):
    _series(conn, [dt.date(2024, 6, 18), dt.date(2025, 6, 18), dt.date(2026, 6, 18)],
            ils=240.0, merchant="DomainCo", description="domain renewal")
    (r,) = recurring.detect(conn, TODAY)
    assert r["cadence"] == "annual"
    assert r["monthly_equiv_agorot"] == 2000   # round(24000 / 12)


def test_description_fallback_when_merchant_null(conn):
    _series(conn, [dt.date(2026, 3, 21), dt.date(2026, 4, 20), dt.date(2026, 5, 20)],
            merchant=None, description="gym membership")
    (r,) = recurring.detect(conn, TODAY)
    assert "gym" in r["key"]


def test_summary_monthly_total_sums_all_items(conn):
    _series(conn, [dt.date(2026, 3, 21), dt.date(2026, 4, 20), dt.date(2026, 5, 20)],
            ils=45.0, merchant="Netflix", description="netflix")
    _series(conn, [dt.date(2026, 4, 9), dt.date(2026, 5, 9), dt.date(2026, 6, 8)],
            ils=20.0, merchant="Spotify", description="spotify")
    s = recurring.summary(conn, TODAY)
    assert len(s["items"]) == 2
    assert s["monthly_total_agorot"] == 6500     # 4500 + 2000


def test_summary_upcoming_window(conn):
    _series(conn, [dt.date(2026, 3, 25), dt.date(2026, 4, 24), dt.date(2026, 5, 24)],
            merchant="Netflix", description="netflix")
    s = recurring.summary(conn, TODAY)           # next = 2026-06-23, within 7d
    assert [i["name"] for i in s["upcoming"]] == ["Netflix"]


def test_summary_excludes_far_future_from_upcoming(conn):
    _series(conn, [dt.date(2024, 6, 18), dt.date(2025, 6, 18), dt.date(2026, 6, 18)],
            ils=240.0, merchant="DomainCo", description="domain")
    s = recurring.summary(conn, TODAY)           # annual next ~2027 -> not upcoming
    assert s["upcoming"] == []
    assert len(s["items"]) == 1


def test_stale_cancelled_subscription_excluded(conn):
    # 3 clean monthly charges that STOPPED ~8 months ago — cancelled, not active.
    # In window (lookback ~25mo) but the last charge is far older than a cycle.
    _series(conn, [dt.date(2025, 8, 20), dt.date(2025, 9, 19), dt.date(2025, 10, 19)],
            merchant="OldGym", description="old gym")
    assert recurring.detect(conn, TODAY) == []


def test_dismissed_key_excluded_then_restored(conn):
    _series(conn, [dt.date(2026, 3, 21), dt.date(2026, 4, 20), dt.date(2026, 5, 20)])
    (r,) = recurring.detect(conn, TODAY)
    recurring.dismiss(conn, r["key"])
    assert recurring.detect(conn, TODAY) == []
    assert json.loads(db.get_setting(conn, "recurring_dismissed")) == [r["key"]]
    recurring.undismiss(conn, r["key"])
    assert len(recurring.detect(conn, TODAY)) == 1
