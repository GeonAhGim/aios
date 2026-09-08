"""R-53 통합테스트 — application/recovery_gate.py::evaluate_recovery.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#§9 R-53, §4.3 CB 표, I5(§8 394행).
`system_safety_state`(id=1, 전역 singleton)는 `tests/integration/risk/
test_circuit_breaker_loop.py`와 공유 TEST_DATABASE_URL을 쓴다 — `pool`
fixture가 매 테스트 시작 시 `normal`로 리셋해 오염을 막는다(그 파일의
동일 fixture와 같은 근거).
"""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.api.contracts.error_codes import HTTP_STATUS, ErrorCode
from src.api.contracts.exception_mapping import map_exception
from src.core.approval import service as approval
from src.core.loader.risk_policy_loader import load_risk_policy
from src.core.risk.decision import GateKind, RiskDecision, RiskOutcome
from src.core.safety.circuit_breaker import CircuitBreakerService
from src.core.safety.recovery_gate import RecoveryDecision
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.application import recovery_gate as recovery_gate_module
from src.foundation.risk_gate.application.recovery_gate import (
    RecoveryDeniedError,
    RecoveryGateRepos,
    evaluate_recovery,
)
from src.foundation.risk_gate.domain.models import SafetyScope
from src.foundation.risk_gate.ports.repository import RiskGateRepository
from src.services.risk_decision_recorder import RiskDecisionRecorder
from tests.integration.conftest import NoopEventBus, create_test_tenant

_COOLDOWN_SEC = 900
_APPROVAL_TTL_SEC = 300


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    async with p.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'normal', "
            "reactivation_approval_id = NULL WHERE id = 1"
        )
    yield p
    await p.close()


@pytest.fixture
def risk_gate_repo(pool) -> RiskGateRepository:
    return PostgresRiskGateRepository(pool)


@pytest.fixture
def cb(pool) -> CircuitBreakerService:
    return CircuitBreakerService(pool, load_risk_policy().circuit_breaker)


@pytest.fixture
def recorder(pool) -> RiskDecisionRecorder:
    return RiskDecisionRecorder(pool, PostgresDecisionRepository(pool), NoopEventBus())


@pytest.fixture
async def actor_id(pool) -> UUID:
    # risk_decision.tenant_id FKs to `tenant`(94da854f522f) — a plain
    # `users` row isn't enough once RiskDecisionRecorder writes WORM.
    return await create_test_tenant(pool)


def _repos(risk_gate_repo, cb, pool, recorder, **overrides) -> RecoveryGateRepos:
    kwargs = {"cooldown_sec": _COOLDOWN_SEC, "approval_ttl_sec": _APPROVAL_TTL_SEC, **overrides}
    return RecoveryGateRepos(
        risk_gate=risk_gate_repo,
        circuit_breaker=cb,
        get_approval_request=lambda approval_id: approval.get_request(pool, approval_id),
        decision_recorder=recorder,
        **kwargs,
    )


async def _make_halted_control(pool, risk_gate_repo, *, actor_id: UUID, elapsed_sec: int) -> UUID:
    """PROVIDER scope 'cb:halted' kill switch — R-45 배선과 같은 컨벤션.
    `created_at`을 뒤로 돌려 "마지막 트립 이후 경과 시간"을 결정론으로
    고정한다(실시간 대기 없음, test_circuit_breaker_loop.py와 동일 기법)."""
    control = await risk_gate_repo.insert_safety_control(
        scope=SafetyScope.PROVIDER,
        scope_ref="test-exch",
        reason="cb:halted",
        actor_subject_id=actor_id,
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE safety_control SET created_at = now() - make_interval(secs => $2) "
            "WHERE id = $1",
            control.id,
            float(elapsed_sec),
        )
    return control.id


async def _make_approved_request(pool) -> int:
    request = await approval.create_request(
        pool,
        scope="PLATFORM",
        trigger_source="test-r53",
        requested_action="REACTIVATE_TO_NORMAL",
        context={},
        approval_mode="SOLO",
    )
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE approval_requests SET created_at = now() - interval '181 seconds' "
            "WHERE id = $1",
            request.id,
        )
    await approval.approve(pool, request.id, uuid4())
    return request.id


