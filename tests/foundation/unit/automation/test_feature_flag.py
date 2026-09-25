"""task-6900 (U-4a DoD follow-up): `FF_U4A_RULE_ENGINE` feature flag.

OFF (default): rule create/list/cancel/preview raise
`RuleEngineFeatureDisabledError` (the shape a future `rules.py` router maps
to 404, mirroring `AssistantFeatureDisabledError`), and evaluation/execution
no-op instead of raising (a batch loop over tenants must not crash on a
disabled flag) -- zero order/hedge/kill/notify sink calls either way.

ON: unchanged behavior, proven by the rest of this package's tests running
with the flag defaulted ON via `conftest.py::_automation_flag_on`.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.trading import OrderSide
from src.foundation.automation.adapters.in_memory_repository import InMemoryAutomationRuleRepository
from src.foundation.automation.application.cancel_rule import cancel_rule
from src.foundation.automation.application.create_rule import create_rule
from src.foundation.automation.application.evaluate_rules import evaluate_active_rules
from src.foundation.automation.application.execute_action import execute_action
from src.foundation.automation.application.list_rules import list_rules
from src.foundation.automation.application.preview_rule import preview_rule
from src.foundation.automation.contracts.v1 import (
    NotifyAction,
    OrderAction,
    PriceCondition,
    RuleStatus,
)
from src.foundation.automation.flags import FEATURE_FLAG_NAME, RuleEngineFeatureDisabledError
from src.foundation.automation.ports.repository import AutomationRuleRepository

from .conftest import FakeGate, FakeNotifier, FakeSink, make_candle


def _condition() -> PriceCondition:
    return PriceCondition(symbol="005930", operator=">", threshold=Decimal("100"))


def _flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FEATURE_FLAG_NAME, "0")


class _RepoCallSpy:
    """Wraps the in-memory repo and records whether `list_by_tenant` was
    ever invoked -- proves the OFF path skips evaluation entirely rather
    than fetching rules and discarding them."""

    def __init__(self, inner: AutomationRuleRepository) -> None:
        self._inner = inner
        self.list_by_tenant_calls = 0

    async def save(self, rule) -> None:  # noqa: ANN001 -- delegate, no new type surface
        await self._inner.save(rule)

    async def get(self, tenant_id: UUID, rule_id: UUID):  # noqa: ANN001, ANN201
        return await self._inner.get(tenant_id, rule_id)

    async def list_by_tenant(self, tenant_id: UUID, *, status: RuleStatus | None = None):  # noqa: ANN001, ANN201
        self.list_by_tenant_calls += 1
        return await self._inner.list_by_tenant(tenant_id, status=status)


# ---- negative: flag OFF -> rule endpoints (application entry points) reject ----


@pytest.mark.asyncio
async def test_create_rule_rejected_when_flag_off(
    tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag_off(monkeypatch)
    repo = InMemoryAutomationRuleRepository()
    with pytest.raises(RuleEngineFeatureDisabledError):
        await create_rule(
            repo,
            tenant_id=tenant_id,
            name="rule-1",
            conditions=(_condition(),),
            action=NotifyAction(message_template="hi"),
        )
    assert await repo.list_by_tenant(tenant_id) == ()  # no side effect leaked through


@pytest.mark.asyncio
async def test_list_rules_rejected_when_flag_off(
    tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = InMemoryAutomationRuleRepository()
    await create_rule(
        repo,
        tenant_id=tenant_id,
        name="rule-1",
        conditions=(_condition(),),
        action=NotifyAction(message_template="hi"),
    )
    _flag_off(monkeypatch)
    with pytest.raises(RuleEngineFeatureDisabledError):
        await list_rules(repo, tenant_id=tenant_id)


@pytest.mark.asyncio
async def test_cancel_rule_rejected_when_flag_off(
    tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = InMemoryAutomationRuleRepository()
    created = await create_rule(
        repo,
        tenant_id=tenant_id,
        name="rule-1",
        conditions=(_condition(),),
        action=NotifyAction(message_template="hi"),
    )
    _flag_off(monkeypatch)
    with pytest.raises(RuleEngineFeatureDisabledError):
        await cancel_rule(repo, tenant_id=tenant_id, rule_id=created.rule_id)
    # unchanged -- the rejected cancel never touched the stored rule
    stored = await repo.get(tenant_id, created.rule_id)
    assert stored is not None and stored.status == RuleStatus.ACTIVE


@pytest.mark.asyncio
async def test_cancel_rule_flag_off_response_identical_for_real_and_unknown_id(
    tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Tenant isolation must not weaken: with the flag off, a real rule id
    and a made-up one both fail the same way (flag check first, before any
    existence/tenant check) -- no information about which ids exist leaks."""
    repo = InMemoryAutomationRuleRepository()
    created = await create_rule(
        repo,
        tenant_id=tenant_id,
        name="rule-1",
        conditions=(_condition(),),
        action=NotifyAction(message_template="hi"),
    )
    _flag_off(monkeypatch)
    with pytest.raises(RuleEngineFeatureDisabledError) as real_exc:
        await cancel_rule(repo, tenant_id=tenant_id, rule_id=created.rule_id)
    with pytest.raises(RuleEngineFeatureDisabledError) as fake_exc:
        await cancel_rule(repo, tenant_id=tenant_id, rule_id=uuid4())
    assert str(real_exc.value) == str(fake_exc.value)


