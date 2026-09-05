"""R-35 `evaluate_pre_submit` 통합테스트 — 실 DB(`TEST_DATABASE_URL`) 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-35.
DoD: (1) 4개 입력(control/CB/distrust/connection) 각각 단독 DENY·PAUSE +
None 입력 fail-closed DENY(I2). (2) TTL 2s 정확 + is_actionable. (3) F0가
control 조회와 같은 스냅샷에서 읽힌 값(5쌍 모두 포함). (4) 교차 tenant
격리. (5) recorder(R-25)로 WORM 기록(DENY 포함).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.risk.decision import GateKind, RiskOutcome
from src.foundation.connections.domain.models import (
    AccountConnection,
    ConnectionHealth,
    ConnectionState,
    HealthState,
)
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application.evaluate_pre_submit import evaluate_pre_submit
from src.foundation.risk_gate.domain.fence import fence_pairs_for
from src.foundation.risk_gate.domain.models import SafetyScope
from src.services.risk_decision_recorder import RiskDecisionRecorder
from tests.integration.conftest import NoopEventBus, create_test_user

_PROVIDER = "bitget"
_SYMBOL = "BTC/USDT"
_SIDE = "BUY"
_QTY = Decimal("0.01")


class _FakeConnectionRepo:
    """provider_code별 connection 유무·health만 흉내내는 최소 fake — 실제
    connection lifecycle 없이 freshness 전달 경로만 검증한다
    (test_risk_gate_lifecycle.py의 `_FakeHealthyConnectionRepo`와 동일 취지)."""

    def __init__(self, *, tenant_id: UUID, provider_code: str, health: HealthState | None) -> None:
        self._tenant_id = tenant_id
        self._provider_code = provider_code
        self._health = health
        self._connection_id = uuid4()

    async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
        if tenant_id != self._tenant_id:
            return []
        return [
            AccountConnection(
                id=self._connection_id,
                tenant_id=self._tenant_id,
                owner_subject_id=self._tenant_id,
                provider_code=self._provider_code,
                opaque_account_ref="ACCT-TEST",
                state=ConnectionState.ACTIVE_READONLY,
                capability_profile=(),
                revision=1,
            )
        ]

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None:
        if self._health is None:
            return None
        return ConnectionHealth(
            connection_id=connection_id, evaluated_at=datetime.now(timezone.utc), state=self._health
        )


class _NoConnectionRepo:
    """이 provider에 connection 자체가 없는 경우 — connection_fresh=None."""

    async def list_connections(self, tenant_id: UUID) -> list[AccountConnection]:
        return []

    async def get_latest_health(self, connection_id: UUID) -> ConnectionHealth | None:
        return None


class _RiskRepoWithFixedSafetyState:
    """fence/control은 실 DB(`PostgresRiskGateRepository`)에 그대로 위임하고
    circuit breaker/distrust level만 테스트가 원하는 값으로 고정한다 —
    `system_safety_state`는 프로세스 전역 단일 행이라 직접 UPDATE하면 같은
    DB를 공유하는 다른 테스트를 오염시킨다."""

    def __init__(
        self, inner: PostgresRiskGateRepository, *, cb_level: str | None, distrust_level: str | None
    ) -> None:
        self._inner = inner
        self._cb_level = cb_level
        self._distrust_level = distrust_level

    async def read_fence_and_controls(self, pairs):  # noqa: ANN001, ANN201
        return await self._inner.read_fence_and_controls(pairs)

    async def read_safety_state(self, *, provider_code: str, symbol: str):  # noqa: ANN201
        return self._cb_level, self._distrust_level


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def risk_repo(pool: asyncpg.Pool) -> PostgresRiskGateRepository:
    return PostgresRiskGateRepository(pool)


@pytest.fixture
def decision_repo(pool: asyncpg.Pool) -> PostgresDecisionRepository:
    return PostgresDecisionRepository(pool)


@pytest.fixture
def recorder(pool: asyncpg.Pool, decision_repo: PostgresDecisionRepository) -> RiskDecisionRecorder:
    return RiskDecisionRecorder(pool, decision_repo, NoopEventBus())


def _healthy_connection_repo(tenant_id: UUID) -> _FakeConnectionRepo:
    return _FakeConnectionRepo(
        tenant_id=tenant_id, provider_code=_PROVIDER, health=HealthState.HEALTHY
    )


def _normal_risk_repo(risk_repo: PostgresRiskGateRepository) -> _RiskRepoWithFixedSafetyState:
    return _RiskRepoWithFixedSafetyState(risk_repo, cb_level="normal", distrust_level="NORMAL")


async def test_baseline_allow_and_ttl_is_exactly_two_seconds(pool, risk_repo, recorder):
    tenant_id = await create_test_user(pool)
    execution_ref = f"exec:{uuid4().hex[:8]}"
    decision, fence = await evaluate_pre_submit(
        _normal_risk_repo(risk_repo),
        _healthy_connection_repo(tenant_id),
        recorder,
        tenant_id=tenant_id,
        execution_ref=execution_ref,
        provider_code=_PROVIDER,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.ALLOW
    assert decision.gate_kind == GateKind.PRE_SUBMIT
    assert decision.expires_at == decision.evaluated_at + timedelta(seconds=2)
    assert decision.is_actionable(decision.evaluated_at) is True
    assert decision.is_actionable(decision.evaluated_at + timedelta(seconds=2.1)) is False
    assert set(fence.tokens) == set(fence_pairs_for(tenant_id, _PROVIDER, execution_ref))


async def test_active_control_alone_denies_and_fence_matches_same_snapshot(
    pool, risk_repo, recorder
):
    tenant_id = await create_test_user(pool)
    control = await risk_repo.insert_safety_control(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(tenant_id),
        reason="pre-submit control test",
        actor_subject_id=tenant_id,
    )

    decision, fence = await evaluate_pre_submit(
        _normal_risk_repo(risk_repo),
        _healthy_connection_repo(tenant_id),
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=_PROVIDER,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_KILL_SWITCH_ACTIVE_ACCOUNT" in decision.reason_codes
    # DoD(3) — F0는 이 control 판단과 같은 스냅샷에서 읽힌 값이어야 하므로,
    # 이 control이 만든 fence 토큰과 정확히 일치해야 한다.
    assert fence.tokens[(SafetyScope.ACCOUNT, str(tenant_id))] == control.fence_token


async def test_circuit_breaker_alone_denies(pool, risk_repo, recorder):
    tenant_id = await create_test_user(pool)
    repo = _RiskRepoWithFixedSafetyState(risk_repo, cb_level="halted", distrust_level="NORMAL")

    decision, _ = await evaluate_pre_submit(
        repo,
        _healthy_connection_repo(tenant_id),
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=_PROVIDER,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_CIRCUIT_BREAKER_HALTED" in decision.reason_codes


async def test_data_distrust_alone_denies(pool, risk_repo, recorder):
    tenant_id = await create_test_user(pool)
    repo = _RiskRepoWithFixedSafetyState(risk_repo, cb_level="normal", distrust_level="DISTRUSTED")

    decision, _ = await evaluate_pre_submit(
        repo,
        _healthy_connection_repo(tenant_id),
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=_PROVIDER,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_DATA_DISTRUST_DISTRUSTED" in decision.reason_codes


async def test_connection_stale_alone_pauses(pool, risk_repo, recorder):
    tenant_id = await create_test_user(pool)
    stale_connection = _FakeConnectionRepo(
        tenant_id=tenant_id, provider_code=_PROVIDER, health=HealthState.DEGRADED
    )

    decision, _ = await evaluate_pre_submit(
        _normal_risk_repo(risk_repo),
        stale_connection,
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=_PROVIDER,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.PAUSE
    assert "RISK_INPUT_STALE" in decision.reason_codes


async def test_missing_circuit_breaker_level_is_fail_closed_deny(pool, risk_repo, recorder):
    """I2 negative test — None을 '문제없음'으로 읽지 않는다."""
    tenant_id = await create_test_user(pool)
    repo = _RiskRepoWithFixedSafetyState(risk_repo, cb_level=None, distrust_level="NORMAL")

    decision, _ = await evaluate_pre_submit(
        repo,
        _healthy_connection_repo(tenant_id),
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=_PROVIDER,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_INPUT_MISSING:cb_level" in decision.reason_codes


async def test_missing_connection_is_fail_closed_deny(pool, risk_repo, recorder):
    """I2 negative test — 이 provider에 connection 자체가 없으면 '건강함'이
    아니라 결손으로 취급해 DENY한다."""
    tenant_id = await create_test_user(pool)

    decision, _ = await evaluate_pre_submit(
        _normal_risk_repo(risk_repo),
        _NoConnectionRepo(),
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=_PROVIDER,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.DENY
    assert "RISK_INPUT_MISSING:connection_fresh" in decision.reason_codes


async def test_other_tenants_control_does_not_leak_into_this_tenants_decision(
    pool, risk_repo, recorder
):
    tenant_id = await create_test_user(pool)
    other_tenant_id = await create_test_user(pool)
    await risk_repo.insert_safety_control(
        scope=SafetyScope.ACCOUNT,
        scope_ref=str(other_tenant_id),
        reason="other tenant control",
        actor_subject_id=other_tenant_id,
    )

    decision, _ = await evaluate_pre_submit(
        _normal_risk_repo(risk_repo),
        _healthy_connection_repo(tenant_id),
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=_PROVIDER,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QTY,
        trace_id=uuid4(),
    )

    assert decision.outcome == RiskOutcome.ALLOW


async def test_denied_decision_is_recorded_in_worm_table(pool, risk_repo, recorder, decision_repo):
    """DoD(5) — 거부도 recorder(R-25)로 WORM 기록된다."""
    tenant_id = await create_test_user(pool)
    repo = _RiskRepoWithFixedSafetyState(risk_repo, cb_level="emergency", distrust_level="NORMAL")

    decision, _ = await evaluate_pre_submit(
        repo,
        _healthy_connection_repo(tenant_id),
        recorder,
        tenant_id=tenant_id,
        execution_ref=f"exec:{uuid4().hex[:8]}",
        provider_code=_PROVIDER,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QTY,
        trace_id=uuid4(),
    )
    assert decision.outcome == RiskOutcome.DENY

    stored = await decision_repo.get(decision.decision_id)
    assert stored is not None
    stored_decision, inputs_snapshot = stored
    assert stored_decision.outcome == RiskOutcome.DENY
    assert stored_decision.gate_kind == GateKind.PRE_SUBMIT
    assert inputs_snapshot["circuit_breaker_level"] == "emergency"
    # task-1532 I10 binding keys are recorded next to the fence (fenced_submit compares them)
    assert (inputs_snapshot["symbol"], inputs_snapshot["side"]) == (_SYMBOL, _SIDE)
    assert Decimal(inputs_snapshot["quantity"]) == _QTY
    pairs = fence_pairs_for(tenant_id, _PROVIDER, decision.execution_ref)
    assert set(inputs_snapshot["fence_snapshot"]) == {f"{s.value}:{ref}" for s, ref in pairs}


async def test_fence_snapshot_covers_exactly_the_five_pairs(pool, risk_repo, recorder):
    """DoD(3) — R-33 `fence_pairs_for`를 재구현하지 않고 그대로 5쌍 확인."""
    tenant_id = await create_test_user(pool)
    execution_ref = f"exec:{uuid4().hex[:8]}"

    _, fence = await evaluate_pre_submit(
        _normal_risk_repo(risk_repo),
        _healthy_connection_repo(tenant_id),
        recorder,
        tenant_id=tenant_id,
        execution_ref=execution_ref,
        provider_code=_PROVIDER,
        symbol=_SYMBOL,
        side=_SIDE,
        quantity=_QTY,
        trace_id=uuid4(),
    )

    expected = fence_pairs_for(tenant_id, _PROVIDER, execution_ref)
    assert set(fence.tokens) == set(expected)
