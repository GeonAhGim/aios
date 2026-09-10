"""R-53 통합테스트 — application/recovery_gate.py::evaluate_recovery.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#§9 R-53, §4.3 CB 표, I5(§8 394행).
`system_safety_state`(id=1, 전역 singleton)는 `tests/integration/risk/
test_circuit_breaker_loop.py`와 공유 TEST_DATABASE_URL을 쓴다 — `pool`
fixture가 매 테스트 시작 시 `normal`로 리셋해 오염을 막는다(그 파일의
동일 fixture와 같은 근거).
"""

from __future__ import annotations

import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import asyncpg
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.contracts.error_codes import HTTP_STATUS, ErrorCode
from src.api.contracts.exception_mapping import map_exception
from src.api.deps import get_current_admin
from src.core.approval import service as approval
from src.core.db.conditional_write import ConcurrencyConflictError
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
from src.main import app
from src.services.auth_service import User
from src.services.risk_decision_recorder import RiskDecisionRecorder
from tests.conftest import lifespan_context_with_retry
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


async def test_fresh_cb_level_still_reactivatable_denies_even_when_all_else_passes(
    pool, risk_gate_repo, cb, recorder, actor_id
):
    """negative — 4번째 조건(fresh 재평가)만 단독으로 걸리는 경우. evidence·
    cooldown·approval을 전부 충족시켜 놓고, 글로벌 `system_safety_state`를
    직접 HALTED로 돌려(R-45 tick 루프가 방금 다시 트립시킨 상황을 흉내)
    `CircuitBreakerService.get_state()`가 그 값을 그대로 돌려주게 만든다.
    이 브랜치는 기존 테스트 어디에도 없었다 — `evaluate_recovery`가 실제로
    이 라이브 상태를 조회해 `can_reactivate`에 넘긴다는 배선을 증명한다."""
    control_id = await _make_halted_control(
        pool, risk_gate_repo, actor_id=actor_id, elapsed_sec=2000
    )
    approval_id = await _make_approved_request(pool)
    repos = _repos(risk_gate_repo, cb, pool, recorder)

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE system_safety_state SET circuit_breaker_level = 'halted' WHERE id = 1"
        )

    with pytest.raises(RecoveryDeniedError) as exc_info:
        await evaluate_recovery(
            repos,
            tenant_id=actor_id,
            control_id=control_id,
            evidence_ref="ev-1",
            approval_id=approval_id,
            trace_id=uuid4(),
        )
    assert exc_info.value.details["reason_codes"] == ["RECOVERY_FRESH_RISK_DENY"]

    async with pool.acquire() as conn:
        state = await conn.fetchval("SELECT state FROM safety_control WHERE id = $1", control_id)
    assert state == "ACTIVE"


class _RecorderAlwaysFails:
    """WORM 삽입 자체가 DB 장애로 실패하는 상황을 흉내낸다 — 실제
    `RiskDecisionRecorder.record()`를 전혀 호출하지 않으므로, 판정이
    ALLOW였어도 WORM 행이 한 건도 생기지 않은 채로 실패가 전파돼야 한다."""

    async def record(self, decision, inputs, *, actor: str) -> None:
        raise ConnectionResetError("simulated WORM write outage")


