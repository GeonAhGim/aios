"""LC-17 적대적 — 잘못된 입력 3종은 전부 거부: amount≤0, 음수 가격
리스팅, `extra`의 secret류 키(`api_key`).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.3 LC-17.

세 케이스 중 앞의 둘은 기존 방어가 실제로 작동하는지 확인한다
(`LedgerEvent.amount`의 `Field(gt=0)`, `listing_service._validate_price`).
세 번째(`extra`의 `api_key` 키)는 task-614(LC-17)가 실증한 결함 A였다 —
`LedgerEvent.extra: dict[str, Decimal | str]`에는 키 이름 검증이 없었고,
`evidence.domain.rules.assert_safe_payload`(secret/token/password/api_key류
키를 거부하는 도구)는 `post_entry.py`가 직접 조립한 고정 payload dict에만
적용되고 `event.extra`에는 적용되지 않았다. task-626에서
`post_entry._assert_extra_safe`가 `assert_safe_payload`를 `event.extra`에
재사용하고, `contracts.v1.EXTRA_ALLOWED_KEYS`(사건 타입별로
`posting_rules.py` 핸들러가 실제로 읽는 키만 담은 화이트리스트)로
비화이트리스트 키도 거부하도록 고쳤다 — 아래 테스트는 이제 통과한다.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    PLATFORM_COMMISSION_REVENUE,
)
from src.services.listing_service import ListingError, ListingService
from tests.integration.conftest import create_test_user


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _always_eligible(strategy_id: str, version: str) -> bool:
    return True


@pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-1"), Decimal("-100.50")])
def test_ledger_event_rejects_amount_not_positive(amount: Decimal) -> None:
    with pytest.raises(ValidationError):
        LedgerEvent(
            event_type=LedgerEventType.TOPUP_CONFIRMED,
            event_ref=f"topup:{uuid4()}",
            tenant_id=None,
            actor_subject_id=None,
            trace_id=uuid4(),
            amount=amount,
            currency=Currency.KRW,
            parties={"user": uuid4()},
            extra={},
        )


async def test_listing_service_rejects_negative_price(pool) -> None:
    seller = await create_test_user(pool)
    strategy_id = f"test-negprice-{uuid4().hex[:8]}"
    version = "1.0.0"
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO strategies "
            "(strategy_id, version, owner_user_id, target_asset, market, exchange, "
            " fsm_definition, author_agent) "
            "VALUES ($1, $2, $3, 'BTC/USDT', 'crypto', 'bitget', '{}'::jsonb, 'test-author')",
            strategy_id,
            version,
            seller,
        )
    service = ListingService(pool, verify_paper_trading_eligibility=_always_eligible)

    with pytest.raises(ListingError):
        await service.create_listing(seller, strategy_id, version, Decimal("-0.01"))

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM strategy_listings WHERE strategy_id = $1", strategy_id
        )
    assert count == 0  # 거부됐다면 리스팅 행 자체가 생기지 않아야 한다.


async def test_extra_with_api_key_shaped_key_is_rejected_by_post_entry(pool) -> None:
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)

    event = LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=f"manual:{uuid4()}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=Decimal("1.00"),
        currency=Currency.KRW,
        parties={},
        extra={
            "api_key": "sk-should-never-reach-storage",
            "debit_account": PLATFORM_CASH_CLEARING,
            "credit_account": PLATFORM_COMMISSION_REVENUE,
        },
    )

    with pytest.raises(Exception):  # noqa: B017 — "거부됨"만 요구, 특정 예외형은 스펙에 없다.
        async with pool.acquire() as conn, conn.transaction():
            await post_entry(
                conn, event, journal=journal, balances=balances, audit=audit, clock=_clock
            )
