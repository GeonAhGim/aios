"""통합테스트 — task-3156 DEEPEN: PLT-07(task-906) `record_audit_log`/
`record_command_event` 실질 증빙.

원 리프(commit fa553a53, `tests/integration/api/test_middleware_trace.py`)의
유일한 "negative" 테스트
(`test_record_audit_log_defaults_trace_id_to_current_context_negative`)는
실제로는 아무 입력도 거부하지 않는다 — trace_id를 안 넘긴 두 호출이 서로
다른 값을 갖는다는 관찰일 뿐 거부 단언이 아니다. 실패 주입·수치 성능
단언·게이트 적색 재현도 0건이라 D2 하한 미달이었다.

이 파일은 원 파일을 건드리지 않고(500줄 loc 래칫 회귀 방지, ADR-2026-09-10-C
§7) audit_log의 VARCHAR 컬럼 제약과 record_command_event의 AUD-004
(`UnsafePayloadError`)가 실제로 쓰기를 거부하는 세 경로를 실DB로 고정하고,
repository 실패가 조용히 삼켜지지 않는지(fail-closed) 확인한 뒤, 단일 실DB
왕복 쓰기의 성능 예산을 수치로 건다. 새 규칙 추가 없이 이 리프의 테스트
증빙만 보강한다.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest

from src.core.logging.audit_log import record_audit_log
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.application.record_command_event import record_command_event
from src.foundation.evidence.domain.rules import UnsafePayloadError


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool() -> AsyncGenerator[asyncpg.Pool, None]:
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


async def test_record_audit_log_rejects_actor_agent_exceeding_column_length(pool: asyncpg.Pool) -> None:
    """negative: `actor_agent`는 VARCHAR(100)(9ec8a1ee28d7 마이그레이션) —
    101자를 넘기면 문자열이 조용히 잘려서 다른 행위자 이름으로 오기록되는
    게 아니라, DB가 INSERT 자체를 거부한다(fail-closed)."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.DataError):
            await record_audit_log(
                conn,
                actor_agent="x" * 101,
                action_type="test.plt07.reject",
                decision_data={},
            )


async def test_record_audit_log_rejects_action_type_exceeding_column_length(pool: asyncpg.Pool) -> None:
    """negative: `action_type`은 VARCHAR(50) — 51자를 넘기면 거부된다."""
    async with pool.acquire() as conn:
        with pytest.raises(asyncpg.DataError):
            await record_audit_log(
                conn,
                actor_agent="test-suite",
                action_type="y" * 51,
                decision_data={},
            )


async def test_record_command_event_rejects_unsafe_payload_key_and_persists_nothing(pool: asyncpg.Pool) -> None:
    """negative: AUD-004(79번 §2) — payload 키 이름이 secret류 패턴에 걸리면
    `UnsafePayloadError`로 거부되고, DB에는 아무 행도 남지 않는다
    (`assert_safe_payload`가 `append_event` 호출보다 먼저 실패하므로 부분
    기록이 없다)."""
    aggregate_id = uuid.uuid4()
    with pytest.raises(UnsafePayloadError):
        await record_command_event(
            PostgresAuditEventRepository(pool),
            tenant_id=None,
            aggregate_type="test.plt07.unsafe",
            aggregate_id=aggregate_id,
            action="trace_propagation",
            actor_subject_id=None,
            payload={"api_key": "should-not-be-stored"},
        )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT 1 FROM foundation_audit_event "
            "WHERE aggregate_type = 'test.plt07.unsafe' AND aggregate_id = $1",
            aggregate_id,
        )
    assert row is None


async def test_record_command_event_propagates_repository_failure_without_swallowing() -> None:
    """실패 주입: `append_event`가 커밋 도중 죽는 상황(커넥션 유실 등)을
    흉내낸다. `record_command_event`가 이 예외를 삼켜 "성공"처럼 보이는
    `AuditEventView`를 반환하면, `audit_log` 쪽엔 이미 기록됐는데
    `foundation_audit_event` 쪽은 조용히 비어 두 테이블의 trace_id
    상관관계가 끊긴 걸 아무도 모르게 된다(R1 위반) — 예외가 삼켜지지 않고
    그대로 위로 전파되는지 확인한다."""

    class _SimulatedDBFailure(Exception):
        pass

    class _ExplodingRepo:
        async def append_event(self, **kwargs: object) -> None:
            raise _SimulatedDBFailure("simulated connection loss mid-commit")

    with pytest.raises(_SimulatedDBFailure):
        await record_command_event(
            _ExplodingRepo(),  # type: ignore[arg-type]
            tenant_id=None,
            aggregate_type="test.plt07.failure",
            aggregate_id=uuid.uuid4(),
            action="trace_propagation",
            actor_subject_id=None,
        )


async def _record_audit_log_p95_ms(pool: asyncpg.Pool, *, n: int) -> float:
    durations_ms: list[float] = []
    async with pool.acquire() as conn:
        for _ in range(n):
            start = time.perf_counter()
            await record_audit_log(
                conn,
                actor_agent="test-suite",
                action_type="test.plt07.perf",
                decision_data={},
            )
            durations_ms.append((time.perf_counter() - start) * 1000)
    durations_ms.sort()
    return durations_ms[int(len(durations_ms) * 0.95)]


async def test_record_audit_log_write_p95_under_borrowed_single_roundtrip_budget(pool: asyncpg.Pool) -> None:
    """수치 성능 단언: ADR-2026-09-09-C Decision 1 예산표에 감사 로그 단일
    쓰기 전용 항목은 없다 — 가장 가까운 유사 항목("주문 제출→ACK p95
    50ms(paper)", 동일하게 단일 실DB 왕복 1회)을 자체 예산으로 차용한다
    (task-3148 PLT-22 DEEPEN과 동일 차용 근거)."""
    p95_ms = await _record_audit_log_p95_ms(pool, n=30)

    assert p95_ms < 50.0


async def test_record_audit_log_budget_gate_fails_on_injected_regression(pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> None:
    """게이트 적색 재현: 위 p95 단언이 실제로 회귀를 잡는지 확인한다 —
    `asyncpg.Connection.execute`에 60ms 인위 지연을 주입해, 같은 측정
    로직이 실제로 AssertionError를 내는지 본다(tautology가 아님을 증명)."""
    import asyncio

    original_execute = asyncpg.Connection.execute

    async def _slow_execute(self: asyncpg.Connection, *args: object, **kwargs: object) -> object:
        await asyncio.sleep(0.06)
        return await original_execute(self, *args, **kwargs)

    monkeypatch.setattr(asyncpg.Connection, "execute", _slow_execute)

    p95_ms = await _record_audit_log_p95_ms(pool, n=5)

    with pytest.raises(AssertionError):
        assert p95_ms < 50.0
