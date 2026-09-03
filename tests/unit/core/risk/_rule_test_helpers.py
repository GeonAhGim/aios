"""R-05~R-09 규칙 테스트가 공유하는 `RiskInputs`/`RiskPolicy` 픽스처.

테스트 파일이 아니라 헬퍼 모듈이므로 `test_` 접두어를 쓰지 않는다
(pytest가 수집 대상으로 보지 않도록).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)

NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)
POLICY = load_risk_policy()


def _order_intent(**overrides: object) -> OrderIntent:
    base: dict[str, object] = dict(
        symbol="BTC/USDT",
        asset_class="CRYPTO_SPOT",
        side="BUY",
        quantity=Decimal("0.1"),
        ref_price=Decimal("50000"),
        notional=Decimal("5000"),
        reduce_only=False,
        strategy_id="strat-1",
        strategy_version="1.0",
        capital_pct=Decimal("10"),
    )
    base.update(overrides)
    return OrderIntent(**base)  # type: ignore[arg-type]


def sample_inputs(**overrides: object) -> RiskInputs:
    base: dict[str, object] = dict(
        tenant_id=uuid4(),
        execution_ref="exec:1",
        certified_badge=True,
        allocated_capital=Decimal("1000"),
        intent=_order_intent(),
        equity=EquityInputs(total_equity=Decimal("10000"), as_of=NOW),
        exposure=ExposureSnapshot(position_quantity=Decimal("0"), as_of=NOW),
        stats=StatsInputs(as_of=NOW),
        activity=ActivityInputs(),
        safety=SafetyInputs(),
        limits=(),
        as_of=NOW,
    )
    base.update(overrides)
    return RiskInputs(**base)  # type: ignore[arg-type]
