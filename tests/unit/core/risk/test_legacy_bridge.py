"""L4_risk_and_safety_v1.0.md#9 R-03 — legacy_bridge.build_risk_inputs 직접 테스트.

주 계약 테스트(`RiskInputs.from_legacy_dict` 왕복)는
`test_inputs_contract.py`에 있다 — 여기서는 이 헬퍼가 반환한 값이 실제로
`RiskInputs` 인스턴스인지, `cls` 파라미터를 실제로 사용하는지만 확인한다.
"""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.core.risk.inputs import RiskInputs
from src.core.risk.legacy_bridge import build_risk_inputs


class _Allocation:
    def __init__(self, symbol: str, strategy_id: str, quantity: Decimal, capital_pct: Decimal):
        self.symbol = symbol
        self.strategy_id = strategy_id
        self.approved_quantity = quantity
        self.capital_pct = capital_pct


def test_build_risk_inputs_returns_instance_of_cls():
    allocation = _Allocation("BTC/USDT", "strat-1", Decimal("0.1"), Decimal("10"))
    result = build_risk_inputs(
        RiskInputs,
        allocation,
        {},
        tenant_id=uuid4(),
        execution_id=1,
        now=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    assert isinstance(result, RiskInputs)


def test_build_risk_inputs_new_entry_defaults_reduce_only_false():
    allocation = _Allocation("BTC/USDT", "strat-1", Decimal("0.1"), Decimal("10"))
    result = build_risk_inputs(
        RiskInputs,
        allocation,
        {},
        tenant_id=uuid4(),
        execution_id=1,
        now=datetime(2026, 9, 3, tzinfo=timezone.utc),
    )
    assert result.intent.reduce_only is False
