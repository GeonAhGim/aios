"""L4-08 DoD — `order_command_outbox` 실DB 어댑터: SKIP LOCKED 3워커 경합,
리스 만료 후 재클레임, 펜싱 거부.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-08, §5.1 outbox
claim/done/retry/dead 행.

지연 단언은 쓰지 않는다(headless worker 지침) — `asyncio.gather`로 3워커를
동시에 실행한 뒤 각 행이 정확히 한 워커에게만 갔는지 구조로 확인한다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.core.db.conditional_write import ConcurrencyConflictError
from src.services.oms.adapters.outbox_repository import OutboxRepository
from src.services.oms.ports.repository import OutboxRepoPort
from tests.integration.oms.conftest import create_test_user, insert_order


@pytest.fixture
async def pool(pool):
    """conftest의 `pool`을 오버라이드해 이 파일의 테스트 앞에 정리를 끼워 넣는다.

    이 파일의 모든 테스트는 `claim_batch(limit=...)`가 자신이 방금 넣은 행만
    본다고 가정한다(예: `claimed = [r for r in rows if r.id == row_id]`,
    `assert state == "SENDING"`). 그런데 `order_command_outbox`는 이 스위트
    전체가 공유하는 테이블이고, dispatcher가 꺼져 있어(tests/conftest.py의
    `AIOS_OMS_DISPATCHER_ENABLED=0`) 앞서 실행된 파일(cancel_order/modify_order
    등)이 남긴 PENDING 행이 정리되지 않은 채 쌓인다. 전체 스위트를 CI와 같은
    순서로 돌리면 그 leftover 행이 `ORDER BY created_at LIMIT 10`을 먼저
    채워 이 파일이 방금 넣은 행이 클레임되지 못하고 PENDING으로 남는다
    (`test_stale_worker_mark_done_rejected`가 이 형태로 실패하는 걸 재현
    확인함). 다른 테이블에는 `order_command_outbox`를 참조하는 FK가 없으므로
    이 파일의 각 테스트 시작 전에 비워도 안전하다.
    """
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM order_command_outbox")
    return pool


def test_outbox_repository_satisfies_port():
    """I-10 배선 증명 — 정적 타입뿐 아니라 런타임에도 포트를 만족한다."""
    assert isinstance(OutboxRepository(), OutboxRepoPort)


async def _enqueue(pool, repo, order_id, *, not_before=None) -> object:
    async with pool.acquire() as conn:
        return await repo.enqueue(
            conn,
            order_id=order_id,
            command_type="SUBMIT",
            payload={"order": {"symbol": "BTC/USDT"}},
            not_before=not_before or datetime.now(timezone.utc),
        )


async def _order_id(pool) -> object:
    async with pool.acquire() as conn:
        user_id = await create_test_user(pool)
        return await insert_order(conn, user_id, quantity=Decimal("1"))


async def test_enqueue_then_claim_batch_marks_sending(pool):
    repo = OutboxRepository()
    order_id = await _order_id(pool)
    row_id = await _enqueue(pool, repo, order_id)

    async with pool.acquire() as conn:
        rows = await repo.claim_batch(conn, worker_id="w1", limit=10, lease_sec=30)

    claimed = [r for r in rows if r.id == row_id]
    assert len(claimed) == 1
    row = claimed[0]
    assert row.state == "SENDING"
    assert row.worker_id == "w1"
    assert row.lease_until is not None and row.lease_until > datetime.now(timezone.utc)
    assert row.payload == {"order": {"symbol": "BTC/USDT"}}


async def test_claim_batch_skips_rows_not_yet_due(pool):
    repo = OutboxRepository()
    order_id = await _order_id(pool)
    future = datetime.now(timezone.utc) + timedelta(hours=1)
    row_id = await _enqueue(pool, repo, order_id, not_before=future)

    async with pool.acquire() as conn:
        rows = await repo.claim_batch(conn, worker_id="w1", limit=10, lease_sec=30)

    assert row_id not in [r.id for r in rows]


async def test_three_workers_claim_each_row_exactly_once(pool):
    """DoD(2) — 실DB 3워커 동시 claim, 행당 정확히 1회."""
    repo = OutboxRepository()
    order_id = await _order_id(pool)
    row_ids = {await _enqueue(pool, repo, order_id) for _ in range(9)}

    async def claim_as(worker_id: str):
        async with pool.acquire() as conn:
            return await repo.claim_batch(conn, worker_id=worker_id, limit=3, lease_sec=30)

    results = await asyncio.gather(claim_as("A"), claim_as("B"), claim_as("C"))

    claimed_ids = [r.id for rows in results for r in rows]
    assert set(claimed_ids) == row_ids  # 전부 정확히 한 번씩 소진
    assert len(claimed_ids) == len(set(claimed_ids))  # 중복 없음(행당 정확히 1회)
    for rows in results:
        assert len(rows) <= 3  # limit 준수 — 정확한 분배 비율은 스케줄링에 의존


async def test_lease_expired_row_reclaimed_after_recovery_resets_pending(pool):
    """DoD(2) — 리스 만료 후 재클레임. 만료 자체를 감지하는 건 별도 복구
    잡(F2, 이 리프 밖)이라 그 결과(state=PENDING로 되돌림)만 흉내 낸다."""
    repo = OutboxRepository()
    order_id = await _order_id(pool)
    row_id = await _enqueue(pool, repo, order_id)

    async with pool.acquire() as conn:
        first = await repo.claim_batch(conn, worker_id="A", limit=10, lease_sec=30)
    assert [r.id for r in first] == [row_id]

    async with pool.acquire() as conn:  # F2 복구가 만료를 보고 되돌리는 것을 흉내
        await conn.execute(
            "UPDATE order_command_outbox SET state='PENDING', worker_id=NULL, "
            "lease_until=NULL WHERE id=$1",
            row_id,
        )

    async with pool.acquire() as conn:
        second = await repo.claim_batch(conn, worker_id="B", limit=10, lease_sec=30)
    assert [r.id for r in second] == [row_id]
    assert second[0].worker_id == "B"


async def test_stale_worker_mark_done_rejected(pool):
    """DoD(2) negative — 리스 잃은 워커의 mark_done은 거부된다."""
    repo = OutboxRepository()
    order_id = await _order_id(pool)
    row_id = await _enqueue(pool, repo, order_id)

    async with pool.acquire() as conn:
        await repo.claim_batch(conn, worker_id="A", limit=10, lease_sec=30)
        # 다른 워커가 이미 이 행을 마무리했다고 가정(B로 강제 전환).
        await conn.execute(
            "UPDATE order_command_outbox SET worker_id='B' WHERE id=$1", row_id
        )

    async with pool.acquire() as conn:
        with pytest.raises(ConcurrencyConflictError):
            await repo.mark_done(conn, row_id, expected_worker="A")

    async with pool.acquire() as conn:
        state = await conn.fetchval(
            "SELECT state FROM order_command_outbox WHERE id=$1", row_id
        )
    assert state == "SENDING"  # A의 늦은 쓰기는 반영되지 않았다


async def test_mark_done_succeeds_for_matching_worker(pool):
    repo = OutboxRepository()
    order_id = await _order_id(pool)
    row_id = await _enqueue(pool, repo, order_id)

    async with pool.acquire() as conn:
        await repo.claim_batch(conn, worker_id="A", limit=10, lease_sec=30)
        await repo.mark_done(conn, row_id, expected_worker="A")
        state = await conn.fetchval(
            "SELECT state FROM order_command_outbox WHERE id=$1", row_id
        )
    assert state == "DONE"


async def test_mark_retry_returns_row_to_pending(pool):
    repo = OutboxRepository()
    order_id = await _order_id(pool)
    row_id = await _enqueue(pool, repo, order_id)
    not_before = datetime.now(timezone.utc) + timedelta(seconds=5)

    async with pool.acquire() as conn:
        await repo.claim_batch(conn, worker_id="A", limit=10, lease_sec=30)
        await repo.mark_retry(
            conn, row_id, attempt=1, not_before=not_before, last_error="TIMEOUT",
            expected_worker="A",
        )
        row = await conn.fetchrow(
            "SELECT state, attempt, last_error, worker_id, lease_until "
            "FROM order_command_outbox WHERE id=$1",
            row_id,
        )
    assert row["state"] == "PENDING"
    assert row["attempt"] == 1
    assert row["last_error"] == "TIMEOUT"
    assert row["worker_id"] is None
    assert row["lease_until"] is None


async def test_mark_retry_rejected_for_stale_worker(pool):
    repo = OutboxRepository()
    order_id = await _order_id(pool)
    row_id = await _enqueue(pool, repo, order_id)

    async with pool.acquire() as conn:
        await repo.claim_batch(conn, worker_id="A", limit=10, lease_sec=30)
        with pytest.raises(ConcurrencyConflictError):
            await repo.mark_retry(
                conn, row_id, attempt=1, not_before=datetime.now(timezone.utc),
                last_error="x", expected_worker="B",
            )


async def test_mark_dead_succeeds_then_rejects_second_call(pool):
    repo = OutboxRepository()
    order_id = await _order_id(pool)
    row_id = await _enqueue(pool, repo, order_id)

    async with pool.acquire() as conn:
        await repo.claim_batch(conn, worker_id="A", limit=10, lease_sec=30)
        await repo.mark_dead(conn, row_id, reason="MAX_ATTEMPTS", expected_worker="A")
        state = await conn.fetchval(
            "SELECT state FROM order_command_outbox WHERE id=$1", row_id
        )
    assert state == "DEAD"

    async with pool.acquire() as conn:
        with pytest.raises(ConcurrencyConflictError):
            await repo.mark_dead(conn, row_id, reason="AGAIN", expected_worker="A")
