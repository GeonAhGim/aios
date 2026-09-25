"""task-7469 -- `foundation_personal_gate.evaluate_personal_layer` coverage.

This function has zero prior unit tests anywhere in the repo. It implements a
personal-conservative kill-switch and risk-bundle layer scoped to a single
configured account (task-3986): it must be a true no-op for every other
account, and must fail closed (deny) if its config bundle cannot be loaded.
Fakes only -- no DB, no network.

Covers all 5 branches of the function body:
1. no scoped account configured -> no-op, kill switch never checked
2. scoped account configured but does not match caller -> no-op
3. scoped account matches and kill switch engaged -> DENY
4. scoped account matches, kill switch clear, no risk snapshot -> no-op
5. scoped account matches, kill switch clear, bundle load fails -> DENY (fail-closed)
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid4

import pytest

from src.foundation.risk.ports.notifier import PersonalNotifierPort
from src.foundation.risk.ports.state import PersonalOperationStatePort
from src.services.order_service import foundation_personal_gate as target
from src.services.order_service.foundation_personal_gate import (
    PersonalGateResult,
    evaluate_personal_layer,
)
from src.services.order_service.gate import OrderContext, PersonalOrderRiskSnapshot

_NOTIFIER = cast(PersonalNotifierPort, object())

_ACCOUNT_ID: UUID = uuid4()


class _FakePersonalState:
    def __init__(self, *, scoped_account_id: UUID | None, kill_engaged: bool) -> None:
        self._scoped_account_id = scoped_account_id
        self._kill_engaged = kill_engaged
        self.calls: list[str] = []

    async def personal_mode_account_id(self) -> UUID | None:
        self.calls.append("personal_mode_account_id")
        return self._scoped_account_id

    async def is_kill_engaged(self) -> bool:
        self.calls.append("is_kill_engaged")
        return self._kill_engaged


def _make_context(
    *, user_id: UUID, personal_risk_snapshot: PersonalOrderRiskSnapshot | None = None
) -> OrderContext:
    return OrderContext(
        user_id=user_id,
        execution_id=1,
        exchange="bitget",
        mandate_revision_id=None,
        personal_risk_snapshot=personal_risk_snapshot,
    )


async def _evaluate(
    context: OrderContext, *, personal_state: _FakePersonalState
) -> PersonalGateResult:
    # `_FakePersonalState` is structurally complete for the members
    # `evaluate_personal_layer` actually calls; cast tells mypy to trust that
    # (the port is a large Protocol and only two methods are exercised here).
    return await evaluate_personal_layer(
        context,
        personal_state=cast(PersonalOperationStatePort, personal_state),
        personal_notifier=_NOTIFIER,
    )


def _make_snapshot() -> PersonalOrderRiskSnapshot:
    return PersonalOrderRiskSnapshot(
        symbol="BTCUSDT",
        order_notional_krw=Decimal("1000000"),
        account_equity_krw=Decimal("10000000"),
        current_exposure_krw=Decimal("2000000"),
        daily_realized_pnl_pct=Decimal("-1.5"),
    )


@pytest.mark.asyncio
async def test_scoped_account_none_is_noop_without_checking_kill_switch() -> None:
    personal_state = _FakePersonalState(scoped_account_id=None, kill_engaged=True)
    context = _make_context(user_id=_ACCOUNT_ID)

    result = await _evaluate(context, personal_state=personal_state)

    assert result.denied is False
    assert "is_kill_engaged" not in personal_state.calls


@pytest.mark.asyncio
async def test_different_account_is_noop() -> None:
    other_account_id = uuid4()
    personal_state = _FakePersonalState(scoped_account_id=other_account_id, kill_engaged=True)
    context = _make_context(user_id=_ACCOUNT_ID)

    result = await _evaluate(context, personal_state=personal_state)

    assert result.denied is False
    assert "is_kill_engaged" not in personal_state.calls


@pytest.mark.asyncio
async def test_kill_engaged_for_scoped_account_denies() -> None:
    personal_state = _FakePersonalState(scoped_account_id=_ACCOUNT_ID, kill_engaged=True)
    context = _make_context(user_id=_ACCOUNT_ID)

    result = await _evaluate(context, personal_state=personal_state)

    assert result.denied is True
    assert result.reason_codes == ("RISK_PERSONAL_KILL_SWITCH_ENGAGED",)


@pytest.mark.asyncio
async def test_no_snapshot_is_noop_after_kill_check_passes() -> None:
    personal_state = _FakePersonalState(scoped_account_id=_ACCOUNT_ID, kill_engaged=False)
    context = _make_context(user_id=_ACCOUNT_ID, personal_risk_snapshot=None)

    result = await _evaluate(context, personal_state=personal_state)

    assert result.denied is False


@pytest.mark.asyncio
async def test_bundle_load_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise_bad_config() -> Any:
        raise ValueError("bad config")

    monkeypatch.setattr(target, "load_personal_bundle", _raise_bad_config)

    async def _unexpected_check_personal_order(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("check_personal_order must not be reached when bundle load fails")

    monkeypatch.setattr(target, "check_personal_order", _unexpected_check_personal_order)

    personal_state = _FakePersonalState(scoped_account_id=_ACCOUNT_ID, kill_engaged=False)
    context = _make_context(user_id=_ACCOUNT_ID, personal_risk_snapshot=_make_snapshot())

    result = await _evaluate(context, personal_state=personal_state)

    assert result.denied is True
    assert result.reason_codes == ("RISK_PERSONAL_BUNDLE_UNAVAILABLE",)
