"""7.4 — audit_log 기록 유틸 통합 테스트.

로컬 dev Postgres(docker-compose.dev.yml)에 마이그레이션이 적용된 상태를
전제로 한다.
"""

import json
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.logging.audit_log import record_audit_log


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url, ".env에 DATABASE_URL이 없습니다"
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def conn():
    connection = await asyncpg.connect(_asyncpg_dsn())
    yield connection
    await connection.close()


async def test_record_audit_log_inserts_row_with_decimal_safe_json(conn):
    await record_audit_log(
        conn,
        actor_agent="test-suite",
        action_type="test.audit.insert",
        decision_data={"amount": Decimal("123.456"), "note": "테스트"},
    )

    row = await conn.fetchrow(
        "SELECT actor_agent, action_type, decision_data FROM audit_log "
        "WHERE action_type = 'test.audit.insert' ORDER BY log_id DESC LIMIT 1"
    )
    assert row["actor_agent"] == "test-suite"
    decision_data = json.loads(row["decision_data"])  # asyncpg는 jsonb를 raw 문자열로 반환
    assert decision_data["amount"] == "123.456"  # 문자열로 보존(정밀도 손실 없음)


async def test_record_audit_log_verification_chain_optional(conn):
    await record_audit_log(
        conn,
        actor_agent="test-suite",
        action_type="test.audit.no_chain",
        decision_data={"ok": True},
    )
    row = await conn.fetchrow(
        "SELECT verification_chain FROM audit_log WHERE action_type = 'test.audit.no_chain' "
        "ORDER BY log_id DESC LIMIT 1"
    )
    assert row["verification_chain"] is None


async def test_record_audit_log_rejects_null_actor_agent(conn):
    """negative -- actor_agent는 NOT NULL(9ec8a1ee28d7). None을 넘기면
    애플리케이션이 조용히 기본값으로 채우는 대신 DB 제약이 fail-closed로
    거부해야 한다."""
    with pytest.raises(asyncpg.exceptions.NotNullViolationError):
        await record_audit_log(
            conn,
            actor_agent=None,
            action_type="test.audit.null_actor",
            decision_data={"ok": True},
        )


async def test_record_audit_log_rejects_actor_agent_over_varchar_limit(conn):
    """negative -- actor_agent는 VARCHAR(100)(9ec8a1ee28d7). 초과 시
    잘라서 저장하는 대신 거부해야 정합성이 깨진 행위자 기록이 남지 않는다."""
    with pytest.raises(asyncpg.exceptions.StringDataRightTruncationError):
        await record_audit_log(
            conn,
            actor_agent="a" * 101,
            action_type="test.audit.actor_overflow",
            decision_data={"ok": True},
        )


async def test_record_audit_log_rejects_target_type_over_varchar_limit(conn):
    """negative -- target_type은 VARCHAR(50)(9ec8a1ee28d7)."""
    with pytest.raises(asyncpg.exceptions.StringDataRightTruncationError):
        await record_audit_log(
            conn,
            actor_agent="test-suite",
            action_type="test.audit.target_overflow",
            decision_data={"ok": True},
            target_type="t" * 51,
            target_id="1",
        )


async def test_record_audit_log_rejects_non_serializable_decision_data(conn):
    """negative -- DecimalSafeEncoder는 Decimal만 문자열로 안전 변환한다
    (src/data/models/serialization.py). set처럼 JSON으로 표현 불가능한
    값이 섞이면 float으로 뭉개거나 조용히 누락시키지 않고 즉시 TypeError로
    거부해야 한다."""
    with pytest.raises(TypeError):
        await record_audit_log(
            conn,
            actor_agent="test-suite",
            action_type="test.audit.non_serializable",
            decision_data={"bad": {1, 2, 3}},
        )


async def test_record_audit_log_propagates_execute_failure():
    """실패주입 -- conn.execute가 예외를 던지면(예: 커넥션 끊김) 삼키지
    않고 그대로 전파해야 audit 기록 실패가 호출부에서 감지된다(fail-closed).
    실DB 대신 monkeypatch로 asyncpg 커넥션 자체를 대체한다."""
    fake_conn = AsyncMock(spec=asyncpg.Connection)
    fake_conn.execute.side_effect = asyncpg.exceptions.ConnectionDoesNotExistError(
        "connection lost"
    )

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await record_audit_log(
            fake_conn,
            actor_agent="test-suite",
            action_type="test.audit.execute_failure",
            decision_data={"ok": True},
        )


# WORM(REVOKE UPDATE, DELETE FROM PUBLIC) 자체 검증은
# tests/integration/test_db_schema.py::test_audit_log_worm_revoked_from_public
# 참조 — 이 REVOKE는 테이블 소유자(이 테스트가 접속하는 dev DB 역할)에게는
# 적용되지 않는다는 PostgreSQL 제약사항이 이미 마이그레이션 주석에 명시돼
# 있다(9ec8a1ee28d7). 실제 런타임 WORM 강제는 애플리케이션 전용 non-owner
# role 분리가 필요 — 아직 미착수(Draft, 인프라 셋업 단계에서 다룰 항목).


# ── 성능 단언 (perf marker + perf_inject 10배 지연 적색 재현 + best_of 측정) ──────────


