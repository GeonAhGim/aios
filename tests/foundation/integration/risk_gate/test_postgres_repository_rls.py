"""F1(M) 안정화 감사(task-9420/9457) — risk_gate repository의 risk_evaluation
read/write가 실제로 `tenant_transaction()`(app.tenant_id GUC)을 거치는지 검증.

Spec: docs/audits/AUDIT_2026-09-30_auth_rls.md F1, docs/design/INVARIANTS.md
I-10("구현됨 ≠ 작동함" — 배선 증명 테스트 필수).

`risk_evaluation`은 b3c7f19ad2e6/c9f4e2a1b6d7이 RLS ENABLE+FORCE한 8개
foundation 테이블 중 하나다. 이 테스트는 `tests/integration/core/db/
test_rls_foundation.py`의 `AppRoleTx`로 SQL을 직접 실행해 정책만 확인하는
방식을 쓰지 않는다 — 대신 `PostgresRiskGateRepository`의 실제
insert_evaluation/get_cached_evaluation 메서드를, 연결마다 `SET ROLE
aios_app`을 거치는 전용 풀로 호출해 리포지토리 코드 경로 자체가 RLS 아래에서
동작하는지(F1 재현: GUC 없으면 0행, GUC 주입 후 1행) 검증한다.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.foundation.risk_gate.adapters import postgres_repository as repo_module
from src.foundation.risk_gate.adapters.postgres_repository import PostgresRiskGateRepository
from src.foundation.risk_gate.domain.models import GateKind, RiskEvaluation, RiskOutcome
from tests.integration.conftest import create_test_tenant

# 예산표(ADR-2026-09-09-C Decision 1)에 전용 항목이 없어
# test_rls_foundation.py와 동일 근거로 "주문 제출→ACK p95 50ms(paper)"를
# 가장 가까운 유사 항목으로 차용한다.
_RLS_READ_P95_BUDGET_MS = 50.0


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    """owner/superuser 풀 — 시드 데이터 삽입 및 비교용 베이스라인에 쓴다."""
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=2, max_size=8)
    yield p
    await p.close()


@pytest.fixture
async def app_role_pool():
    """매 acquire마다 `SET ROLE aios_app`을 거치는 전용 풀 — 이 풀로 생성한
    `PostgresRiskGateRepository`는 더 이상 슈퍼유저가 아니므로, repository가
    tenant_transaction()으로 GUC를 바인딩하지 않으면 RLS가 실제로 행을
    가린다(c9f4e2a1b6d7 docstring — 슈퍼유저만 FORCE RLS를 무조건 우회)."""

    async def _set_app_role(conn: asyncpg.Connection) -> None:
        await conn.execute("SET ROLE aios_app")

    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4, setup=_set_app_role)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresRiskGateRepository(pool)


def _unbound_tenant_transaction(pool_: asyncpg.Pool, _tenant_id):
    """F1 발견 당시의 결함 코드 경로를 그대로 재현한다 — `app.tenant_id`
    GUC를 전혀 바인딩하지 않고 커넥션만 빌려주는 `pool.acquire()`."""
    return pool_.acquire()


async def _seed_evaluation(pool_: asyncpg.Pool, *, tenant_id, fingerprint: str) -> None:
    async with pool_.acquire() as conn:
        await conn.execute(
            "INSERT INTO risk_evaluation "
            "(tenant_id, gate_kind, subject_fingerprint, outcome, reason_codes, "
            " obligations, rule_version, expires_at, trace_id) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, NULL, $8)",
            tenant_id,
            GateKind.DEPLOYMENT.value,
            fingerprint,
            RiskOutcome.ALLOW.value,
            [],
            [],
            "v1",
            uuid4(),
        )


async def test_get_cached_evaluation_without_guc_binding_returns_none_regression(
    pool, app_role_pool, monkeypatch: pytest.MonkeyPatch
):
    """negative #1 — F1 재현: `tenant_transaction()`을 쓰지 않고 맨 `pool.
    acquire()`만 쓰는(수정 전) 경로로 되돌리면, 같은 역할(aios_app)·같은
    테넌트 조회가 0행(=None)이 된다. 이 테스트는 그 회귀를 고정한다 — 누가
    나중에 GUC 바인딩을 다시 빼면 이 테스트가 적색이 된다."""
    monkeypatch.setattr(repo_module, "tenant_transaction", _unbound_tenant_transaction)
    tenant_a = await create_test_tenant(pool)
    fingerprint = f"fp-{uuid4().hex}"
    await _seed_evaluation(pool, tenant_id=tenant_a, fingerprint=fingerprint)

    broken_repo = PostgresRiskGateRepository(app_role_pool)
    result = await broken_repo.get_cached_evaluation(tenant_a, fingerprint)

    assert result is None


async def test_get_cached_evaluation_with_guc_binding_returns_own_row(pool, app_role_pool):
    """positive — 수정된 경로(`tenant_transaction()` 사용)는 같은
    aios_app 역할 아래에서도 테넌트 자신의 행을 1건 돌려준다(F1 수정
    확인: GUC 주입 후 동일 조회 1행)."""
    tenant_a = await create_test_tenant(pool)
    fingerprint = f"fp-{uuid4().hex}"
    await _seed_evaluation(pool, tenant_id=tenant_a, fingerprint=fingerprint)

    fixed_repo = PostgresRiskGateRepository(app_role_pool)
    result = await fixed_repo.get_cached_evaluation(tenant_a, fingerprint)

    assert result is not None
    assert result.tenant_id == tenant_a
    assert result.subject_fingerprint == fingerprint


async def test_get_cached_evaluation_cross_tenant_guc_returns_none(pool, app_role_pool):
    """negative #2 — 교차 테넌트 격리: tenant A GUC로 tenant B의 행을
    조회하면 0행(None)이어야 한다."""
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    fingerprint = f"fp-{uuid4().hex}"
    await _seed_evaluation(pool, tenant_id=tenant_b, fingerprint=fingerprint)

    fixed_repo = PostgresRiskGateRepository(app_role_pool)
    result = await fixed_repo.get_cached_evaluation(tenant_a, fingerprint)

    assert result is None


async def test_insert_evaluation_under_app_role_persists_and_is_tenant_scoped(pool, app_role_pool):
    """write 경로도 같은 계약으로 이관됐는지 확인 — aios_app 역할로
    insert_evaluation()을 호출해도(WITH CHECK 정책 평가 통과) 저장되고,
    저장된 행은 그 테넌트 자신만 다시 읽을 수 있다."""
    tenant_a = await create_test_tenant(pool)
    fingerprint = f"fp-{uuid4().hex}"
    evaluation = RiskEvaluation(
        id=uuid4(),
        tenant_id=tenant_a,
        gate_kind=GateKind.DEPLOYMENT,
        subject_fingerprint=fingerprint,
        outcome=RiskOutcome.ALLOW,
        reason_codes=(),
        obligations=(),
        rule_version="v1",
        evaluated_at=datetime.now(timezone.utc),
        expires_at=None,
        trace_id=uuid4(),
    )
    fixed_repo = PostgresRiskGateRepository(app_role_pool)

    inserted = await fixed_repo.insert_evaluation(evaluation)
    assert inserted.tenant_id == tenant_a

    fetched = await fixed_repo.get_cached_evaluation(tenant_a, fingerprint)
    assert fetched is not None
    assert fetched.id == inserted.id


async def test_invalidate_evaluations_for_tenant_under_app_role_only_deletes_own_rows(
    pool, app_role_pool
):
    """tenant 범위 invalidate도 tenant_transaction 계약을 타며, 다른
    테넌트의 캐시 행은 지우지 못한다(교차 테넌트 쓰기 격리)."""
    tenant_a = await create_test_tenant(pool)
    tenant_b = await create_test_tenant(pool)
    fp_a = f"fp-{uuid4().hex}"
    fp_b = f"fp-{uuid4().hex}"
    await _seed_evaluation(pool, tenant_id=tenant_a, fingerprint=fp_a)
    await _seed_evaluation(pool, tenant_id=tenant_b, fingerprint=fp_b)

    fixed_repo = PostgresRiskGateRepository(app_role_pool)
    await fixed_repo.invalidate_evaluations(tenant_id=tenant_a)

    async with pool.acquire() as conn:
        remaining_a = await conn.fetchval(
            "SELECT count(*) FROM risk_evaluation WHERE tenant_id = $1", tenant_a
        )
        remaining_b = await conn.fetchval(
            "SELECT count(*) FROM risk_evaluation WHERE tenant_id = $1", tenant_b
        )
    assert remaining_a == 0
    assert remaining_b == 1


async def test_get_cached_evaluation_guc_binding_failure_raises_without_fallback(
    pool, app_role_pool, monkeypatch: pytest.MonkeyPatch
):
    """failure-injection — GUC 바인딩(`set_config`) 자체가 예외를 내면
    repository는 그 예외를 그대로 전파해야 한다. fail-closed 기본(CLAUDE.md
    §3) — GUC 없이 조용히 커넥션을 넘겨 RLS 없는 조회가 일어나는 경로가
    있으면 안 된다."""
    tenant_a = await create_test_tenant(pool)
    fingerprint = f"fp-{uuid4().hex}"
    await _seed_evaluation(pool, tenant_id=tenant_a, fingerprint=fingerprint)

    original_execute = asyncpg.Connection.execute

    async def _raise_on_set_config(self, query, *args, **kwargs):
        if "set_config" in query:
            raise RuntimeError("injected GUC binding failure")
        return await original_execute(self, query, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "execute", _raise_on_set_config)

    fixed_repo = PostgresRiskGateRepository(app_role_pool)
    with pytest.raises(RuntimeError, match="injected GUC binding failure"):
        await fixed_repo.get_cached_evaluation(tenant_a, fingerprint)


async def _read_p95_ms(
    repo_: PostgresRiskGateRepository, tenant_id, fingerprint: str, *, n: int
) -> float:
    durations_ms: list[float] = []
    for _ in range(n):
        start = time.perf_counter()
        await repo_.get_cached_evaluation(tenant_id, fingerprint)
        durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_get_cached_evaluation_p95_under_borrowed_order_ack_budget(pool, app_role_pool):
    """수치 성능 단언: tenant_transaction 경유(SET ROLE + GUC 바인딩 + 정책
    평가 포함 단일 실DB 왕복) 조회의 p95가 차용 예산(50ms, 위 상수) 안에
    있어야 한다."""
    tenant_a = await create_test_tenant(pool)
    fingerprint = f"fp-{uuid4().hex}"
    await _seed_evaluation(pool, tenant_id=tenant_a, fingerprint=fingerprint)
    fixed_repo = PostgresRiskGateRepository(app_role_pool)

    p95_ms = await _read_p95_ms(fixed_repo, tenant_a, fingerprint, n=30)

    assert p95_ms < _RLS_READ_P95_BUDGET_MS
