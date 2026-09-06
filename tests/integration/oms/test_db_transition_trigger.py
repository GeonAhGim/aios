"""L4-06 DoD — `<rev1>` 마이그레이션의 실DB 트리거·왕복 검증.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-06
("alembic upgrade head 후 손 UPDATE 역전이 RAISE, downgrade 가능"),
§4.1 I2-I6, §8.2 `test_db_transition_trigger.py` 행("손 UPDATE FILLED→
SUBMITTED RAISE; order_events 없이 status UPDATE RAISE; filled 감소 RAISE;
version 자동 증가").

I2/I4/I6(전이표·터미널 불변·이벤트 동반)는 `oms_order_transition_cutover`가
무장된 뒤에만 강제된다(레거시 `order_service` 경로 보호, 073beca589d5
docstring) — 이 파일의 관련 테스트는 트랜잭션 안에서 무장한 뒤 항상
롤백해 공유 테스트 DB의 다른 테스트에 영향을 남기지 않는다
(tests/adversarial/risk/test_fence_race.py와 동일 관례).
"""
from __future__ import annotations

import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import asyncpg
import pytest

from src.core.db.append_only import InvalidIdentifierError, worm_sql
from tests.integration.oms.conftest import (
    arm_cutover_sql,
    create_test_user,
    insert_event,
    insert_order,
)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _run_alembic(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )


@pytest.fixture(autouse=True)
def _ensure_head():
    """라운드트립 테스트가 실패로 중단돼도 이후 테스트가 downgrade된
    스키마를 보지 않게 매 테스트 전후 head를 보장한다(정의 순서 무의존)."""
    _run_alembic("upgrade", "head")
    yield
    _run_alembic("upgrade", "head")


async def _table_exists(pool: asyncpg.Pool, table_name: str) -> bool:
    async with pool.acquire() as conn:
        reg = await conn.fetchval("SELECT to_regclass($1)", f"public.{table_name}")
    return reg is not None


async def test_downgrade_then_upgrade_round_trip(pool):
    for table in (
        "order_events",
        "order_command_outbox",
        "provider_event_inbox",
        "fills",
        "order_idempotency",
        "oms_order_transition_cutover",
    ):
        assert await _table_exists(pool, table)

    # "-1"이 아니라 073beca589d5의 down_revision을 명시한다 — 이 리프 위에
    # 다른 마이그레이션(d0a580db5ce8 등)이 쌓이면 "-1"은 그 최신 마이그레이션만
    # 되돌려 이 테스트의 전제(073beca589d5가 되돌려짐)가 깨진다.
    _run_alembic("downgrade", "e1d9b5ed8d7d")
    for table in (
        "order_events",
        "order_command_outbox",
        "provider_event_inbox",
        "fills",
        "order_idempotency",
        "oms_order_transition_cutover",
    ):
        assert not await _table_exists(pool, table)

    async with pool.acquire() as conn:
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'orders'"
        )
    assert "version" not in {c["column_name"] for c in cols}

    _run_alembic("upgrade", "head")
    for table in (
        "order_events",
        "order_command_outbox",
        "provider_event_inbox",
        "fills",
        "order_idempotency",
        "oms_order_transition_cutover",
    ):
        assert await _table_exists(pool, table)
    async with pool.acquire() as conn:
        cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'orders'"
        )
    assert "version" in {c["column_name"] for c in cols}


async def test_version_auto_increments_on_every_update(pool):
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        order_id = await insert_order(conn, user_id)
        v0 = await conn.fetchval("SELECT version FROM orders WHERE order_id = $1", order_id)
        assert v0 == 0

        # 상태 불변 UPDATE(예: MODIFIED류)도 I5는 항상 적용된다.
        await conn.execute("UPDATE orders SET price = 100 WHERE order_id = $1", order_id)
        v1 = await conn.fetchval("SELECT version FROM orders WHERE order_id = $1", order_id)
        assert v1 == 1

        await conn.execute("UPDATE orders SET price = 101 WHERE order_id = $1", order_id)
        v2 = await conn.fetchval("SELECT version FROM orders WHERE order_id = $1", order_id)
        assert v2 == 2


