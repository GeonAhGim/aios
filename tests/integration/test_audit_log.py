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
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_async


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


@pytest.mark.perf
async def test_record_audit_log_inserts_row_with_decimal_safe_json(conn):
    """perf — audit_log insert wall-clock 측정.

    실측 (2026-11-15, local Postgres, best_of 30): min wall_ms ≈ 0.077ms.
    예산 = 실측의 30배 이상 → 3.0ms. DB 왕복은 wall-bound이므로 wall_ms 기준.
    """
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

    # --- perf measurement (after functional assertions) ---
    reset_virtual_totals()

    async def _insert():
        await record_audit_log(
            conn,
            actor_agent="perf-bench",
            action_type="test.audit.perf_bench",
            decision_data={"amount": Decimal("0.01")},
        )

    sample = await PerfBudget().best_of_async(_insert, n=10, warmup=2)
    budget_ms = 3.0  # 30× real min (0.077ms), wall-bound DB round-trip
    assert sample.wall_ms < budget_ms, (
        f"audit_log insert wall={sample.wall_ms:.3f}ms exceeds budget {budget_ms:.3f}ms"
    )


@pytest.mark.perf
async def test_record_audit_log_perf_budget_detects_10x_injection(conn, monkeypatch):
    """gate-red — `virtual_delayed_async`로 10× budget_ms 주입하면
    `PerfBudget().best_of_async`가 AssertionError를 잡는다.

    주입량 = 10 × budget_ms (초 단위). budget_ms=3.0 → 주입 30초 가상 지연.
    ledger가 wall 측정에 반영되므로 예산을 크게 초과한다.
    """
    reset_virtual_totals()

    budget_ms = 3.0
    budget_s = budget_ms / 1000.0
    injection_s = 10 * budget_s  # 30.0 seconds virtual

    # Patch class-level execute so the wrapper receives (self, *args) correctly
    original_execute = asyncpg.Connection.execute
    wrapped_execute = virtual_delayed_async(original_execute, injection_s)
    monkeypatch.setattr(asyncpg.Connection, "execute", wrapped_execute)

    sample = await PerfBudget().best_of_async(
        lambda: record_audit_log(
            conn,
            actor_agent="gate-red",
            action_type="test.audit.gate_red",
            decision_data={"amount": Decimal("1.0")},
        ),
        n=3,
        warmup=0,
    )
    assert sample.wall_ms >= budget_ms, (
        f"Expected wall={sample.wall_ms:.3f}ms >= budget {budget_ms:.3f}ms "
        f"(injection={injection_s:.1f}s virtual)"
    )


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
