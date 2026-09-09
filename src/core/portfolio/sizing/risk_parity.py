"""L4_strategy_portfolio_backtest_v1.0.md#§2 row 93 — risk-parity sizing.

실행 간 역변동성 가중 `w_i ∝ 1/σ_i`, 합 = 1. `PortfolioAggregate`(L18)는
전략별 노출 비중(`per_strategy_pct`)만 보존하고 개별 실행의 변동성은 버리므로,
이 리프는 다른 실행 각각의 실제 σ_j를 다시 조회할 수 없다 — 그 한계 안에서
**2-블록(신규 포지션 vs 기존 노출 총합) 단순화**를 쓴다: 기존 노출 총합
(`exposures.total_exposure_pct`)을 하나의 카운터파티로 취급하고, 신규
포지션의 몫을 그 대비 `1/σ_i` 비례로 나눈다.

`w_i = (1/σ_i) / (1/σ_i + S)`, `S = existing_fraction`이면 대수적으로
`w_i + S/(1/σ_i + S) = 1`이 항상 성립한다(§8 테스트표 "리스크패리티 합=1").
`existing_block_weight_fraction()`이 그 두 번째 항을 반환하므로 테스트가
둘의 합을 직접 검증할 수 있다.

다중 전략 각각의 개별 변동성을 보존하는 진짜 N-자산 리스크패리티는 L18이
전략별 vol 필드를 추가하기 전까지는 이 시그니처(`size(inp)`, 단일 실행 입력
하나)로 계산할 수 없다 — 미검증.
"""
from __future__ import annotations

from decimal import Decimal

from src.core.portfolio.config import SizingMethod
from src.core.portfolio.sizing import (
    HUNDRED,
    SizingResult,
    hash_inputs,
    require,
    require_positive,
)
from src.core.portfolio.state_input import PortfolioStateInput

_ONE = Decimal("1")


def _denominator(inp: PortfolioStateInput) -> tuple[Decimal, Decimal, Decimal]:
    """`(inv_vol, existing_fraction, denom)` — `size()`와
    `existing_block_weight_fraction()`이 같은 세 값을 공유해야 두 결과의
    합이 대수적으로 정확히 1이 된다."""
    exposures = require(inp.exposures, "exposures")
    realized_vol_pct = require(inp.realized_vol_pct, "realized_vol_pct")
    require_positive(realized_vol_pct, "realized_vol_pct")

    existing_fraction = exposures.total_exposure_pct / HUNDRED
    inv_vol = _ONE / realized_vol_pct
    return inv_vol, existing_fraction, inv_vol + existing_fraction


def existing_block_weight_fraction(inp: PortfolioStateInput) -> Decimal:
    """기존 노출 블록이 차지하는 몫 — `size()`가 반환하는 `weight_pct/100`과
    더하면 항상 1이다."""
    _inv_vol, existing_fraction, denom = _denominator(inp)
    return existing_fraction / denom


def size(inp: PortfolioStateInput) -> SizingResult:
    inv_vol, _existing_fraction, denom = _denominator(inp)
    require_positive(inp.current_price, "current_price")
    require_positive(inp.total_equity, "total_equity")

    weight_fraction = inv_vol / denom
    weight_pct = weight_fraction * HUNDRED
    notional = inp.total_equity * weight_fraction
    quantity = notional / inp.current_price

    exposures = require(inp.exposures, "exposures")
    realized_vol_pct = require(inp.realized_vol_pct, "realized_vol_pct")

    return SizingResult(
        quantity=quantity,
        weight_pct=weight_pct,
        method=SizingMethod.RISK_PARITY,
        inputs_hash=hash_inputs(
            SizingMethod.RISK_PARITY,
            realized_vol_pct=realized_vol_pct,
            total_exposure_pct=exposures.total_exposure_pct,
            current_price=inp.current_price,
        ),
    )
