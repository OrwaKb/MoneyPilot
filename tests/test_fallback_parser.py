import datetime as dt

import pytest

from app.ai.parser import fallback_parse

TODAY = dt.date(2026, 6, 11)


def test_simple_expense():
    (p,) = fallback_parse("45 falafel with karim", TODAY)
    assert p.amount == 45 and p.direction == "expense"
    assert p.category == "Food out" and p.effective_date == TODAY
    assert p.confidence <= 0.5

def test_yesterday():
    (p,) = fallback_parse("30 coffee yesterday", TODAY)
    assert p.effective_date == TODAY - dt.timedelta(days=1)

def test_currency_detection():
    (p,) = fallback_parse("bought a game 15 dollars", TODAY)
    assert p.currency == "USD" and p.amount == 15

def test_income_hint():
    (p,) = fallback_parse("got paid 9000 salary", TODAY)
    assert p.direction == "income"

def test_multiline_gives_multiple():
    ps = fallback_parse("45 falafel\n220 fuel", TODAY)
    assert len(ps) == 2
    assert ps[1].category == "Transport"

def test_decimal_amount_and_unknown_category():
    (p,) = fallback_parse("12.50 mystery thing", TODAY)
    assert p.amount == 12.5 and p.category == "Other"

def test_no_amount_raises():
    with pytest.raises(ValueError):
        fallback_parse("had a nice day", TODAY)

def test_thousands_separator_amounts():
    (p,) = fallback_parse("1,234.56 rent", TODAY)
    assert p.amount == 1234.56 and p.category == "Bills"
    (q,) = fallback_parse("12,345 rent", TODAY)
    assert q.amount == 12345

def test_arabic_indic_digits():
    (p,) = fallback_parse("٤٥ فلافل", TODAY)
    assert p.amount == 45  # unicode digits parse; category degrades to Other

def test_multiline_amountless_line_raises():
    with pytest.raises(ValueError):
        fallback_parse("45 falafel\nbought socks no amount\n220 fuel", TODAY)
