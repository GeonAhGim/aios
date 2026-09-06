"""LC-12(b) `application/topup.py::post_topup` 통합테스트 — 실 DB
(TEST_DATABASE_URL) 대상.

DoD(task-1761) — 같은 `topup_id`(=`event_ref` 근거, 모듈 docstring 참조)로
`post_topup`을 2회 호출해도 두 번째 호출은 `post_entry`의 REPLAY 경로를 타
`user_wallets.balance`를 다시 증가시키지 않아야 한다(§4.3 멱등 계약의 원장
레벨 방어선 — `wallet_service.confirm_topup`의 상위 상태 체크와는 별개).
"""
from __future__ import annotations

import random
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.topup import post_topup
from tests.integration.conftest import create_test_user


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _wallet_balance(pool, user_id: UUID) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT balance FROM user_wallets WHERE user_id = $1", user_id
        )
    return value if value is not None else Decimal("0")


async def test_post_topup_same_topup_id_twice_does_not_double_credit(pool):
    user = await create_test_user(pool)
    admin = await create_test_user(pool)
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    # event_ref(f"topup:{topup_id}")가 전역 멱등키의 근거라, 다른 테스트/실행과
    # 부딪히지 않도록 매 테스트마다 고유한 값을 쓴다(seed 테이블 FK 제약 없음).
    topup_id = random.randint(1, 2_000_000_000)
    amount = Decimal("100.00")

    async def _post() -> Decimal:
        async with pool.acquire() as conn, conn.transaction():
            return await post_topup(
                conn,
                topup_id,
                user,
                amount,
                admin,
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
            )

    first = await _post()
    second = await _post()

    assert first == Decimal("100.00")
    assert second == Decimal("100.00")  # REPLAY — 두 번째 호출이 잔액을 더 늘리지 않는다
    assert await _wallet_balance(pool, user) == Decimal("100.00")

    async with pool.acquire() as conn:
        tx_count = await conn.fetchval(
            "SELECT COUNT(*) FROM wallet_transactions WHERE user_id = $1", user
        )
    assert tx_count == 1
