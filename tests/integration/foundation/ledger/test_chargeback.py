"""LC-15b `application/chargeback.py` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4 CHARGEBACK, §9 LC-15.
DoD(task-487): 가용분 충분/부족 두 시나리오 각각 Σ차=Σ대 직접 단언 + 부족분은
RECEIVABLE 음수 허용 계정으로 이연(대손 이연) + 같은 topup_id 재차지백은 새
분개를 만들지 않음(LC-9 REPLAY) + 잔액이 바뀐 뒤 재요청은 DIGEST_MISMATCH로
거부(negative case).

`PLATFORM:CASH_CLEARING`은 LC-6 시드 계정이자 `allow_negative=False`라(§4.4
CHARGEBACK이 이 계정을 credit — 잔액 감소) 실제로 그만큼의 선행 입금이
있어야 한다 — `ledger_balance`를 직접 조작해 사용자 `AVAILABLE`만 세팅하는
`test_refund.py::_seed_available`식 지름길을 여기서는 쓸 수 없다. 그래서
실제 `TOPUP_CONFIRMED`를 먼저 포스팅해 CASH_CLEARING을 함께 채운다."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.chargeback import post_chargeback
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.application.purchase_flow import ensure_account
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_CASH_CLEARING
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.idempotency import IdempotencyDigestMismatchError
from tests.integration.conftest import create_test_user


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _new_topup_id() -> int:
    return uuid4().int % 2_000_000_000


class _RealPorts:
    def __init__(self, pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)
        self.clock = _clock


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


async def _post(pool, ports, event: LedgerEvent) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await post_entry(
            conn, event, journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=ports.clock,
        )


async def _topup(pool, ports, user_id: UUID, amount: Decimal, ref: str) -> None:
    async with pool.acquire() as conn:
        await ensure_account(conn, ua(user_id, UserSub.AVAILABLE), Currency.KRW)
    await _post(
        pool, ports,
        LedgerEvent(
            event_type=LedgerEventType.TOPUP_CONFIRMED, event_ref=ref, tenant_id=None,
            actor_subject_id=None, trace_id=uuid4(), amount=amount, currency=Currency.KRW,
            parties={"user": user_id}, extra={},
        ),
    )


async def _spend_via_purchase(
    pool, ports, buyer: UUID, seller: UUID, amount: Decimal, ref: str
) -> None:
    """`buyer` AVAILABLE을 HELD를 거쳐 `seller` PENDING_PAYOUT으로 전액(무수수료)
    옮겨 "이미 다 썼다"를 흉내낸다 — `purchase_flow.place_hold`/`capture_hold`
    가 아니라 원시 이벤트를 직접 포스팅한다: 그 함수들의 `_reconcile_available`
    이 `user_wallets`(이 테스트가 쓰지 않는 레거시 투영) 드리프트를 감지해
    `PLATFORM:CASH_CLEARING`을 상대 계정으로 끼워 넣으므로(§4.4 무관 계정),
    이 헬퍼가 세팅하려는 순수 원장 시나리오와 맞지 않는다(`test_refund.py`의
    `_consume_available`과 동일한 이유)."""
    async with pool.acquire() as conn:
        await ensure_account(conn, ua(buyer, UserSub.HELD), Currency.KRW)
        await ensure_account(conn, ua(seller, UserSub.PENDING_PAYOUT), Currency.KRW)
    await _post(
        pool, ports,
        LedgerEvent(
            event_type=LedgerEventType.HOLD_PLACED, event_ref=f"{ref}:hold", tenant_id=None,
            actor_subject_id=None, trace_id=uuid4(), amount=amount, currency=Currency.KRW,
            parties={"buyer": buyer}, extra={},
        ),
    )
    await _post(
        pool, ports,
        LedgerEvent(
            event_type=LedgerEventType.HOLD_CAPTURED, event_ref=f"{ref}:capture", tenant_id=None,
            actor_subject_id=None, trace_id=uuid4(), amount=amount, currency=Currency.KRW,
            parties={"buyer": buyer, "seller": seller},
            extra={"commission_rate": Decimal("0")},
        ),
    )


async def _balance(pool, account_code: str) -> Decimal:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            account_code,
        )
    return value if value is not None else Decimal("0")


async def _entry_lines(pool, entry_id) -> list[tuple[str, Decimal]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT side, amount FROM ledger_posting_line WHERE entry_id = $1", entry_id
        )
    return [(row["side"], row["amount"]) for row in rows]


async def test_chargeback_full_coverage_debits_available_only(pool, ports):
    user = await create_test_user(pool)
    topup_id = _new_topup_id()
    await _topup(pool, ports, user, Decimal("200.00"), f"test-chargeback:topup:{topup_id}")
    amount = Decimal("150.00")
    cash_clearing_before = await _balance(pool, PLATFORM_CASH_CLEARING)

    async with pool.acquire() as conn, conn.transaction():
        result = await post_chargeback(
            conn, topup_id=topup_id, user_id=user, amount=amount, admin_id=None,
            trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=ports.clock,
        )

    assert result.user_available_amount == Decimal("200.00")
    lines = await _entry_lines(pool, result.entry.entry_id)
    debit_total = sum((a for side, a in lines if side == "DEBIT"), Decimal("0"))
    credit_total = sum((a for side, a in lines if side == "CREDIT"), Decimal("0"))
    assert debit_total == credit_total == amount  # Σ차=Σ대(직접 단언)
    assert len(lines) == 2  # AVAILABLE 전액 커버 — RECEIVABLE 행 없음

    assert await _balance(pool, ua(user, UserSub.AVAILABLE)) == Decimal("50.00")
    assert await _balance(pool, ua(user, UserSub.RECEIVABLE)) == Decimal("0.00")
    cash_clearing_after = await _balance(pool, PLATFORM_CASH_CLEARING)
    assert cash_clearing_after - cash_clearing_before == -amount


async def test_chargeback_shortfall_splits_available_and_receivable(pool, ports):
    user, other = await create_test_user(pool), await create_test_user(pool)
    topup_id = _new_topup_id()
    await _topup(pool, ports, user, Decimal("150.00"), f"test-chargeback:topup:{topup_id}")
    # 정산금 대부분을 이미 다른 사용자에게 소비 — 150.00 중 40.00만 남긴다.
    await _spend_via_purchase(
        pool, ports, user, other, Decimal("110.00"), f"test-chargeback:spend:{topup_id}"
    )
    amount = Decimal("150.00")
    assert await _balance(pool, ua(user, UserSub.AVAILABLE)) == Decimal("40.00")
    cash_clearing_before = await _balance(pool, PLATFORM_CASH_CLEARING)

    async with pool.acquire() as conn, conn.transaction():
        result = await post_chargeback(
            conn, topup_id=topup_id, user_id=user, amount=amount, admin_id=None,
            trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=ports.clock,
        )

    assert result.user_available_amount == Decimal("40.00")
    lines = await _entry_lines(pool, result.entry.entry_id)
    debit_total = sum((a for side, a in lines if side == "DEBIT"), Decimal("0"))
    credit_total = sum((a for side, a in lines if side == "CREDIT"), Decimal("0"))
    assert debit_total == credit_total == amount
    assert len(lines) == 3  # AVAILABLE(가용분) + RECEIVABLE(부족분) + CASH_CLEARING

    assert await _balance(pool, ua(user, UserSub.AVAILABLE)) == Decimal("0.00")
    assert await _balance(pool, ua(user, UserSub.RECEIVABLE)) == Decimal("110.00")  # 대손 이연
    cash_clearing_after = await _balance(pool, PLATFORM_CASH_CLEARING)
    assert cash_clearing_after - cash_clearing_before == -amount


async def test_chargeback_duplicate_topup_id_is_not_double_posted(pool, ports):
    user = await create_test_user(pool)
    topup_id = _new_topup_id()
    await _topup(pool, ports, user, Decimal("200.00"), f"test-chargeback:topup:{topup_id}")

    async def _chargeback():
        async with pool.acquire() as conn, conn.transaction():
            return await post_chargeback(
                conn, topup_id=topup_id, user_id=user, amount=Decimal("50.00"), admin_id=None,
                trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
                audit=ports.audit, clock=ports.clock,
            )

    first = await _chargeback()
    assert first.entry.replayed is False

    second = await _chargeback()
    assert second.entry.entry_id == first.entry.entry_id
    assert second.entry.replayed is True

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1",
            f"chargeback:topup:{topup_id}",
        )
    assert count == 1
    # 재요청이 새 분개를 안 만들었으니 AVAILABLE은 첫 호출분(50.00)만 빠졌다.
    assert await _balance(pool, ua(user, UserSub.AVAILABLE)) == Decimal("150.00")


async def test_chargeback_digest_mismatch_when_balance_changed_between_calls(pool, ports):
    """같은 `topup_id`로 두 번째 호출 전에 사용자 AVAILABLE 잔액이 바뀌면
    covered/shortfall 분할이 달라져 분개행 digest가 어긋난다 — LC-9가
    `IdempotencyDigestMismatchError`(409)로 거부한다(negative case)."""
    user, other = await create_test_user(pool), await create_test_user(pool)
    topup_id = _new_topup_id()
    await _topup(pool, ports, user, Decimal("200.00"), f"test-chargeback:topup:{topup_id}")

    async with pool.acquire() as conn, conn.transaction():
        first = await post_chargeback(
            conn, topup_id=topup_id, user_id=user, amount=Decimal("50.00"), admin_id=None,
            trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=ports.clock,
        )
    assert first.entry.replayed is False  # covered=50.00/shortfall=0.00로 포스팅됨

    # 남은 AVAILABLE(150.00) 전부를 소비 — 두 번째 호출은 covered=0/shortfall=50
    # 이 되어 첫 호출과 분개행이 달라진다(같은 event_ref, 다른 lines).
    await _spend_via_purchase(
        pool, ports, user, other, Decimal("150.00"), f"test-chargeback:spend:{topup_id}"
    )

    with pytest.raises(IdempotencyDigestMismatchError):
        async with pool.acquire() as conn, conn.transaction():
            await post_chargeback(
                conn, topup_id=topup_id, user_id=user, amount=Decimal("50.00"), admin_id=None,
                trace_id=uuid4(), journal=ports.journal, balances=ports.balances,
                audit=ports.audit, clock=ports.clock,
            )
