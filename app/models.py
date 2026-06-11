from __future__ import annotations

import datetime as dt
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Direction = Literal["expense", "income", "goal_contribution"]
PayMethod = Literal["card", "cash", "transfer"]


def to_agorot(amount) -> int:
    """Money in -> integer agorot, banker-proof (HALF_UP on the agora)."""
    return int((Decimal(str(amount)) * 100).quantize(Decimal("1"),
               rounding=ROUND_HALF_UP))


def fmt_ils(agorot: int) -> str:
    sign = "-" if agorot < 0 else ""
    shekels, ag = divmod(abs(agorot), 100)
    base = f"{sign}₪{shekels:,}"
    return base if ag == 0 else f"{base}.{ag:02d}"


class ParsedTxn(BaseModel):
    """One transaction as returned by the AI parser (or fallback parser)."""
    effective_date: dt.date
    amount: float = Field(gt=0)          # in currency units, always positive
    currency: str = "ILS"
    direction: Direction = "expense"
    category: str = "Other"
    description: str = ""
    merchant: Optional[str] = None
    people: Optional[str] = None
    payment_method: PayMethod = "card"
    goal_name: Optional[str] = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    @field_validator("currency")
    @classmethod
    def _norm_currency(cls, v: str) -> str:
        return v.strip().upper()[:3]
