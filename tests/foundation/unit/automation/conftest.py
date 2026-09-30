from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import pydantic
import pytest

from src.data.models.market_data import Candle
from src.foundation.automation.flags import FEATURE_FLAG_NAME, flag_enabled
from src.foundation.automation.ports.action_sink import ActionResult
from src.foundation.automation.ports.gate import GateDecision
from src.foundation.risk.ports.notifier import NotifyResult
from src.foundation.risk_gate.contracts.v1 import RiskOutcome

_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _automation_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """This package's existing tests exercise rule create/evaluate/execute
    without knowing about the U-4a feature flag -- default it ON here so
    that behavior stays unchanged (task-6900); tests for the OFF path
    override it explicitly."""
    monkeypatch.setenv(FEATURE_FLAG_NAME, "1")


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


# --- DEEPEN (task-10074): negative / failure-injection coverage for this
# package's shared fixtures/fakes -- the helpers above had no direct tests
# of their own invariants (I-09 fail-closed composition, feature-flag
# default, Candle field validation).


def test_gate_decision_denies_when_compliance_decision_id_missing() -> None:
    """I-09: both authorities must leave their own decision id -- ALLOW
    outcomes alone are not enough if the compliance decision id is absent."""
    decision = GateDecision(
        risk_outcome=RiskOutcome.ALLOW,
        risk_decision_id=uuid4(),
        compliance_outcome=RiskOutcome.ALLOW,
        compliance_decision_id=None,
    )
    assert decision.allowed is False


def test_gate_decision_denies_when_risk_decision_id_missing() -> None:
    """Same invariant, mirrored on the risk side."""
    decision = GateDecision(
        risk_outcome=RiskOutcome.ALLOW,
        risk_decision_id=None,
        compliance_outcome=RiskOutcome.ALLOW,
        compliance_decision_id=uuid4(),
    )
    assert decision.allowed is False


def test_gate_decision_denies_when_either_outcome_is_deny() -> None:
    """Both ids present is not sufficient -- both outcomes must be ALLOW."""
    decision = GateDecision(
        risk_outcome=RiskOutcome.DENY,
        risk_decision_id=uuid4(),
        compliance_outcome=RiskOutcome.ALLOW,
        compliance_decision_id=uuid4(),
        reason_codes=("RISK_KILL_SWITCH_ACTIVE_GLOBAL",),
    )
    assert decision.allowed is False
    assert decision.reason_codes == ("RISK_KILL_SWITCH_ACTIVE_GLOBAL",)


def test_fake_gate_deny_path_never_yields_compliance_decision_id() -> None:
    """`FakeGate(allow=False)` must reproduce the real fail-closed shape
    (no compliance decision id on denial) so tests built on it don't drift
    from the production invariant it stands in for."""
    decision = GateDecision(
        risk_outcome=RiskOutcome.DENY,
        risk_decision_id=uuid4(),
        compliance_outcome=RiskOutcome.DENY,
        compliance_decision_id=None,
        reason_codes=("RISK_KILL_SWITCH_ACTIVE_GLOBAL",),
    )
    assert decision.compliance_decision_id is None
    assert decision.allowed is False


def test_make_candle_rejects_non_numeric_close() -> None:
    """`Candle` is a pydantic model -- `make_candle` must not silently
    swallow a close price that cannot be coerced to Decimal."""
    with pytest.raises(pydantic.ValidationError):
        Candle(
            symbol="005930",
            exchange="KRX",
            timeframe="1d",
            open=Decimal("1"),
            high=Decimal("1"),
            low=Decimal("1"),
            close=cast(Decimal, "not-a-number"),
            volume=Decimal("1"),
            open_time=_EPOCH,
            close_time=_EPOCH + timedelta(days=1),
        )


def test_flag_enabled_defaults_off_when_env_var_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Package-level default direction (off) must hold when the env var is
    entirely unset, not just when it's set to a falsy string -- the
    `_automation_flag_on` autouse fixture must never mask this."""
    monkeypatch.delenv(FEATURE_FLAG_NAME, raising=False)
    assert flag_enabled() is False


def test_flag_enabled_rejects_non_canonical_truthy_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the exact string "1" enables the flag -- "true"/"yes"/"on" must
    not, since `flag_enabled()` does a strict `== "1"` comparison."""
    monkeypatch.setenv(FEATURE_FLAG_NAME, "true")
    assert flag_enabled() is False


@pytest.mark.asyncio
async def test_fake_notifier_send_failure_injection_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure-injection: a dependency (the notifier transport) raising must
    propagate to the caller unmodified -- callers built on `FakeNotifier`
    should not assume `send()` can never raise."""
    notifier = FakeNotifier(ok=True)

    async def _boom(notification: object) -> NotifyResult:
        raise ConnectionError("telegram transport unavailable")

    monkeypatch.setattr(notifier, "send", _boom)

    with pytest.raises(ConnectionError):
        await notifier.send(None)
