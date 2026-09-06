"""P0-F(I-03, task-1719) DoD — `LedgerIntegrityScheduler.run_once`가
`core/idempotency.py::purge_expired`를 실제로 호출하는지 확인한다.

Spec: docs/FULL_AUDIT_2026-09-06.md P0-F(I-03) — `idempotency_keys`가
운영 호출 없이 무한 증가하던 결함의 회귀 방지. `run_once`는 이미 LC-10
무결성 검증(5분 주기)을 태우는 지점이라, 만료 정리도 같은 주기에
얹었다(scheduler.py) — 정리가 무결성 검증 실행 자체를 막지 않는지도
함께 확인한다.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from src.core.observability.metrics_registry import MetricsRegistry
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.adapters.postgres_payout_repository import PostgresPayoutRepository
from src.foundation.ledger.application.scheduler import LedgerIntegrityScheduler


@pytest.fixture
def scheduler(pool):
    return LedgerIntegrityScheduler(
        pool,
        journal=PostgresJournalRepository(pool),
        balances=PostgresBalanceRepository(pool),
        audit=PostgresAuditEventRepository(pool),
        registry=MetricsRegistry(),
        payouts=PostgresPayoutRepository(pool),
    )


async def test_run_once_purges_expired_idempotency_keys(pool, scheduler):
    live_key = f"sched-live-{uuid.uuid4().hex}"
    expired_key = f"sched-expired-{uuid.uuid4().hex}"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO idempotency_keys (key, status_code, response_body, expires_at) "
            "VALUES ($1, 201, '{}'::jsonb, now() + interval '1 hour')",
            live_key,
        )
        await conn.execute(
            "INSERT INTO idempotency_keys (key, status_code, response_body, expires_at) "
            "VALUES ($1, 201, '{}'::jsonb, $2)",
            expired_key,
            datetime(2000, 1, 1, tzinfo=timezone.utc),
        )

    report = await scheduler.run_once()

    assert report.chain_ok  # 정리 작업이 무결성 검증 자체를 막지 않았다
    async with pool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT 1 FROM idempotency_keys WHERE key = $1", expired_key
        ) is None
        assert await conn.fetchval(
            "SELECT 1 FROM idempotency_keys WHERE key = $1", live_key
        ) == 1
