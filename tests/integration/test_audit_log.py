"""7.4 — audit_log 기록 유틸 통합 테스트.

로컬 dev Postgres(docker-compose.dev.yml)에 마이그레이션이 적용된 상태를
전제로 한다.
"""
import json
from decimal import Decimal
from pathlib import Path

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


# WORM(REVOKE UPDATE, DELETE FROM PUBLIC) 자체 검증은
# tests/integration/test_db_schema.py::test_audit_log_worm_revoked_from_public
# 참조 — 이 REVOKE는 테이블 소유자(이 테스트가 접속하는 dev DB 역할)에게는
# 적용되지 않는다는 PostgreSQL 제약사항이 이미 마이그레이션 주석에 명시돼
# 있다(9ec8a1ee28d7). 실제 런타임 WORM 강제는 애플리케이션 전용 non-owner
# role 분리가 필요 — 아직 미착수(Draft, 인프라 셋업 단계에서 다룰 항목).
