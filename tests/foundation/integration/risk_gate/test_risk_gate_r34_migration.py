"""R-34: f4b9d6e5a7c8 (gate_kind 6종 + trace_id + idempotency_digest +
paused_by_control_id) -- split out of test_risk_gate_lifecycle.py to keep
that file under the loc_over_1000 ratchet (CLAUDE.md §7)."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.core.observability.context import bind as bind_request_context
from src.foundation.risk_gate.application.evaluate_risk_gate import evaluate_risk_gate
from src.foundation.risk_gate.domain.models import GateKind
from tests.foundation.integration.risk_gate.conftest import _tenant, activate_mandate_with_defaults
from tests.support.db import ensure_worker_database, template_database_url
from tests.support.deep_downgrade import purge_position_snapshots

_PROJECT_ROOT = Path(__file__).resolve().parents[4]


def _run_alembic(*args: str, database_url: str | None = None) -> None:
    env = {**os.environ, "DATABASE_URL": database_url} if database_url else None
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )


async def _gate_kind_check_def(pool: asyncpg.Pool) -> str:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
            "WHERE conrelid = 'risk_evaluation'::regclass "
            "AND conname = 'risk_evaluation_gate_kind_check'"
        )
    assert row is not None
    return row["def"]


async def _column_exists(pool: asyncpg.Pool, table_name: str, column_name: str) -> bool:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = $1 AND column_name = $2",
            table_name,
            column_name,
        )
    return value is not None


async def test_gate_kind_check_rejects_value_outside_the_six(pool):
    """DoD(2)/(4) — 6종 목록 밖의 값은 CHECK로 거부돼야 한다(임의 추가·개명
    금지의 반대 방향 검증: 목록에 없는 값도 통과시키면 안 된다)."""
    tenant_id = await _tenant(pool)
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "INSERT INTO risk_evaluation "
                "(tenant_id, gate_kind, subject_fingerprint, outcome, rule_version, expires_at) "
                "VALUES ($1, 'NOT_A_GATE_KIND', 'fp', 'ALLOW', 'v1', now())",
                tenant_id,
            )


async def test_gate_kind_check_accepts_all_six_values(pool):
    """DoD(4) — 6종 전부가 실제로 통과해야 한다(명세 §3.8/§5 열거 그대로).

    삽입한 행은 테스트가 끝나면 반드시 지운다 — PRE_TRADE/PRE_SUBMIT/
    INTRADAY/RECOVERY 값 행이 커밋된 채 남으면, 옛 2종 CHECK로 되돌아가는
    `test_migration_round_trip_restores_gate_kinds_and_new_columns`의
    downgrade가 그 행들 때문에 실패한다(공유 테스트 DB)."""
    tenant_id = await _tenant(pool)
    gate_kinds = ("DEPLOYMENT", "PRE_INTENT", "PRE_TRADE", "PRE_SUBMIT", "INTRADAY", "RECOVERY")
    inserted_ids: list[UUID] = []
    try:
        for gate_kind in gate_kinds:
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    "INSERT INTO risk_evaluation "
                    "(tenant_id, gate_kind, subject_fingerprint, outcome, rule_version, "
                    "expires_at) "
                    "VALUES ($1, $2, $3, 'ALLOW', 'v1', now()) RETURNING id",
                    tenant_id,
                    gate_kind,
                    f"fp-{gate_kind}",
                )
            assert row is not None
            inserted_ids.append(row["id"])
    finally:
        if inserted_ids:
            async with pool.acquire() as conn:
                await conn.execute(
                    "DELETE FROM risk_evaluation WHERE id = ANY($1::uuid[])", inserted_ids
                )


async def test_idempotency_digest_unique_rejects_duplicate(pool):
    """DoD(2) — safety_control.idempotency_digest UNIQUE 위반은 거부돼야 한다."""
    tenant_id = await _tenant(pool)
    # tenant_id로 매 실행마다 다른 값 — 고정 문자열이면 이전 실행이 커밋해
    # 남긴 행과 재실행 시 충돌한다.
    digest = hashlib.sha256(f"r-34-dup-digest:{tenant_id}".encode()).hexdigest()
    async with pool.acquire() as conn:
        try:
            await conn.execute(
                "INSERT INTO safety_control "
                "(scope, scope_ref, reason, actor_subject_id, fence_token, idempotency_digest) "
                "VALUES ('ACCOUNT', $1, 'r1', $2, 1, $3)",
                str(tenant_id),
                tenant_id,
                digest,
            )
            with pytest.raises(asyncpg.UniqueViolationError):
                await conn.execute(
                    "INSERT INTO safety_control "
                    "(scope, scope_ref, reason, actor_subject_id, fence_token, idempotency_digest) "
                    "VALUES ('ACCOUNT', $1, 'r2', $2, 2, $3)",
                    str(tenant_id),
                    tenant_id,
                    digest,
                )
        finally:
            await conn.execute("DELETE FROM safety_control WHERE idempotency_digest = $1", digest)


async def test_paused_by_control_id_fk_rejects_unknown_control(pool):
    """DoD(2) — strategy_executions.paused_by_control_id FK는 존재하지 않는
    safety_control.id를 거부해야 한다."""
    tenant_id = await _tenant(pool)
    strategy_id = f"r34-test-{uuid4().hex[:8]}"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategies "
            "(strategy_id, version, owner_user_id, target_asset, market, exchange, "
            " fsm_definition, author_agent, lifecycle_status) "
            "VALUES ($1, '1.0.0', $2, 'BTC/USDT', 'crypto', 'bitget', $3::jsonb, "
            " 'test-author', 'APPROVED')",
            strategy_id,
            tenant_id,
            json.dumps({}),
        )
        with pytest.raises(asyncpg.ForeignKeyViolationError):
            await conn.execute(
                "INSERT INTO strategy_executions "
                "(strategy_id, strategy_version, user_id, exchange, mode, "
                " allocated_capital, currency, status, paused_by_control_id) "
                "VALUES ($1, '1.0.0', $2, 'bitget', 'PAPER', 1000, 'USDT', 'RUNNING', $3)",
                strategy_id,
                tenant_id,
                uuid4(),
            )


async def test_evaluate_risk_gate_always_records_trace_id_from_context(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """DoD(3) — evaluate_risk_gate()의 신규 경로는 PLT-01 관측 컨텍스트의
    trace_id를 항상 채워 risk_evaluation에 기록한다."""
    tenant_id = await _tenant(pool)
    await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    with bind_request_context() as ctx:
        expected_trace_id = ctx.trace_id
        result = await evaluate_risk_gate(
            repo, mandate_repo, connection_repo, tenant_id=tenant_id, gate_kind=GateKind.DEPLOYMENT
        )

    assert result.trace_id == expected_trace_id
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT trace_id FROM risk_evaluation WHERE id = $1", result.id)
    assert row is not None
    assert row["trace_id"] == expected_trace_id


async def test_migration_round_trip_restores_gate_kinds_and_new_columns():
    """DoD(2) — upgrade→downgrade→upgrade 왕복을 실DB로 재현: downgrade는
    3개 신규 컬럼을 지우고 CHECK를 옛 2종으로 되돌리며, 재차 upgrade하면
    정확히 원래(6종 + 3개 컬럼) 상태로 복원돼야 한다. Disposable DB clone
    (task-5783) -- never the shared session DB other tests and
    `scripts/replay_verify.py` depend on. An interrupted downgrade can only
    corrupt its own throwaway DB."""
    migration_db_url = await ensure_worker_database(template_database_url(), "migrationrt_fnd06")
    migration_pool = await asyncpg.create_pool(
        migration_db_url.replace("postgresql+asyncpg://", "postgresql://"),
        min_size=1,
        max_size=2,
    )
    try:
        before = await _gate_kind_check_def(migration_pool)
        assert "PRE_SUBMIT" in before
        assert "INTRADAY" in before
        assert "RECOVERY" in before
        assert await _column_exists(migration_pool, "risk_evaluation", "trace_id")
        assert await _column_exists(migration_pool, "safety_control", "idempotency_digest")
        assert await _column_exists(migration_pool, "strategy_executions", "paused_by_control_id")

        # deep downgrade: see tests/support/deep_downgrade.py
        await purge_position_snapshots(migration_pool)
        _run_alembic("downgrade", "c7e6a3b2d4f5", database_url=migration_db_url)

        after_downgrade = await _gate_kind_check_def(migration_pool)
        assert "PRE_SUBMIT" not in after_downgrade
        assert "INTRADAY" not in after_downgrade
        assert "RECOVERY" not in after_downgrade
        assert not await _column_exists(migration_pool, "risk_evaluation", "trace_id")
        assert not await _column_exists(migration_pool, "safety_control", "idempotency_digest")
        assert not await _column_exists(
            migration_pool, "strategy_executions", "paused_by_control_id"
        )

        _run_alembic("upgrade", "head", database_url=migration_db_url)

        after_upgrade = await _gate_kind_check_def(migration_pool)
        assert after_upgrade == before
        assert await _column_exists(migration_pool, "risk_evaluation", "trace_id")
        assert await _column_exists(migration_pool, "safety_control", "idempotency_digest")
        assert await _column_exists(migration_pool, "strategy_executions", "paused_by_control_id")
    finally:
        await migration_pool.close()


@pytest.mark.perf
async def test_idempotency_digest_unique_violation_detection_stays_fast_at_scale(pool):
    """성능 단언(D2) — safety_control.idempotency_digest UNIQUE는 인덱스를
    타야 한다. 인덱스 없이 순차 스캔이면 위반 감지 시간이 기존 행 수에
    비례해 늘어난다. 200개의 서로 다른 digest를 먼저 채운 뒤, 그중 하나를
    중복 삽입하는 시도가 여전히 짧은 시간 안에 거부되는지 확인한다(웜업
    1회로 최초 쿼리플랜 컴파일 비용을 측정에서 제외)."""
    tenant_id = await _tenant(pool)
    digests = [
        hashlib.sha256(f"r-34-perf-{tenant_id}-{i}".encode()).hexdigest() for i in range(200)
    ]
    async with pool.acquire() as conn:
        try:
            for i, digest in enumerate(digests):
                await conn.execute(
                    "INSERT INTO safety_control "
                    "(scope, scope_ref, reason, actor_subject_id, fence_token, idempotency_digest) "
                    "VALUES ('ACCOUNT', $1, 'perf-scale-fill', $2, $3, $4)",
                    str(tenant_id),
                    tenant_id,
                    i + 1,
                    digest,
                )

            async def _duplicate_insert(digest: str, fence: int) -> None:
                with pytest.raises(asyncpg.UniqueViolationError):
                    await conn.execute(
                        "INSERT INTO safety_control "
                        "(scope, scope_ref, reason, actor_subject_id, fence_token, "
                        " idempotency_digest) "
                        "VALUES ('ACCOUNT', $1, 'perf-scale-dup', $2, $3, $4)",
                        str(tenant_id),
                        tenant_id,
                        fence,
                        digest,
                    )

            await _duplicate_insert(digests[1], 9001)  # 웜업 — 계획 캐시 컴파일 제외

            start = time.perf_counter()
            await _duplicate_insert(digests[0], 9002)
            elapsed = time.perf_counter() - start
            assert elapsed < 0.5, (
                f"UNIQUE 위반 감지가 {elapsed:.3f}s 걸렸다 — 인덱스 미사용(순차 스캔) 의심"
            )
        finally:
            await conn.execute("DELETE FROM safety_control WHERE actor_subject_id = $1", tenant_id)


async def test_concurrent_replay_with_same_idempotency_digest_only_one_instance_wins(pool):
    """다중 인스턴스 리플레이 경합(D3) — §5 "요청 Idempotency-Key"는 여러
    앱 인스턴스가 네트워크 재시도로 동시에 같은 activate 요청을 다시 보내는
    상황을 막기 위한 것이다. 10개의 동시 삽입이 정확히 같은
    idempotency_digest를 갖고 경합해도 UNIQUE 제약이 정확히 하나만 통과시켜야
    한다(나머지는 유실이 아니라 명시적 거부)."""
    tenant_id = await _tenant(pool)
    digest = hashlib.sha256(f"r-34-concurrent-replay:{tenant_id}".encode()).hexdigest()

    async def _try_insert(fence: int) -> bool:
        async with pool.acquire() as conn:
            try:
                await conn.execute(
                    "INSERT INTO safety_control "
                    "(scope, scope_ref, reason, actor_subject_id, fence_token, "
                    " idempotency_digest) "
                    "VALUES ('ACCOUNT', $1, 'concurrent-replay', $2, $3, $4)",
                    str(tenant_id),
                    tenant_id,
                    fence,
                    digest,
                )
                return True
            except asyncpg.UniqueViolationError:
                return False

    try:
        results = await asyncio.gather(*[_try_insert(i) for i in range(10)])
        assert results.count(True) == 1, "동시 재시도(다중 인스턴스) 중 정확히 하나만 성공해야 한다"
        assert results.count(False) == 9
    finally:
        async with pool.acquire() as conn:
            await conn.execute("DELETE FROM safety_control WHERE actor_subject_id = $1", tenant_id)


async def test_concurrent_evaluate_risk_gate_calls_never_cross_contaminate_trace_id(
    pool, repo, mandate_repo, trust_repo, connection_repo
):
    """적대적/동시성 증명(D3) — `context.py` 모듈독스트링대로 asyncio 태스크는
    생성 시점의 ContextVar를 복제해 상속한다. 여러 tenant의
    `evaluate_risk_gate()` 호출을 동시에 실행해도, 각자 자신이 `bind()`한
    trace_id만 기록해야 한다 — 한 요청의 trace_id가 다른 요청의
    `risk_evaluation` 행으로 새어 들어가면 §3.8 감사 추적 자체가 오염된다."""
    tenants = [await _tenant(pool) for _ in range(5)]
    for tenant_id in tenants:
        await activate_mandate_with_defaults(mandate_repo, trust_repo, tenant_id=tenant_id)

    async def _evaluate(tenant_id: UUID) -> tuple[UUID, str]:
        with bind_request_context() as ctx:
            result = await evaluate_risk_gate(
                repo,
                mandate_repo,
                connection_repo,
                tenant_id=tenant_id,
                gate_kind=GateKind.DEPLOYMENT,
            )
            assert result.outcome.value == "ALLOW"
            return ctx.trace_id, result.trace_id

    pairs = await asyncio.gather(*[_evaluate(t) for t in tenants])
    for expected_trace_id, recorded_trace_id in pairs:
        assert recorded_trace_id == expected_trace_id

    all_trace_ids = [expected for expected, _ in pairs]
    assert len(set(all_trace_ids)) == len(all_trace_ids), (
        "서로 다른 동시 요청이 같은 trace_id를 공유했다 — 컨텍스트 격리 실패"
    )
