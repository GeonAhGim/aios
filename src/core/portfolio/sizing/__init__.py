"""L4_strategy_portfolio_backtest_v1.0.md#§2 rows 90~94 — sizing package.

`SizingResult`/`PortfolioSizingError`는 개념상 §2 row 87의
`core.portfolio.models` MINOR 확장(SizingResult DTO)에 속하지만, L17
(task-2452)의 실제 커밋은 그 확장을 하지 않았고, 이 리프(L19, task-2525)의
파일 범위는 `sizing/**`로 고정돼 있다 — 그래서 사이징 패키지가 자신의 공개
결과 타입·공용 예외를 여기서 직접 소유한다(범위 밖 `models.py`는 건드리지
않는다).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, TypeVar

from pydantic import BaseModel

from src.core.portfolio.config import SizingMethod
from src.core.risk.hashing import canonical_json, sha256_hex

SCHEMA_VERSION = "sizing-v1"
HUNDRED = Decimal("100")

_T = TypeVar("_T")


class PortfolioSizingError(ValueError):
    """사이징 입력이 계약을 벗어났다 — fail-closed 기본 예외."""

    code = "PORTFOLIO_SIZING_ERROR"


class SizingInputMissingError(PortfolioSizingError):
    """필수 입력이 `None` — 0/기본값으로 대체하지 않고 거부한다."""

    code = "PORTFOLIO_SIZING_INPUT_MISSING"

    def __init__(self, field: str) -> None:
        self.field = field
        super().__init__(f"{self.code}: required input '{field}' is missing")


class SizingInputInvalidError(PortfolioSizingError):
    """입력값이 존재하지만 산식이 정의되지 않는 범위(예: `price <= 0`)."""

    code = "PORTFOLIO_SIZING_INPUT_INVALID"

    def __init__(self, field: str, reason: str) -> None:
        self.field = field
        super().__init__(f"{self.code}: {field} {reason}")


class SizingResult(BaseModel):
    schema_version: str = SCHEMA_VERSION
    quantity: Decimal
    weight_pct: Decimal
    method: SizingMethod
    inputs_hash: str


def require(value: _T | None, field: str) -> _T:
    """`None` 입력을 여기서 걸러 각 산식 파일에서 반복하지 않게 한다."""
    if value is None:
        raise SizingInputMissingError(field)
    return value


def require_positive(value: Decimal, field: str) -> Decimal:
    if value <= 0:
        raise SizingInputInvalidError(field, "must be > 0")
    return value


def hash_inputs(method: SizingMethod, **fields: Any) -> str:
    """R1: 같은 산식·같은 입력이면 항상 같은 `inputs_hash`."""
    payload = {"method": method.value, **fields}
    return sha256_hex(canonical_json(payload))
