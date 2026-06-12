from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

Direction = Literal["expense", "income", "goal_contribution"]
PayMethod = Literal["card", "cash", "transfer"]

_CURRENCY_ALIASES = {"NIS": "ILS", "SHEKEL": "ILS", "SHEKELS": "ILS", "₪": "ILS",
                     "$": "USD", "€": "EUR"}


def to_agorot(amount) -> int:
    """Money in → integer agorot, banker-proof (HALF_UP on the agora)."""
    try:
        return int((Decimal(str(amount)) * 100).quantize(Decimal("1"),
                   rounding=ROUND_HALF_UP))
    except InvalidOperation as e:
        raise ValueError(f"not a money amount: {amount!r}") from e


def fmt_ils(agorot: int) -> str:
    sign = "-" if agorot < 0 else ""
    shekels, ag = divmod(abs(agorot), 100)
    base = f"{sign}₪{shekels:,}"
    return base if ag == 0 else f"{base}.{ag:02d}"


class ParsedTxn(BaseModel):
    """One transaction as returned by the AI parser (or fallback parser)."""
    effective_date: dt.date
    amount: float = Field(gt=0, allow_inf_nan=False)          # in currency units, always positive
    currency: str = "ILS"
    direction: Direction = "expense"
    category: str = "Other"
    description: str = ""
    merchant: Optional[str] = None
    people: Optional[str] = None
    payment_method: PayMethod = "card"
    goal_name: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="before")
    @classmethod
    def _absorb_ai_noise(cls, data):
        """Real AI replies use null/[] for fields they have no info for.
        Drop nulls so field defaults apply (except the required core fields,
        where null must still error), and flatten a people list to a string."""
        if isinstance(data, dict):
            data = {k: v for k, v in data.items()
                    if v is not None or k in ("effective_date", "amount")}
            people = data.get("people")
            if isinstance(people, list):
                data["people"] = ", ".join(str(x) for x in people) or None
            for k in ("direction", "payment_method"):
                if isinstance(data.get(k), str):
                    data[k] = data[k].strip().lower()
        return data

    @field_validator("currency")
    @classmethod
    def _norm_currency(cls, v: str) -> str:
        v = v.strip().upper()
        v = _CURRENCY_ALIASES.get(v, v)
        if len(v) != 3 or not v.isalpha():
            raise ValueError(f"not a currency code: {v!r}")
        return v
