"""L4_risk_and_safety_v1.0.md#3.2, #9 R-03 — `RiskInputs.from_legacy_dict` 구현.

`inputs.py`가 180줄 상한을 지키도록 조립 로직만 분리했다 — 공개 계약은
여전히 `RiskInputs.from_legacy_dict`(§3.2)이고, 이 함수는 그 뒤에서만
호출되는 사실상 사설(private) 헬퍼다.

미검증(Draft): 레거시 `AllocationDecision`/`account_state`에는
`side`·`ref_price`·`notional`·`reduce_only`·`asset_class`·
`strategy_version`·노출 스냅샷이 없어 안전한 기본값(신규 매수·0 명목가·
비감소)으로 채운다. 실제 조립은 `execution_loop/risk_inputs_assembler.py`
(R-31)가 대체하며, 그 전까지 notional에 의존하는 규칙은 이 경로로
정확히 재현되지 않는다.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)


def build_risk_inputs(
    cls: type[RiskInputs],
    allocation: Any,
    account_state: dict[str, Any],
    *,
    tenant_id: UUID,
    execution_id: int,
    now: datetime,
) -> RiskInputs:
    position_quantity = account_state.get("position_quantity")
    avg_trade_count_24h = account_state.get("avg_trade_count_24h")

    return cls(
        tenant_id=tenant_id,
        execution_ref=f"exec:{execution_id}",
        certified_badge=account_state.get("certified_badge"),
        allocated_capital=account_state.get("allocated_capital"),
        intent=OrderIntent(
            symbol=allocation.symbol,
            asset_class="CRYPTO_SPOT",
            side="BUY",
            quantity=allocation.approved_quantity,
            ref_price=Decimal("0"),
            notional=Decimal("0"),
            reduce_only=False,
            strategy_id=allocation.strategy_id,
            strategy_version="unknown",
            capital_pct=allocation.capital_pct,
        ),
        equity=EquityInputs(
            total_equity=account_state.get("total_equity"),
            available_balance=account_state.get("available_balance"),
            daily_pnl_pct=account_state.get("daily_pnl_pct"),
            drawdown_pct=account_state.get("drawdown_pct"),
            as_of=now,
        ),
        exposure=ExposureSnapshot(
            position_quantity=position_quantity,
            open_positions_count=(
                1 if position_quantity is not None and position_quantity != 0 else 0
            ),
            gross_leverage=account_state.get("leverage"),
            as_of=now,
        ),
        stats=StatsInputs(
            var_pct=account_state.get("var_pct"),
            correlated_exposure_pct=account_state.get("correlated_exposure_pct"),
            as_of=now,
        ),
        activity=ActivityInputs(
            trades_last_1h=account_state.get("recent_trade_count_1h"),
            trades_avg_per_hour_24h=(
                Decimal(str(avg_trade_count_24h)) if avg_trade_count_24h is not None else None
            ),
        ),
        safety=SafetyInputs(
            circuit_breaker_level=account_state.get("circuit_breaker_level"),
            execution_paused_by_safety=account_state.get("execution_paused_by_safety"),
        ),
        as_of=now,
    )