async def test_evidence_missing_denies_rsk007_and_control_stays_active(
    pool, risk_gate_repo, cb, recorder, actor_id
):
    """DoD(a) — evidence_ref=None으로 halted 컨트롤 해제 요청 -> DENY RSK-007,
    control은 그대로 ACTIVE(자동 하향 0건, I5)."""
    control_id = await _make_halted_control(
        pool, risk_gate_repo, actor_id=actor_id, elapsed_sec=2000
    )
    approval_id = await _make_approved_request(pool)
    repos = _repos(risk_gate_repo, cb, pool, recorder)

    with pytest.raises(RecoveryDeniedError) as exc_info:
        await evaluate_recovery(
            repos,
            tenant_id=actor_id,
            control_id=control_id,
            evidence_ref=None,
            approval_id=approval_id,
            trace_id=uuid4(),
        )
    assert exc_info.value.details["reason_codes"] == ["RSK-007"]

    async with pool.acquire() as conn:
        state = await conn.fetchval("SELECT state FROM safety_control WHERE id = $1", control_id)
    assert state == "ACTIVE"


async def test_cooldown_899_seconds_denies_901_allows(pool, risk_gate_repo, cb, recorder, actor_id):
    """DoD(b) — cooldown_sec=900일 때 899초 경과는 DENY, 901초 경과는(다른
    조건 충족 시) ALLOW."""
    repos = _repos(risk_gate_repo, cb, pool, recorder)
    approval_id = await _make_approved_request(pool)

    short_control = await _make_halted_control(
        pool, risk_gate_repo, actor_id=actor_id, elapsed_sec=899
    )
    with pytest.raises(RecoveryDeniedError) as exc_info:
        await evaluate_recovery(
            repos,
            tenant_id=actor_id,
            control_id=short_control,
            evidence_ref="ev-1",
            approval_id=approval_id,
            trace_id=uuid4(),
        )
    assert exc_info.value.details["reason_codes"] == ["RECOVERY_COOLDOWN_NOT_MET"]

    long_control = await _make_halted_control(
        pool, risk_gate_repo, actor_id=actor_id, elapsed_sec=901
    )
    decision = await evaluate_recovery(
        repos,
        tenant_id=actor_id,
        control_id=long_control,
        evidence_ref="ev-1",
        approval_id=approval_id,
        trace_id=uuid4(),
    )
    assert decision.outcome == RiskOutcome.ALLOW

    async with pool.acquire() as conn:
        state = await conn.fetchval("SELECT state FROM safety_control WHERE id = $1", long_control)
    assert state == "INACTIVE"


@pytest.mark.parametrize("case", ["pending", "expired"])
async def test_approval_not_approved_denies(pool, risk_gate_repo, cb, recorder, actor_id, case):
    """DoD(c) — approval_id 상태가 APPROVED가 아니면(PENDING·만료 포함) DENY."""
    control_id = await _make_halted_control(
        pool, risk_gate_repo, actor_id=actor_id, elapsed_sec=2000
    )
    if case == "pending":
        request = await approval.create_request(
            pool,
            scope="PLATFORM",
            trigger_source="test-r53",
            requested_action="REACTIVATE_TO_NORMAL",
            context={},
            approval_mode="SOLO",
        )
        approval_id = request.id
    else:
        approval_id = await _make_approved_request(pool)
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE approval_requests SET resolved_at = now() - interval '10000 seconds' "
                "WHERE id = $1",
                approval_id,
            )
    repos = _repos(risk_gate_repo, cb, pool, recorder)

    with pytest.raises(RecoveryDeniedError) as exc_info:
        await evaluate_recovery(
            repos,
            tenant_id=actor_id,
            control_id=control_id,
            evidence_ref="ev-1",
            approval_id=approval_id,
            trace_id=uuid4(),
        )
    assert exc_info.value.details["reason_codes"] == ["RECOVERY_APPROVAL_NOT_APPROVED"]


