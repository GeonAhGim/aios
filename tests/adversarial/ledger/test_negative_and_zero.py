"""LC-17 적대적 — 잘못된 입력 3종은 전부 거부: amount≤0, 음수 가격
리스팅, `extra`의 secret류 키(`api_key`).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.3 LC-17.

세 케이스 중 앞의 둘은 기존 방어가 실제로 작동하는지 확인한다
(`LedgerEvent.amount`의 `Field(gt=0)`, `listing_service._validate_price`).
세 번째(`extra`의 `api_key` 키)는 §8.3 DoD가 "거부"를 요구하지만, 코드를
읽어 보면 그런 방어가 없다 — `LedgerEvent.extra: dict[str, Decimal | str]`
필드에는 키 이름 검증이 전혀 없고(`contracts/v1.py`), `posting_rules.py`의
각 이벤트 핸들러는 자신이 아는 화이트리스트 키만 골라 읽을 뿐 나머지
키는 조용히 무시한다(`_extra_decimal`/`_extra_str`가 존재하는 키만
본다). `evidence.domain.rules.assert_safe_payload`(secret/token/password/
api_key류 키를 거부하는 바로 그 도구)는 `post_entry.py`·
`postgres_journal_repository.py`가 자신들이 직접 조립한 고정 payload
dict에만 적용하고, `event.extra`에는 한 번도 적용하지 않는다 — 즉
`extra={"api_key": "..."}`를 실어 실제 포스팅 경로(`post_entry`)를 타면
현재 코드는 조용히 성공시킨다. 이건 task-614(LC-17)가 발견한 실결함이라
(코드 수정은 이 리프 범위 밖 — task 지침 "프로덕션 코드 수정 금지") 아래
테스트는 스펙이 요구하는 동작을 그대로 단언하되 `xfail(strict=True)`로
"현재는 실패한다"는 사실 자체를 고정한다 — 조용히 통과시키지 않는다.
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


@pytest.mark.xfail(
    strict=True,
    reason=(
        "실결함(task-614/LC-17 발견, needs_decision): LedgerEvent.extra에 "
        "'api_key'류 키를 실어 post_entry를 태워도 현재 코드는 거부하지 않는다 — "
        "posting_rules.py의 MANUAL_ADJUSTMENT 핸들러가 debit_account/credit_account만 "
        "읽고 나머지 extra 키는 무시하며, assert_safe_payload는 event.extra 자체에 "
        "적용되지 않는다. §8.3 DoD는 거부를 요구하므로 이 테스트는 그 요구를 그대로 "
        "단언하고, 실패 자체를 강제 고정해(strict) 조용한 회귀·조용한 방치를 막는다."
    ),
)
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