async def test_worm_write_failure_propagates_and_control_stays_active(
    pool, risk_gate_repo, cb, actor_id
):
    """WORM 불변성 실패주입 — decision recorder(WORM 삽입 경로)가 DB 장애로
    실패하면, 4가지 조건을 전부 만족해 판정이 ALLOW였더라도 그 실패가
    삼켜지지 않고 그대로 전파되며 `deactivate_safety_control`은 절대
    호출되지 않는다(recovery_gate.py 217~224행 순서 — WORM 기록이 해제보다
    먼저다). 영속 근거(WORM) 없이 실효(해제)만 먼저 발생하는 경로가 없다는
    불변식을 증명한다."""
    control_id = await _make_halted_control(
        pool, risk_gate_repo, actor_id=actor_id, elapsed_sec=2000
    )
    approval_id = await _make_approved_request(pool)
    repos = _repos(risk_gate_repo, cb, pool, _RecorderAlwaysFails())

    with pytest.raises(ConnectionResetError):
        await evaluate_recovery(
            repos,
            tenant_id=actor_id,
            control_id=control_id,
            evidence_ref="ev-1",
            approval_id=approval_id,
            trace_id=uuid4(),
        )

    async with pool.acquire() as conn:
        state = await conn.fetchval("SELECT state FROM safety_control WHERE id = $1", control_id)
        decision_count = await conn.fetchval(
            "SELECT count(*) FROM risk_decision WHERE tenant_id = $1", actor_id
        )
    assert state == "ACTIVE"
    assert decision_count == 0


async def test_concurrent_recovery_instances_only_one_deactivates_control(
    pool, risk_gate_repo, cb, recorder, actor_id
):
    """다중 인스턴스 증명(D3) — 서로 다른 서비스 인스턴스를 흉내낸 두 개의
    독립된 `RecoveryGateRepos`(각자 새로 만든 `PostgresRiskGateRepository`)가
    같은 halted control을 동시에 재가동 평가한다. 둘 다 같은 ACTIVE 상태를
    읽어 `can_reactivate`가 둘 다 ALLOW를 내리지만,
    `deactivate_safety_control`의 조건부 UPDATE(`WHERE state='ACTIVE'`,
    105번 표준)는 정확히 한쪽만 통과시킨다 — 진 쪽은 `ConcurrencyConflictError`
    로 실패가 그대로 전파돼야 한다(이중 해제 없음). 두 쪽 모두 WORM에는 각자의
    ALLOW 판정을 남긴다 — 레코딩은 해제 성공 여부와 무관하게 먼저 일어난다."""
    control_id = await _make_halted_control(
        pool, risk_gate_repo, actor_id=actor_id, elapsed_sec=2000
    )
    approval_id = await _make_approved_request(pool)

    repos_a = _repos(PostgresRiskGateRepository(pool), cb, pool, recorder)
    repos_b = _repos(PostgresRiskGateRepository(pool), cb, pool, recorder)

    async def _eval(repos):
        return await evaluate_recovery(
            repos,
            tenant_id=actor_id,
            control_id=control_id,
            evidence_ref="ev-1",
            approval_id=approval_id,
            trace_id=uuid4(),
        )

    results = await asyncio.gather(_eval(repos_a), _eval(repos_b), return_exceptions=True)

    successes = [r for r in results if isinstance(r, RiskDecision)]
    failures = [r for r in results if isinstance(r, BaseException)]
    assert len(successes) == 1
    assert successes[0].outcome == RiskOutcome.ALLOW
    assert len(failures) == 1
    assert isinstance(failures[0], ConcurrencyConflictError)

    async with pool.acquire() as conn:
        state = await conn.fetchval("SELECT state FROM safety_control WHERE id = $1", control_id)
        rows = await conn.fetch("SELECT outcome FROM risk_decision WHERE tenant_id = $1", actor_id)
    assert state == "INACTIVE"
    assert [r["outcome"] for r in rows] == ["ALLOW", "ALLOW"]


