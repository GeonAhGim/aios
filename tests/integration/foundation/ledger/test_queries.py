"""LC-16 `application/queries.py` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §4.4, §9 LC-16.
DoD: "프론트 무변경으로 기존 지갑 테스트 전부 통과" — `get_balance`가
`balance`(레거시)를 그대로 두고 `available`/`held`/`pending_payout`을
정확히 보고하는지, 그리고 레거시·원장 잔액이 어긋나면 500이 아니라
명시적 `WalletLedgerDriftError`로 표면화하는지(negative case)를 검증한다.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.legacy_wallet_bridge import bridge_credit
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_hold_repository import PostgresHoldRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.purchase_flow import capture_hold, place_hold
from src.foundation.ledger.application.queries import WalletLedgerDriftError, get_balance
from tests.integration.conftest import create_test_user

_TEST_PURPOSE = "TEST_QUERIES_PURCHASE"


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class _RealPorts:
    def __init__(self, pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)
        self.holds = PostgresHoldRepository(pool)
        self.clock = _clock


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


async def test_get_balance_new_user_is_all_zero(pool, ports):
    user = await create_test_user(pool)

    result = await get_balance(pool, user, balances=ports.balances)

    assert result.user_id == user
    assert result.balance == Decimal("0")
    assert result.available == Decimal("0")
    assert result.held == Decimal("0")
    assert result.pending_payout == Decimal("0")


async def test_get_balance_reports_available_and_held_after_hold_placed(pool, ports):
    buyer = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, buyer, Decimal("100.00"), "TOPUP")

    reference = f"test-queries:{uuid4()}"
    async with pool.acquire() as conn, conn.transaction():
        await place_hold(
            conn, buyer_id=buyer, amount=Decimal("30.00"), purpose=_TEST_PURPOSE,
            reference=reference, expires_at=_clock() + timedelta(minutes=15),
            actor_subject_id=buyer, trace_id=uuid4(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
            holds=ports.holds,
        )
        # 실제 호출자(`src/services/purchase_service.py::_project`)라면 여기서
        # 레거시 투영도 함께 갱신한다 — `purchase_flow.place_hold` 자체는
        # `user_wallets`를 건드리지 않는다(모듈 docstring 참고). 이 테스트는
        # 그 계약을 그대로 재현해 드리프트 없는 상태를 유지한다.
        await conn.execute(
            "UPDATE user_wallets SET balance = balance - $2 WHERE user_id = $1",
            buyer, Decimal("30.00"),
        )

    result = await get_balance(pool, buyer, balances=ports.balances)
    assert result.balance == Decimal("70.00")
    assert result.available == Decimal("70.00")
    assert result.held == Decimal("30.00")
    assert result.pending_payout == Decimal("0")


async def test_get_balance_reports_pending_payout_after_capture(pool, ports):
    buyer = await create_test_user(pool)
    seller = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, buyer, Decimal("100.00"), "TOPUP")

    reference = f"test-queries:{uuid4()}"
    async with pool.acquire() as conn, conn.transaction():
        hold = await place_hold(
            conn, buyer_id=buyer, amount=Decimal("100.00"), purpose=_TEST_PURPOSE,
            reference=reference, expires_at=_clock() + timedelta(minutes=15),
            actor_subject_id=buyer, trace_id=uuid4(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
            holds=ports.holds,
        )
        await conn.execute(
            "UPDATE user_wallets SET balance = balance - $2 WHERE user_id = $1",
            buyer, Decimal("100.00"),
        )
        capture = await capture_hold(
            conn, hold, seller_id=seller, commission_rate=Decimal("0.15"),
            actor_subject_id=buyer, trace_id=uuid4(), now=_clock(),
            journal=ports.journal, balances=ports.balances, audit=ports.audit, clock=ports.clock,
            holds=ports.holds,
        )

    buyer_result = await get_balance(pool, buyer, balances=ports.balances)
    assert buyer_result.balance == Decimal("0.00")
    assert buyer_result.available == Decimal("0.00")
    assert buyer_result.held == Decimal("0")  # capture가 buyer HELD를 전부 비웠다

    seller_result = await get_balance(pool, seller, balances=ports.balances)
    assert seller_result.balance == Decimal("0")  # 레거시 투영은 아직 아무도 안 건드림
    assert seller_result.available == Decimal("0")  # seller AVAILABLE 계정 자체가 아직 없음
    assert seller_result.pending_payout == capture.payout_amount == Decimal("85.00")


async def test_get_balance_raises_explicit_drift_error_instead_of_generic_failure(pool, ports):
    """DoD negative test: 원장과 레거시 투영이 어긋나면 500이 아니라
    `WalletLedgerDriftError`로 명시적으로 실패해야 한다."""
    user = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, user, Decimal("50.00"), "TOPUP")

    # 원장 밖에서(운영자 수기 수정 등) 레거시 투영만 어긋나게 만든다 — 이
    # 브리지를 거치지 않은 직접 SQL이라 원장은 그대로 50.00으로 남는다.
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE user_wallets SET balance = balance + $2 WHERE user_id = $1",
            user, Decimal("999.00"),
        )

    with pytest.raises(WalletLedgerDriftError) as exc_info:
        await get_balance(pool, user, balances=ports.balances)

    assert exc_info.value.user_id == user
    assert exc_info.value.legacy_balance == Decimal("1049.00")
    assert exc_info.value.ledger_available == Decimal("50.00")


async def test_get_balance_no_false_positive_drift_under_concurrent_commits(pool, ports):
    """task-951 결함 수정 검증: 조회 도중 다른 트랜잭션이 bridge_credit/
    place_hold를 커밋해도 위양성 `WalletLedgerDriftError`가 나면 안 된다.
    수정 전(4개의 개별 SELECT)에서는 legacy/ledger 읽기 사이에 커밋이
    끼어들면 서로 다른 시점의 값을 비교해 위양성 409를 던질 수 있었다 —
    이 테스트는 다량의 동시 커밋과 동시 조회를 경합시켜, `get_balance` 중
    하나라도 `WalletLedgerDriftError`를 던지면 `asyncio.gather`가 그대로
    전파해 테스트를 실패시킨다."""
    buyer = await create_test_user(pool)
    async with pool.acquire() as conn, conn.transaction():
        await bridge_credit(conn, buyer, Decimal("1000.00"), "TOPUP")

    async def credit_writer() -> None:
        async with pool.acquire() as conn, conn.transaction():
            await bridge_credit(conn, buyer, Decimal("1.00"), "TOPUP")

    async def hold_writer() -> None:
        reference = f"test-queries-concurrent:{uuid4()}"
        async with pool.acquire() as conn, conn.transaction():
            await place_hold(
                conn, buyer_id=buyer, amount=Decimal("1.00"), purpose=_TEST_PURPOSE,
                reference=reference, expires_at=_clock() + timedelta(minutes=15),
                actor_subject_id=buyer, trace_id=uuid4(),
                journal=ports.journal, balances=ports.balances, audit=ports.audit,
                clock=ports.clock, holds=ports.holds,
            )
            await conn.execute(
                "UPDATE user_wallets SET balance = balance - $2 WHERE user_id = $1",
                buyer, Decimal("1.00"),
            )

    async def reader() -> None:
        await get_balance(pool, buyer, balances=ports.balances)

    writers = [credit_writer() for _ in range(15)] + [hold_writer() for _ in range(15)]
    readers = [reader() for _ in range(60)]
    await asyncio.gather(*writers, *readers)

    final = await get_balance(pool, buyer, balances=ports.balances)
    assert final.balance == final.available
    assert final.balance == Decimal("1000.00") + Decimal("15.00") - Decimal("15.00")
    assert final.held == Decimal("15.00")
