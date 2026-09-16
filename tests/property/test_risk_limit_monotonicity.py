"""H-8 property 테스트 — 리스크 노출 한도(`check_exposure_limits`)의 단조성.

Spec: docs/design/ADR-2026-09-09-B(H-8) + docs/specs/L4_risk_and_safety_v1.0.md#3.2,
#2.1, #9 R-14, `src/core/risk/limits.py`.

두 방향의 단조성 불변식:
1. 한도값(`limit_value`)을 고정하고 관측치(`observed`, 여기서는
   `intent.notional`)를 올리면 — 더 낮은 관측치에서 이미 DENY였다면 더 높은
   관측치에서도 DENY다(하드 한도는 관측치가 늘어날수록 절대 완화되지 않는다).
2. 관측치를 고정하고 한도값을 올리면 — 더 낮은 한도에서 이미 ALLOW였다면
   더 높은 한도에서도 ALLOW다(한도를 늘리는 것은 절대 더 엄격해지지 않는다).

`src/core/risk/`는 FROZEN_PAPER_ONLY이지만 이 task의 decision에는 FROZEN
승인이 없다 — 이 테스트 파일은 `src/core/risk/limits.py`를 읽기만 하고
수정하지 않는다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from hypothesis import assume, given
from hypothesis import strategies as st

from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)
from src.core.risk.limits import ExposureLimit, LimitMetric, LimitScope, check_exposure_limits

_NOW = datetime(2026, 9, 16, tzinfo=timezone.utc)
_TENANT_ID: UUID = uuid4()

# NUMERIC(20,2) 규모를 넘지 않는 범위 — `notional`/`limit_value` 둘 다 이
# 자릿수로 quantize된 Decimal이어야 pydantic 검증을 통과한다.
_MONEY = st.decimals(
    min_value=Decimal("0.00"), max_value=Decimal("1000000000.00"), places=2, allow_nan=False
)


def _inputs_with_notional(notional: Decimal) -> RiskInputs:
    intent = OrderIntent(
        symbol="BTC/USDT",
        asset_class="CRYPTO_SPOT",
        side="BUY",
        quantity=Decimal("1"),
        ref_price=Decimal("1"),
        notional=notional,
        reduce_only=False,
        strategy_id="strat-1",
        strategy_version="1.0",
        capital_pct=Decimal("1"),
    )
    return RiskInputs(
        tenant_id=_TENANT_ID,
        execution_ref="exec:1",
        certified_badge=True,
        allocated_capital=Decimal("1000"),
        intent=intent,
        equity=EquityInputs(as_of=_NOW),
        exposure=ExposureSnapshot(as_of=_NOW),
        stats=StatsInputs(as_of=_NOW),
        activity=ActivityInputs(),
        safety=SafetyInputs(),
        limits=(),
        as_of=_NOW,
    )


def _order_limit(limit_value: Decimal, *, hard: bool = True) -> ExposureLimit:
    return ExposureLimit(
        scope=LimitScope.TENANT,
        scope_ref=str(_TENANT_ID),
        metric=LimitMetric.MAX_ORDER_NOTIONAL,
        limit_value=limit_value,
        hard=hard,
        limit_id=uuid4(),
    )


@given(_MONEY, _MONEY, _MONEY)
def test_raising_observed_notional_never_turns_a_deny_back_into_allow(
    limit_value: Decimal, lower: Decimal, higher: Decimal
) -> None:
    assume(lower <= higher)
    limit = _order_limit(limit_value)

    lower_result = check_exposure_limits(_inputs_with_notional(lower), (limit,))
    higher_result = check_exposure_limits(_inputs_with_notional(higher), (limit,))

    if lower_result.outcome == RiskOutcome.DENY:
        assert higher_result.outcome == RiskOutcome.DENY


@given(_MONEY, _MONEY, _MONEY)
def test_raising_the_limit_value_never_turns_an_allow_back_into_deny(
    observed: Decimal, lower_limit: Decimal, higher_limit: Decimal
) -> None:
    assume(lower_limit <= higher_limit)
    inputs = _inputs_with_notional(observed)

    lower_result = check_exposure_limits(inputs, (_order_limit(lower_limit),))
    higher_result = check_exposure_limits(inputs, (_order_limit(higher_limit),))

    if lower_result.outcome == RiskOutcome.ALLOW:
        assert higher_result.outcome == RiskOutcome.ALLOW


@given(_MONEY, _MONEY)
def test_soft_limit_never_denies_only_hard_limit_can(
    limit_value: Decimal, observed: Decimal
) -> None:
    """소프트(`hard=False`) 한도는 위반해도 ESCALATE까지만 간다 — 소프트
    한도 단독으로는 절대 DENY가 나오지 않는다(단조성의 전제인 hard/soft
    구분 자체를 검증)."""
    result = check_exposure_limits(
        _inputs_with_notional(observed), (_order_limit(limit_value, hard=False),)
    )
    assert result.outcome != RiskOutcome.DENY


# ---- 음성 테스트: fail-closed(입력 결손) ----


@given(_MONEY)
def test_missing_trades_last_1h_always_denies_regardless_of_limit_value(
    limit_value: Decimal,
) -> None:
    """관측치가 아예 없으면(activity.trades_last_1h=None) 한도값과 무관하게
    항상 DENY다 — "관측치가 없다"는 단조성 비교 대상이 아니라 즉시
    fail-closed여야 한다(I2)."""
    inputs = _inputs_with_notional(Decimal("0"))
    limit = ExposureLimit(
        scope=LimitScope.TENANT,
        scope_ref=str(_TENANT_ID),
        metric=LimitMetric.MAX_TRADES_PER_HOUR,
        limit_value=limit_value,
        hard=True,
        limit_id=uuid4(),
    )
    result = check_exposure_limits(inputs, (limit,))
    assert result.outcome == RiskOutcome.DENY
    assert result.missing_fields == ("activity.trades_last_1h",)


# ---- 성능 단언: 사전거래 게이트 p99 5ms(ADR-2026-09-09-C 성능 예산표) ----


def test_check_exposure_limits_p99_latency_stays_within_pre_trade_gate_budget() -> None:
    inputs = _inputs_with_notional(Decimal("500.00"))
    limits = tuple(_order_limit(Decimal(i)) for i in range(1, 21))

    samples: list[float] = []
    for _ in range(500):
        start = time.perf_counter()
        check_exposure_limits(inputs, limits)
        samples.append(time.perf_counter() - start)

    samples.sort()
    p99 = samples[int(len(samples) * 0.99) - 1]
    assert p99 < 0.005, f"p99={p99 * 1000:.3f}ms >= 5ms 예산(사전거래 게이트, ADR-2026-09-09-C)"