async def test_filled_quantity_decrease_raises(pool):
    """I3 — cutover 무관, 레거시 경로도 항상 강제된다."""
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        order_id = await insert_order(
            conn,
            user_id,
            status="PARTIALLY_FILLED",
            quantity=Decimal("1"),
            filled_quantity=Decimal("0.5"),
        )
        with pytest.raises(asyncpg.CheckViolationError, match="filled_quantity cannot decrease"):
            await conn.execute(
                "UPDATE orders SET filled_quantity = 0.3 WHERE order_id = $1", order_id
            )


async def test_filled_quantity_over_quantity_raises(pool):
    """§4.2 I3 상한(CHECK) — cutover 무관."""
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        order_id = await insert_order(
            conn, user_id, quantity=Decimal("1"), filled_quantity=Decimal("0")
        )
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute(
                "UPDATE orders SET filled_quantity = 2 WHERE order_id = $1", order_id
            )


async def test_terminal_reverse_transition_raises_once_cutover_armed(pool):
    """DoD — 손 UPDATE FILLED→SUBMITTED RAISE."""
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(arm_cutover_sql)
            order_id = await insert_order(
                conn, user_id, status="FILLED", quantity=Decimal("1"), filled_quantity=Decimal("1")
            )
            with pytest.raises(asyncpg.CheckViolationError, match="is terminal"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE orders SET status = 'SUBMITTED' WHERE order_id = $1", order_id
                    )
        finally:
            await tr.rollback()


async def test_transition_outside_table_raises_once_cutover_armed(pool):
    """§4.2 표 밖 (from,to) — 예: CREATED -> ACKNOWLEDGED는 직행 불가."""
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(arm_cutover_sql)
            order_id = await insert_order(conn, user_id, status="CREATED")
            with pytest.raises(asyncpg.CheckViolationError, match="not a valid"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE orders SET status = 'ACKNOWLEDGED' WHERE order_id = $1", order_id
                    )
        finally:
            await tr.rollback()


async def test_status_change_without_order_event_raises_once_cutover_armed(pool):
    """DoD — order_events 없이 status UPDATE RAISE, 이벤트를 먼저 쓰면 통과,
    같은 tx의 다음 전이는 플래그를 다시 세워야 한다(소모형)."""
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        tr = conn.transaction()
        await tr.start()
        try:
            await conn.execute(arm_cutover_sql)
            order_id = await insert_order(conn, user_id, status="CREATED")

            with pytest.raises(asyncpg.CheckViolationError, match="without order_events"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE orders SET status = 'VALIDATED' WHERE order_id = $1", order_id
                    )

            # §5.1 순서대로: SET LOCAL -> order_events INSERT -> UPDATE.
            await conn.execute("SELECT set_config('oms.event_written', '1', true)")
            await insert_event(
                conn, order_id, from_status="CREATED", to_status="VALIDATED", event="VALIDATED"
            )
            await conn.execute(
                "UPDATE orders SET status = 'VALIDATED' WHERE order_id = $1", order_id
            )
            status = await conn.fetchval("SELECT status FROM orders WHERE order_id = $1", order_id)
            assert status == "VALIDATED"

            # 플래그는 소모됐다 — 다시 세우지 않은 두 번째 전이는 다시 거부된다.
            with pytest.raises(asyncpg.CheckViolationError, match="without order_events"):
                async with conn.transaction():
                    await conn.execute(
                        "UPDATE orders SET status = 'SUBMITTED' WHERE order_id = $1", order_id
                    )
        finally:
            await tr.rollback()


def test_worm_sql_rejects_malicious_table_name():
    """§7 인젝션 negative — 이 마이그레이션이 재사용하는 `worm_sql()`(order_events/
    fills)이 식별자 검증을 우회당하지 않는다."""
    with pytest.raises(InvalidIdentifierError):
        worm_sql("order_events; DROP TABLE orders;--")