async def test_one_decision_row_per_evaluation_and_worm_blocks_update(
    pool, risk_gate_repo, cb, recorder, actor_id
):
    """DoD(d) — 판정 1회당 risk_decision 행 정확히 1건, UPDATE는 WORM 트리거가 거부."""
    control_id = await _make_halted_control(
        pool, risk_gate_repo, actor_id=actor_id, elapsed_sec=2000
    )
    approval_id = await _make_approved_request(pool)
    repos = _repos(risk_gate_repo, cb, pool, recorder)

    decision = await evaluate_recovery(
        repos,
        tenant_id=actor_id,
        control_id=control_id,
        evidence_ref="ev-1",
        approval_id=approval_id,
        trace_id=uuid4(),
    )

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM risk_decision WHERE decision_id = $1", decision.decision_id
        )
        assert count == 1

        with pytest.raises(asyncpg.RaiseError, match="append-only violation"):
            async with conn.transaction():
                await conn.execute(
                    "UPDATE risk_decision SET outcome = 'DENY' WHERE decision_id = $1",
                    decision.decision_id,
                )


async def test_delegates_outcome_to_can_reactivate(
    monkeypatch, pool, risk_gate_repo, cb, recorder, actor_id
):
    """DoD(e) — 이 함수는 조립만 한다. `can_reactivate`를 항상 ALLOW로
    바꿔치면, 지역 조건문으로 재구현했다면 절대 통과 못 할 입력(evidence
    없음 + cooldown 미달)으로도 ALLOW가 나와야 위임이 실제로 쓰이고
    있다는 증거다."""
    control_id = await _make_halted_control(pool, risk_gate_repo, actor_id=actor_id, elapsed_sec=1)
    approval_id = await _make_approved_request(pool)
    repos = _repos(risk_gate_repo, cb, pool, recorder)

    monkeypatch.setattr(
        recovery_gate_module,
        "can_reactivate",
        lambda **kwargs: RecoveryDecision(outcome=RiskOutcome.ALLOW),
    )

    decision = await evaluate_recovery(
        repos,
        tenant_id=actor_id,
        control_id=control_id,
        evidence_ref=None,
        approval_id=approval_id,
        trace_id=uuid4(),
    )
    assert decision.outcome == RiskOutcome.ALLOW


def _fake_deny_decision(*, tenant_id: UUID) -> RiskDecision:
    now = datetime.now(timezone.utc)
    return RiskDecision(
        decision_id=uuid4(),
        gate_kind=GateKind.RECOVERY,
        tenant_id=tenant_id,
        execution_ref=None,
        subject_fingerprint="f" * 64,
        outcome=RiskOutcome.DENY,
        reason_codes=("RSK-007",),
        obligations=(),
        rule_results=(),
        rule_version="risk_gate.recovery/1",
        rule_hash="h" * 64,
        engine_version="risk_gate.recovery/1",
        inputs_hash="i" * 64,
        input_refs=(),
        evaluated_at=now,
        expires_at=now + timedelta(seconds=5),
        trace_id=uuid4(),
        evidence_ref=None,
        latency_us=1,
    )


def test_router_denied_maps_to_403_risk_denied_with_rsk007():
    """DoD(f) — DENY는 403(ErrorCode.RISK_DENIED)이고 taxonomy(RSK-007)가
    응답 details에 실린다. `map_exception`은 EXCEPTION_MAP(전역 핸들러)이
    실제로 쓰는 것과 같은 조회 경로다 — raw HTTPException을 라우터가 직접
    만들지 않는다는 계약을 이 경로로 검증한다."""
    decision = _fake_deny_decision(tenant_id=uuid4())

    code, _message, details = map_exception(RecoveryDeniedError(decision))

    assert code == ErrorCode.RISK_DENIED
    assert HTTP_STATUS[code] == 403
    assert details["reason_codes"] == ["RSK-007"]