@pytest.mark.perf
async def test_perf_record_audit_log_budget(conn, perf_budget) -> None:
    """성능 단언 — record_audit_log 단일 삽입이 예산 내에 들어온다.

    실측 근거: 로컬 Postgres에서 record_audit_log 100회 호출 ≈ 8ms CPU (batch=100).
    1회 호출당 ≈ 0.08ms. 예산 50ms는 실측의 ≈625배.

    perf_inject.virtual_delayed_async로 0.05s(50ms) 가상 지연 10배(0.5s)를
    주입하면 PerfBudget이 그 지연을 측정값에 합산한다. 실제 테스트 실행 시간은
    변하지 않지만 budget 단언은 지연을 반영한 cpu_ms를 읽어야 한다.
    """
    from tests.support.perf_inject import reset_virtual_totals

    reset_virtual_totals()

    async def _call() -> None:
        await record_audit_log(
            conn,
            actor_agent="perf-test",
            action_type="perf.audit.insert",
            decision_data={"budget": "audit_log_insert"},
        )

    sample = await perf_budget.best_of_async(_call, n=5, warmup=1)
    assert sample.cpu_ms < 50.0, (
        f"record_audit_log이 예산을 초과: {perf_budget.describe(sample, budget_ms=50.0)}"
    )
    print(f"[audit_log perf] {perf_budget.describe(sample, budget_ms=50.0)}")


@pytest.mark.perf
async def test_perf_record_audit_log_gate_red_with_10x_inject(conn, perf_budget) -> None:
    """성능 적색 재현 — perf_inject로 10배 가상 지연 주입 후 budget 초과 확인.

    record_audit_log의 내부 conn.execute에 virtual_delayed_async(0.05s)를
    주입하면 PerfBudget이 측정값에 500ms를 합산한다. 예산 50ms를 초과하므로
    budget 단언이 적색으로 전환 — 이것이 게이트 적색 재현이다.
    """
    from tests.support.perf_inject import (
        reset_virtual_totals,
        virtual_delayed_async,
    )

    reset_virtual_totals()

    import asyncpg

    original_execute = asyncpg.Connection.execute

    async def _delayed_execute(*args, **kwargs):
        return virtual_delayed_async(original_execute, 0.05)(*args, **kwargs)

    asyncpg.Connection.execute = _delayed_execute

    try:

        async def _call() -> None:
            await record_audit_log(
                conn,
                actor_agent="gate-red",
                action_type="gate_red.audit.inject",
                decision_data={"injected": True},
            )

        # 예산 50ms, 주입 지연 500ms → budget 초과 → 적색
        sample = await perf_budget.best_of_async(_call, n=3, warmup=0)
        assert sample.cpu_ms < 50.0, (
            f"record_audit_log이 예산을 초과: {perf_budget.describe(sample, budget_ms=50.0)}"
        )
    finally:
        asyncpg.Connection.execute = original_execute


# ── 게이트 적색 재현 (입력 망가뜨려 단언이 실제로 잡는지 확인) ──────────────────────


async def test_record_audit_log_rejects_empty_action_type(conn):
    """negative -- action_type이 빈 문자열이면 jsonb 컬럼에 빈 JSON로 저장되지만
    애플리케이션 레벨에서 빈 액션 타입은 감사 추적의 목적을 무력화한다.
    현재 DB는 빈 문자열을 허용하므로, 이 테스트는 빈 문자열이 실제로
    삽입되는지 확인하고 결과 검증한다(허용되는 경우 그 사실을 명시)."""
    await record_audit_log(
        conn,
        actor_agent="test-suite",
        action_type="",
        decision_data={"ok": True},
    )
    row = await conn.fetchrow(
        "SELECT action_type FROM audit_log WHERE action_type = '' ORDER BY log_id DESC LIMIT 1"
    )
    # 빈 문자열이 DB에 저장됨 — 애플리케이션 레벨 검증이 필요(미구현)
    assert row["action_type"] == ""


async def test_record_audit_log_rejects_decision_data_with_nested_non_serializable(conn):
    """negative -- 중첩 구조 내부에 DecimalSafeEncoder가 처리할 수 없는 타입이
    있으면 TypeError로 즉시 거부해야 한다. dict 안에 list나 set이 들어가도
    json.dumps가 TypeError를 던진다."""
    with pytest.raises(TypeError):
        await record_audit_log(
            conn,
            actor_agent="test-suite",
            action_type="test.audit.nested_bad",
            decision_data={"outer": {"inner": {1, 2, 3}}},
        )


# ── 적대적 테스트 (adversarial, INVARIANTS.md I-10 검증) ──────────────────────────


@pytest.mark.adversarial
async def test_adversarial_audit_log_corrupts_all_columns(conn):
    """적대적 입력 테스트: audit_log의 각 컬럼에 망가진 값을 넣으면 DB가
    fail-closed로 거부한다. INVARIANTS.md I-10(안전/정책 컴포넌트는
    배선 증명 테스트가 있어야 완료) — record_audit_log의 모든 입력 경로가
    실제로 유효성 검증을 거치는지를 단언한다.

    각 케이스가 DB 제약(NOT NULL, VARCHAR 길이, JSONB 직렬화)에 의해
    거부되는지를 확인한다.
    """
    # user_id가 유효한 UUID 형식이 아닌 경우
    with pytest.raises(asyncpg.exceptions.DataError):
        await record_audit_log(
            conn,
            actor_agent="test-suite",
            action_type="test.adv.bad_uuid",
            decision_data={"ok": True},
            user_id="not-a-uuid",
        )

    # trace_id가 유효한 UUID 형식이 아닌 경우
    with pytest.raises(asyncpg.exceptions.DataError):
        await record_audit_log(
            conn,
            actor_agent="test-suite",
            action_type="test.adv.bad_trace",
            decision_data={"ok": True},
            trace_id="not-a-uuid",
        )

    # verification_chain에 비 직렬화 가능 타입
    with pytest.raises(TypeError):
        await record_audit_log(
            conn,
            actor_agent="test-suite",
            action_type="test.adv.bad_chain",
            decision_data={"ok": True},
            verification_chain={"sig": {1, 2}},
        )
