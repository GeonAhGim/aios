from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.market_data import Candle
from src.foundation.automation.ports.action_sink import ActionResult
from src.foundation.risk.ports.notifier import NotifyResult

_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_candle(
    *,
    symbol: str = "005930",
    close: Decimal,
    index: int = 0,
    open_: Decimal | None = None,
    high: Decimal | None = None,
    low: Decimal | None = None,
    volume: Decimal = Decimal("100"),
    exchange: str = "KRX",
    timeframe: str = "1d",
) -> Candle:
    open_time = _EPOCH + timedelta(days=index)
    return Candle(
        symbol=symbol,
        exchange=exchange,
        timeframe=timeframe,
        open=open_ if open_ is not None else close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
        open_time=open_time,
        close_time=open_time + timedelta(days=1),
    )


class FakeNotifier:
    def __init__(self, *, ok: bool = True, error: str | None = None) -> None:
        self._ok = ok
        self._error = error
        self.calls: list = []

    async def send(self, notification):
        self.calls.append(notification)
        return NotifyResult(ok=self._ok, status_code=200 if self._ok else 502, error=self._error)


class FakeGate:
    """`outcome`이 "allow"면 risk∩compliance 모두 ALLOW+decision id를 채운다.
    그 외 값은 그대로 거부 사유로 쓴다(kill-switch 활성 시나리오 재현용)."""

    def __init__(
        self, *, allow: bool = True, reason: str = "RISK_KILL_SWITCH_ACTIVE_GLOBAL"
    ) -> None:
        self._allow = allow
        self._reason = reason
        self.calls: list = []

    async def check(self, intent):
        from src.foundation.automation.ports.gate import GateDecision
        from src.foundation.risk_gate.contracts.v1 import RiskOutcome

        self.calls.append(intent)
        if self._allow:
            return GateDecision(
                risk_outcome=RiskOutcome.ALLOW,
                risk_decision_id=uuid4(),
                compliance_outcome=RiskOutcome.ALLOW,
                compliance_decision_id=uuid4(),
            )
        return GateDecision(
            risk_outcome=RiskOutcome.DENY,
            risk_decision_id=uuid4(),
            compliance_outcome=RiskOutcome.DENY,
            compliance_decision_id=None,
            reason_codes=(self._reason,),
        )


class FakeSink:
    def __init__(self) -> None:
        self.order_calls: list = []
        self.hedge_calls: list = []
        self.kill_calls: list = []

    async def submit_order(self, intent, gate_decision):
        self.order_calls.append((intent, gate_decision))
        return ActionResult(executed=True, detail="order_submitted")

    async def submit_hedge(self, intent, gate_decision):
        self.hedge_calls.append((intent, gate_decision))
        return ActionResult(executed=True, detail="hedge_submitted")

    async def trigger_kill(self, intent, gate_decision):
        self.kill_calls.append((intent, gate_decision))
        return ActionResult(executed=True, detail="kill_triggered")

    @property
    def total_calls(self) -> int:
        return len(self.order_calls) + len(self.hedge_calls) + len(self.kill_calls)


@pytest.fixture
def tenant_id() -> UUID:
    return uuid4()
