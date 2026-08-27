"""2.1 / 2.1b — 공통 기반 타입.

Spec: 01_data_models_v1.3.md#§1 (ProvenanceStatus),
11_implementation_rules_v1.2.md#§11.1 (Currency/Money/FXRate)
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel

from src.core.exceptions import CurrencyMismatchError


class ProvenanceStatus(str, Enum):
    """4.6-A Memory 검증 상태 — Task/Memory/Strategy 공통 사용"""

    UNVERIFIED = "UNVERIFIED"
    VERIFIED = "VERIFIED"
    DISPUTED = "DISPUTED"


class Currency(str, Enum):
    USDT = "USDT"
    KRW = "KRW"


class Money(BaseModel):
    """모든 금액 필드는 원시 Decimal이 아니라 이 타입을 쓴다."""

    amount: Decimal
    currency: Currency

    def __add__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise CurrencyMismatchError(self.currency, other.currency)
        return Money(amount=self.amount + other.amount, currency=self.currency)


class FXRate(BaseModel):
    """환율 스냅샷. 8.1-A 다중소스 교차검증과 동일한 원칙 적용 대상."""

    base: Currency
    quote: Currency
    rate: Decimal
    timestamp: datetime
    source: str
