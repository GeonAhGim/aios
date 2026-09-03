"""LC-17 적대적 — 리플레이 공격: 같은 event_ref, 다른 금액 → 거부 + 감사 DENIED.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.3 LC-17,
`domain/idempotency.py` `IdempotencyDigestMismatchError`
("재시도 불가, 409 + 감사 DENIED로 매핑").

HTTP 409로의 실제 매핑은 라우터 계층(이 리프 범위 밖)의 책임이라 여기서는
`post_entry`가 실제로 무엇을 던지는지와 그 감사 흔적이 실제로 남는지만
확인한다. `post_entry.py` 모듈 docstring이 명시하듯 이 예외는 "호출자가
트랜잭션 경계 **안에서** 잡아 흡수하고 커밋해야 DENIED 감사가 남는다" —
`test_verify_integrity.py`의 동결 테스트와 같은 관행으로 `pytest.raises`를
`conn.transaction()` 블록 안에 둔다(바깥에 두면 예외가 트랜잭션을 롤백시켜
DENIED 감사 행도 함께 사라진다).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.idempotency import IdempotencyDigestMismatchError
from tests.integration.conftest import create_test_user


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class _Ports:
    def __init__(self, pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


@pytest.fixture
def ports(pool):
    return _Ports(pool)


async def _seed_user_available_account(pool, user_id: UUID) -> None:
    code = ua(user_id, UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, 'LIABILITY', 'KRW', FALSE) ON CONFLICT (account_code) DO NOTHING",
            code,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, allow_negative, last_entry_seq) "
            "SELECT account_id, FALSE, 0 FROM ledger_account WHERE account_code = $1 "
            "ON CONFLICT (account_id) DO NOTHING",
            code,
        )


async def test_same_event_ref_different_amount_is_denied_with_audit_trail(pool, ports) -> None:
    user_id = await create_test_user(pool)
    await _seed_user_available_account(pool, user_id)
    event_ref = f"topup:{uuid4()}"

    first_event = LedgerEvent(
        event_type=LedgerEventType.TOPUP_CONFIRMED,
        event_ref=event_ref,
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=Decimal("100.00"),
        currency=Currency.KRW,
        parties={"user": user_id},
        extra={},
    )
    async with pool.acquire() as conn, conn.transaction():
        first_entry = await post_entry(
            conn, first_event, journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=_clock,
        )

    replay_event = first_event.model_copy(update={"amount": Decimal("999999.00")})

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(IdempotencyDigestMismatchError):
            await post_entry(
                conn, replay_event, journal=ports.journal, balances=ports.balances,
                audit=ports.audit, clock=_clock,
            )

    async with pool.acquire() as conn:
        denied_count = await conn.fetchval(
            "SELECT COUNT(*) FROM foundation_audit_event "
            "WHERE aggregate_id = $1 AND aggregate_type = 'ledger_journal_entry' "
            "AND action = $2 AND outcome = 'DENIED'",
            first_entry.entry_id,
            LedgerEventType.TOPUP_CONFIRMED.value,
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            ua(user_id, UserSub.AVAILABLE),
        )
    assert denied_count == 1
    # 리플레이가 거부된 뒤에도 최초 분개만 반영됐다(이중 적립 없음).
    assert balance == Decimal("100.00")