@pytest.mark.perf
async def test_evaluate_recovery_latency_within_normalized_budget(
    pool, risk_gate_repo, cb, recorder, actor_id
):
    """성능 단언 — RECOVERY 게이트 평가(ALLOW) 1회의 p95 지연을 같은 연결의
    기준 왕복비용(`SELECT 1`)에 정규화한 임계와 비교한다(절대 ms 상수 회피,
    tests/adversarial/risk/test_tick_mandate_fence_staleness.py 관례 재사용).
    각 반복마다 새 halted control을 미리 만들어, 측정 구간에는 평가 자체의
    비용만 들어가게 한다."""
    approval_id = await _make_approved_request(pool)
    repos = _repos(risk_gate_repo, cb, pool, recorder)
    reps = 15
    control_ids = [
        await _make_halted_control(pool, risk_gate_repo, actor_id=actor_id, elapsed_sec=2000)
        for _ in range(reps)
    ]

    async def _p95_ms(step) -> float:
        samples = []
        for _ in range(reps):
            t0 = time.perf_counter()
            await step()
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        return samples[int(len(samples) * 0.95) - 1]

    async with pool.acquire() as conn:
        baseline_p95 = await _p95_ms(lambda: conn.fetchval("SELECT 1"))

    index = {"i": 0}

    async def _one_eval() -> None:
        control_id = control_ids[index["i"]]
        index["i"] += 1
        await evaluate_recovery(
            repos,
            tenant_id=actor_id,
            control_id=control_id,
            evidence_ref="ev-1",
            approval_id=approval_id,
            trace_id=uuid4(),
        )

    gate_p95 = await _p95_ms(_one_eval)

    # evaluate_recovery는 SELECT 1 왕복보다 훨씬 많은 순차 왕복(control 조회·
    # approval 조회·cb 상태 조회·WORM 기록·해제 커맨드)을 거친다 — 같은
    # 디렉터리의 다른 테스트가 먼저 쌓아 둔 테이블 크기에 따라 관측치가
    # 커질 수 있어 여유를 크게 둔다.
    budget_ms = max(600.0, 150.0 * baseline_p95)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"evaluate_recovery p95={gate_p95:.3f}ms baseline(SELECT 1) p95={baseline_p95:.3f}ms "
        f"budget={budget_ms:.3f}ms"
    )
    assert gate_p95 < budget_ms


@pytest.fixture
async def http_client():
    async with lifespan_context_with_retry(app):
        # raise_app_exceptions=False — 도메인 예외는 전역 핸들러가 봉투로
        # 번역하고 Starlette가 정상 처리된 뒤에도 원본을 재전파하므로
        # (tests/integration/api/test_positions_router.py의 client 픽스처와
        # 같은 근거).
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            yield ac


async def test_router_recovery_denied_end_to_end_returns_403_rsk007_envelope(
    pool, risk_gate_repo, actor_id, http_client
):
    """DENY→403 매핑 게이트 적색 재현 — `test_router_denied_maps_to_403_...`는
    `map_exception`을 직접 호출해 매핑 테이블 항목만 검증했을 뿐, 실제 라우터
    가 그 예외를 던지고 전역 핸들러(`install_exception_handlers`)가 그걸
    받아 403 봉투로 바꾸는 전체 배선은 아무도 재현하지 않았다. 이 테스트는
    진짜 FastAPI 앱에 실제 HTTP 요청을 보내 그 전체 경로를 end-to-end로
    재현한다 — 라우터가 raw HTTPException을 쓰지 않는다는 계약이 배선까지
    깨지지 않았음을 증명한다."""
    control_id = await _make_halted_control(
        pool, risk_gate_repo, actor_id=actor_id, elapsed_sec=2000
    )
    approval_id = await _make_approved_request(pool)

    fake_admin = User(
        user_id=actor_id,
        email="admin@example.com",
        display_name=None,
        mfa_enabled=False,
        mfa_verified_at=None,
        status="ACTIVE",
        is_verifier=False,
        is_platform_admin=True,
    )
    app.dependency_overrides[get_current_admin] = lambda: fake_admin
    try:
        response = await http_client.post(
            f"/v1/foundation/risk-gate/safety-controls/{control_id}:evaluate-recovery",
            json={"evidence_ref": None, "approval_id": approval_id},
        )
    finally:
        app.dependency_overrides.pop(get_current_admin, None)

    assert response.status_code == 403
    body = response.json()
    assert body["error_code"] == "RISK_DENIED"
    assert body["details"]["reason_codes"] == ["RSK-007"]

    async with pool.acquire() as conn:
        state = await conn.fetchval("SELECT state FROM safety_control WHERE id = $1", control_id)
    assert state == "ACTIVE"
