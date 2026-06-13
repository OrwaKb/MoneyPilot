import datetime as dt

import pytest
from pydantic import ValidationError

from app.models import ParsedTxn, fmt_ils, to_agorot, to_whole_agorot


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

def test_parsed_txn_absorbs_ai_null_noise():
    # Real Claude replies use null/[] for fields they have no info for;
    # nulls must fall back to the field defaults instead of erroring.
    p = ParsedTxn(effective_date=dt.date(2026, 6, 12), amount=45,
                  currency=None, category=None, description=None,
                  merchant=None, people=[], payment_method=None,
                  goal_name=None, confidence=None)
    assert p.payment_method == "card" and p.category == "Other"
    assert p.currency == "ILS" and p.description == ""
    assert p.people is None and p.confidence == 1.0

def test_parsed_txn_joins_people_list():
    p = ParsedTxn(effective_date=dt.date(2026, 6, 12), amount=45,
                  people=["karim", "sara"])
    assert p.people == "karim, sara"

def test_parsed_txn_normalizes_enum_case():
    p = ParsedTxn(effective_date=dt.date(2026, 6, 12), amount=45,
                  direction="Expense", payment_method="CARD")
    assert p.direction == "expense" and p.payment_method == "card"

def test_parsed_txn_still_rejects_missing_required():
    with pytest.raises(ValidationError):
        ParsedTxn(effective_date=None, amount=45)  # date stays required
    with pytest.raises(ValidationError):
        ParsedTxn(effective_date=dt.date(2026, 6, 12), amount=None)


# --- to_whole_agorot: First Flight amounts are whole shekels only -------------------

def test_to_whole_agorot_int():
    assert to_whole_agorot(12) == 1200

def test_to_whole_agorot_accepts_whole_float_and_str():
    assert to_whole_agorot(12.0) == 1200
    assert to_whole_agorot("12") == 1200
    assert to_whole_agorot("  12  ") == 1200

def test_to_whole_agorot_rejects_fraction():
    with pytest.raises(ValueError):
        to_whole_agorot(12.5)
    with pytest.raises(ValueError):
        to_whole_agorot("47.9")

def test_to_whole_agorot_rejects_zero_by_default():
    with pytest.raises(ValueError):
        to_whole_agorot(0)

def test_to_whole_agorot_allows_zero_when_opted_in():
    assert to_whole_agorot(0, allow_zero=True) == 0

def test_to_whole_agorot_rejects_negative_even_when_zero_allowed():
    with pytest.raises(ValueError):
        to_whole_agorot(-3)
    with pytest.raises(ValueError):
        to_whole_agorot(-3, allow_zero=True)

def test_to_whole_agorot_rejects_blank_and_junk():
    with pytest.raises(ValueError):
        to_whole_agorot("")
    with pytest.raises(ValueError):
        to_whole_agorot(None)
    with pytest.raises(ValueError):
        to_whole_agorot("nine")

def test_to_whole_agorot_rejects_non_finite():
    with pytest.raises(ValueError):
        to_whole_agorot(float("inf"))
