import datetime as dt

import pytest
from pydantic import ValidationError

from app.models import ParsedTxn, fmt_ils, to_agorot


def test_to_agorot_int():
    assert to_agorot(45) == 4500

def test_to_agorot_float_half_up():
    assert to_agorot("10.255") == 1026
    assert to_agorot(10.25) == 1025

def test_fmt_ils_whole():
    assert fmt_ils(4500) == "₪45"

def test_fmt_ils_cents_and_thousands():
    assert fmt_ils(123456) == "₪1,234.56"

def test_fmt_ils_negative():
    assert fmt_ils(-4500) == "-₪45"

def test_parsed_txn_defaults():
    p = ParsedTxn(effective_date=dt.date(2026, 6, 11), amount=45,
                  description="falafel")
    assert p.direction == "expense" and p.currency == "ILS"
    assert p.payment_method == "card" and p.confidence == 1.0

def test_parsed_txn_rejects_nonpositive_amount():
    with pytest.raises(ValidationError):
        ParsedTxn(effective_date=dt.date(2026, 6, 11), amount=0, description="x")

def test_parsed_txn_rejects_bad_direction():
    with pytest.raises(ValidationError):
        ParsedTxn(effective_date=dt.date(2026, 6, 11), amount=1,
                  description="x", direction="loan")

def test_parsed_txn_normalizes_currency():
    p = ParsedTxn(effective_date=dt.date(2026, 6, 11), amount=1,
                  description="x", currency="usd")
    assert p.currency == "USD"


def test_parsed_txn_rejects_infinite_amount():
    with pytest.raises(ValidationError):
        ParsedTxn(effective_date=dt.date(2026, 6, 11), amount=float("inf"),
                  description="x")

def test_parsed_txn_currency_alias_nis():
    p = ParsedTxn(effective_date=dt.date(2026, 6, 11), amount=1,
                  description="x", currency="nis")
    assert p.currency == "ILS"

def test_parsed_txn_rejects_junk_currency():
    with pytest.raises(ValidationError):
        ParsedTxn(effective_date=dt.date(2026, 6, 11), amount=1,
                  description="x", currency="dollars")

def test_to_agorot_junk_raises_valueerror():
    with pytest.raises(ValueError):
        to_agorot("45 ILS")

def test_fmt_ils_negative_cents():
    assert fmt_ils(-50) == "-₪0.50"