@pytest.mark.asyncio
async def test_preview_rule_rejected_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _flag_off(monkeypatch)
    bars = [make_candle(close=Decimal("110"), index=0)]
    with pytest.raises(RuleEngineFeatureDisabledError):
        preview_rule((_condition(),), {"005930": bars})


# ---- negative: flag OFF -> evaluation is a hard no-op, never reaches the repo ----


@pytest.mark.asyncio
async def test_evaluate_active_rules_noop_and_skips_repo_when_flag_off(
    tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = InMemoryAutomationRuleRepository()
    await create_rule(
        repo,
        tenant_id=tenant_id,
        name="rule-1",
        conditions=(_condition(),),
        action=NotifyAction(message_template="hi"),
    )
    spy = _RepoCallSpy(repo)
    _flag_off(monkeypatch)

    snapshot = make_candle(close=Decimal("150"))
    from src.foundation.automation.domain.evaluate import MarketSnapshot

    triggered = await evaluate_active_rules(
        spy, tenant_id=tenant_id, snapshots={"005930": MarketSnapshot(candle=snapshot)}
    )

    assert triggered == ()
    assert spy.list_by_tenant_calls == 0


# ---- negative: flag OFF -> action execution never fires, order actions included ----


@pytest.mark.asyncio
async def test_execute_action_order_never_executed_when_flag_off(
    tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    _flag_off(monkeypatch)
    sink = FakeSink()
    result = await execute_action(
        tenant_id=tenant_id,
        rule_id=uuid4(),
        action=OrderAction(symbol="005930", side=OrderSide.BUY, quantity=Decimal("1")),
        trace_id=uuid4(),
        gate=FakeGate(allow=True),  # gate would allow -- the flag alone must block it
        sink=sink,
        notifier=FakeNotifier(),
    )
    assert result.executed is False
    assert result.error == FEATURE_FLAG_NAME
    assert sink.total_calls == 0


@pytest.mark.asyncio
async def test_execute_action_notify_never_sent_when_flag_off(
    tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    """NotifyAction normally bypasses the gate entirely -- confirm the flag
    check still blocks it (the gate is not a safety net for notify)."""
    _flag_off(monkeypatch)
    notifier = FakeNotifier()
    result = await execute_action(
        tenant_id=tenant_id,
        rule_id=uuid4(),
        action=NotifyAction(message_template="hi"),
        trace_id=uuid4(),
        gate=FakeGate(allow=True),
        sink=FakeSink(),
        notifier=notifier,
    )
    assert result.executed is False
    assert notifier.calls == []


# ---- positive: flag ON (explicit, not just this package's autouse default) ----


@pytest.mark.asyncio
async def test_full_flow_unchanged_when_flag_explicitly_on(
    tenant_id: UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(FEATURE_FLAG_NAME, "1")
    repo = InMemoryAutomationRuleRepository()
    created = await create_rule(
        repo,
        tenant_id=tenant_id,
        name="rule-1",
        conditions=(_condition(),),
        action=OrderAction(symbol="005930", side=OrderSide.BUY, quantity=Decimal("1")),
    )
    listed = await list_rules(repo, tenant_id=tenant_id)
    assert listed == (created,)

    from src.foundation.automation.domain.evaluate import MarketSnapshot

    triggered = await evaluate_active_rules(
        repo,
        tenant_id=tenant_id,
        snapshots={"005930": MarketSnapshot(candle=make_candle(close=Decimal("150")))},
    )
    assert len(triggered) == 1

    sink = FakeSink()
    result = await execute_action(
        tenant_id=tenant_id,
        rule_id=created.rule_id,
        action=triggered[0].action,
        trace_id=uuid4(),
        gate=FakeGate(allow=True),
        sink=sink,
        notifier=FakeNotifier(),
    )
    assert result.executed is True
    assert sink.total_calls == 1

    cancelled = await cancel_rule(repo, tenant_id=tenant_id, rule_id=created.rule_id)
    assert cancelled.status == RuleStatus.CANCELLED
