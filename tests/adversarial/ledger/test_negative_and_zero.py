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

DEEPEN(task-2974, docs/audit/DEPTH_LA_LB_LC.md) — DEPTH 감사(task-2723)가
원 task-626을 D1로 판정했다: negative 3건은 있으나 failure-injection(DB/
어댑터 결함 시뮬레이션)과 수치 성능 단언이 없었다. 아래를 추가해 D2로
올린다: 실패주입 1건(`_deny_unsafe_extra`가 쓰는 감사 어댑터가
`asyncpg.PostgresConnectionError`를 던질 때 — 거부 사유 자체가 DB에
닿지 못해도 원 예외가 삼켜지지 않고 그대로 전파되며, 트랜잭션 밖에서
DENIED 행도 저널 행도 전혀 남지 않는지 증명) + 성능 단언 1건(extra 거부
왕복 지연을 같은 풀의 기준 왕복비용(`SELECT 1`)에 정규화한 예산 — 절대
ms 상수는 `tests/integration/foundation/ledger/test_perf_journal.py`
(task-920/1029)가 겪은 CI 편차 상시적색 전례가 있어 쓰지 않는다,
`tests/integration/oms/test_submit_order_failure_injection.py`(task-2765)와
동일 기법)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest
from pydantic import ValidationError

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import (
    LedgerEventExtraRejectedError,
    post_entry,
)
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    PLATFORM_COMMISSION_REVENUE,
)
from src.services.listing_service import ListingError, ListingService
from tests.integration.conftest import create_test_user


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _always_eligible(strategy_id: str, version: str, seller_user_id: object = None) -> bool:
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


def _api_key_shaped_event(trace_id: object = None) -> LedgerEvent:
    return LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=f"manual:{uuid4()}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=trace_id if trace_id is not None else uuid4(),
        amount=Decimal("1.00"),
        currency=Currency.KRW,
        parties={},
        extra={
            "api_key": "sk-should-never-reach-storage",
            "debit_account": PLATFORM_CASH_CLEARING,
            "credit_account": PLATFORM_COMMISSION_REVENUE,
        },
    )


async def test_post_entry_extra_rejection_propagates_when_audit_adapter_connection_fails(
    pool, monkeypatch
) -> None:
    """실패주입(DB/어댑터 결함 시뮬레이션) — `_deny_unsafe_extra`가 거부
    사유를 감사에 남기려 할 때 감사 어댑터가 DB 커넥션 단절
    (`asyncpg.PostgresConnectionError`)을 만나면, 그 원 예외가
    `LedgerEventExtraRejectedError`로 조용히 삼켜지지 않고 그대로
    전파돼야 한다(fail-closed, 모듈 docstring §6 "C 감사 append 실패 →
    포스팅 전체 롤백"). 거부 자체가 감사 기록 없이 일어나면 결함 A 방어가
    증적을 남기지 않고 넘어간 셈이라 그 자체가 결함이다. 트랜잭션 롤백
    뒤 저널 행도 DENIED 감사 행도 전혀 남지 않았는지 새 커넥션으로
    확인한다."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)

    async def _boom_append_event_in(self: object, conn: object, **kwargs: object) -> None:
        raise asyncpg.PostgresConnectionError("injected connection failure")

    monkeypatch.setattr(PostgresAuditEventRepository, "append_event_in", _boom_append_event_in)
    audit = PostgresAuditEventRepository(pool)
    trace_id = uuid4()
    event = _api_key_shaped_event(trace_id)

    with pytest.raises(asyncpg.PostgresConnectionError):
        async with pool.acquire() as conn, conn.transaction():
            await post_entry(
                conn, event, journal=journal, balances=balances, audit=audit, clock=_clock
            )
    monkeypatch.undo()

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1", event.event_ref
        )
        audit_count = await conn.fetchval(
            "SELECT COUNT(*) FROM foundation_audit_event WHERE trace_id = $1", trace_id
        )
    assert journal_count == 0
    assert audit_count == 0


@pytest.mark.perf
async def test_post_entry_extra_rejection_latency_within_normalized_budget(pool) -> None:
    """수치 성능 단언 — LC-17 결함 A 거부 경로(`assert_safe_payload` 순수
    검증 + DENIED 감사 append 1회 DB 왕복)의 p95 지연이 같은 풀의 기준
    왕복비용(`SELECT 1`) 대비 정규화한 임계를 넘지 않는다. 절대 ms
    상수는 `tests/integration/foundation/ledger/test_perf_journal.py`
    (task-920/1029)가 겪은 CI 편차 상시적색 전례 때문에 쓰지 않는다."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    reps = 15

    async def _median_ms(step):  # type: ignore[no-untyped-def]
        samples: list[float] = []
        for _ in range(reps):
            t0 = time.perf_counter()
            await step()
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        return samples[len(samples) // 2]

    async with pool.acquire() as conn:
        baseline_median = await _median_ms(lambda: conn.fetchval("SELECT 1"))

    async def _one_rejection() -> None:
        event = _api_key_shaped_event()
        with pytest.raises(LedgerEventExtraRejectedError):
            async with pool.acquire() as conn, conn.transaction():
                await post_entry(
                    conn, event, journal=journal, balances=balances, audit=audit, clock=_clock
                )

    rejection_median = await _median_ms(_one_rejection)

    budget_ms = max(400.0, 40.0 * baseline_median)
    print(  # noqa: T201 — 실측치는 비차단 기록, 게이트는 아래 assert.
        f"\npost_entry extra-rejection median={rejection_median:.3f}ms "
        f"baseline(SELECT 1) median={baseline_median:.3f}ms budget={budget_ms:.3f}ms"
    )
    assert rejection_median < budget_ms
