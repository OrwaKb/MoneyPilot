import datetime as dt

import pytest

from app import db
from app.api import Api
from app.widget import WidgetApi

TODAY = dt.date(2026, 6, 11)

GOOD_REPLY = (
    '[{"effective_date": "2026-06-11", "amount": 45, "currency": "ILS",'
    ' "direction": "expense", "category": "Food out", "description": "falafel",'
    ' "merchant": null, "people": null, "payment_method": "card",'
    ' "goal_name": null, "confidence": 0.95}]')


@pytest.fixture
def wapi(tmp_path):
    a = Api(tmp_path / "ledger.db", backup_dir=tmp_path / "backups",
            today_fn=lambda: TODAY)
    a.save_settings({"user_name": "Tester", "salary_day": "10",
                     "salary_amount_agorot": "900000", "card_charge_day": "2",
                     "opening_balance_agorot": "500000",
                     "opening_balance_date": "2026-06-01"})
    return WidgetApi(a)


def test_get_overview_delegates_unchanged(wapi):
    assert wapi.get_overview() == wapi._api.get_overview()


def test_add_entry_and_undo_delegate(wapi, monkeypatch):
    from app.ai import parser
    monkeypatch.setattr(parser.client, "ask_claude", lambda *a, **k: GOOD_REPLY)
    res = wapi.add_entry("45 falafel")
    assert res["ok"] is True and len(res["entries"]) == 1
    tid = res["entries"][0]["id"]
    assert wapi.undo_txn(str(tid))["ok"] is True          # accepts a str id
    assert db.list_transactions(wapi._api.conn) == []      # soft-deleted


def test_is_onboarded_wrapped_in_dict(wapi, tmp_path):
    assert wapi.is_onboarded() == {"ok": True, "onboarded": True}
    # a fresh, un-onboarded ledger (no salary_day) reports False
    a2 = Api(tmp_path / "blank.db", backup_dir=tmp_path / "b2")
    assert WidgetApi(a2).is_onboarded() == {"ok": True, "onboarded": False}
