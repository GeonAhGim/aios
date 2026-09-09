"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 95, §9 L18 — aggregation.py.

8.2-C: 여러 실행(execution)의 포지션·현금을 심볼별/전략별/총 노출로 집계하는
순수 함수. `PortfolioAggregate`는 L17(`state_input.py`)이 `exposures` 필드
타입으로 먼저 필요로 해서 이미 그쪽에 정의돼 있다 — 여기서 재정의하지 않고
그대로 가져와 쓴다. 이 파일이 새로 추가하는 것은 `ExecutionExposure` 입력
타입과 `aggregate()` 하나뿐이다.

`aggregate()`는 `total_equity`를 인자로 받지 않는다 — 공개 계약이
`(exposures, cash, as_of)` 세 인자뿐이므로, 총자산은 "현금 + 노출 합"으로
이 함수가 직접 정의한다. 모든 퍼센트는 각 그룹 노출 합을 그 `total_equity`
하나로 나눈 값이고 `total_exposure_pct`/`cash_pct`도 같은 나눗셈의 재결합일
뿐이므로, 반올림 없이(§9 DoD (b)) 합산 항등식이 대수적으로 성립한다 — 단
나눗셈 자체가 유한소수로 딱 떨어지는 입력이라는 전제에서다(순환소수 입력은
`Decimal` 컨텍스트 정밀도(기본 28자리)에서 반올림돼 항등식이 근사값이 될 수
있음 — 이 계약은 그 경우를 다루지 않는다).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, field_validator

from src.core.portfolio.state_input import PortfolioAggregate

_HUNDRED = Decimal("100")


def _reject_float(value: Any) -> Any:
    if isinstance(value, float):
        raise ValueError("float is not accepted here — pass Decimal")
    return value


class AggregationError(ValueError):
    """8.2-C 집계 입력이 정의된 계약을 벗어났다 — `ZeroDivisionError` 등
    미정의 예외로 새어나가지 않도록 여기서 fail-closed로 거부한다."""


class MissingAsOfError(AggregationError):
    """`as_of`가 `None`으로 들어왔다 — "지금"으로 임의 대체하지 않고 호출자가
    명시적으로 시점을 넘기도록 강제한다(재현성 R1)."""


class NonPositiveEquityError(AggregationError):
    """`cash + Σnotional`(총자산)이 0 이하 — 퍼센트의 분모가 없거나 음수인
    상태이므로 `ZeroDivisionError`나 음수 퍼센트로 새어나가는 대신 거부한다."""


class ExecutionExposure(BaseModel):
    execution_id: int
    strategy_id: str
    symbol: str
    notional: Decimal
    vol_pct: Decimal | None = None

    @field_validator("notional", "vol_pct", mode="before")
    @classmethod
    def _no_float(cls, value: Any) -> Any:
        return _reject_float(value)


def aggregate(
    exposures: Sequence[ExecutionExposure], cash: Decimal, as_of: datetime
) -> PortfolioAggregate:
    """여러 실행의 `notional`을 심볼/전략별로 묶어 퍼센트로 환산한다.

    `total_equity = cash + Σ notional`. 심볼별/전략별 그룹 합은 전체 노출
    합의 재분배일 뿐이므로, 각 그룹 퍼센트를 더하면 `total_exposure_pct`와
    같고 `cash_pct + total_exposure_pct`도 100과 같다(모듈 docstring 참고).
    """
    if as_of is None:
        raise MissingAsOfError("as_of는 필수입니다 — 기본값(현재 시각)으로 대체하지 않습니다.")

    total_notional = Decimal("0")
    per_symbol_notional: dict[str, Decimal] = {}
    per_strategy_notional: dict[str, Decimal] = {}
    for exposure in exposures:
        total_notional += exposure.notional
        per_symbol_notional[exposure.symbol] = (
            per_symbol_notional.get(exposure.symbol, Decimal("0")) + exposure.notional
        )
        per_strategy_notional[exposure.strategy_id] = (
            per_strategy_notional.get(exposure.strategy_id, Decimal("0")) + exposure.notional
        )

    total_equity = cash + total_notional
    if total_equity <= 0:
        raise NonPositiveEquityError(
            f"total_equity(cash+총노출)={total_equity}는 0 이하입니다 — "
            "퍼센트를 정의할 수 없어 집계를 거부합니다."
        )

    def _pct(notional: Decimal) -> Decimal:
        return notional / total_equity * _HUNDRED

    per_symbol_pct = {symbol: _pct(notional) for symbol, notional in per_symbol_notional.items()}
    per_strategy_pct = {
        strategy_id: _pct(notional) for strategy_id, notional in per_strategy_notional.items()
    }

    return PortfolioAggregate(
        total_equity=total_equity,
        per_symbol_pct=per_symbol_pct,
        per_strategy_pct=per_strategy_pct,
        total_exposure_pct=_pct(total_notional),
        cash_pct=_pct(cash),
        as_of=as_of,
    )
