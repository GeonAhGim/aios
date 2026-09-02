"""Strategy Validation API 요청/응답 스키마 — HTTP 세부만 여기 두고, 계약
자체는 `src/foundation/validation/contracts/v1.py`를 감싼다(106번 §2)."""
from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel

from src.foundation.validation.contracts.v1 import ValidationResultView

__all__ = ["StartValidationRequest", "ValidationResultView"]


class StartValidationRequest(BaseModel):
    """76번 §1 "input snapshot ref" — bar 데이터의 출처(exchange/symbol/
    timeframe/limit)를 요청 본문에 명시적으로 고정한다. 실제 bar 조회는
    기존 `/strategy-builder/candles`와 동일하게 CredentialResolver +
    거래소 adapter를 재사용한다(신규 데이터 경로 없음)."""

    exchange: str
    symbol: str
    timeframe: str = "1h"
    limit: int = 200
    cost_model_fee_bps: Decimal
    cost_model_slippage_bps: Decimal
    warmup_bars: int = 0
    periods_per_year: int = 252
    initial_equity: Decimal = Decimal("10000")
