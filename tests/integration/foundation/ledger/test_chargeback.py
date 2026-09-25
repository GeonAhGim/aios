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
실제 `TOPUP_CONFIRMED`를 먼저 포스팅해 CASH_CLEARING을 함께 채운다.

DEEPEN(task-2965, docs/audit/DEPTH_LA_LB_LC.md) — 위 3건(D1)은 negative
1건(digest mismatch)뿐이고 실패주입·성능단언·게이트적색 재현이 없었다.
아래를 추가해 D3로 올린다: negative 2건 추가(전역 동결 fail-closed,
LC-17 extra 화이트리스트) + 실패주입 2건(감사 append 실패 롤백, 잔액
갱신 도중 DB 커넥션 단절 시 부분쓰기 없음) + 성능 단언 1건(왕복 지연
예산) + 게이트 적색 재현 1건(부분커버 -> 재생(REPLAY) -> 잔액변화 후
재생(DIGEST_MISMATCH)을 한 topup_id로 이어 각 단계가 다음 단계로
새지 않음을 증명)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.chargeback import post_chargeback
from src.foundation.ledger.application.post_entry import (
    LedgerEventExtraRejectedError,
    LedgerWriteFrozenError,
    post_entry,
)
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


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs: object) -> None:
        raise RuntimeError("injected audit failure")


async def _post(pool, ports, event: LedgerEvent) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await post_entry(
            conn,
            event,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
        )


async def _topup(pool, ports, user_id: UUID, amount: Decimal, ref: str) -> None:
    async with pool.acquire() as conn:
        await ensure_account(conn, ua(user_id, UserSub.AVAILABLE), Currency.KRW)
    await _post(
        pool,
        ports,
        LedgerEvent(
            event_type=LedgerEventType.TOPUP_CONFIRMED,
            event_ref=ref,
            tenant_id=None,
            actor_subject_id=None,
            trace_id=uuid4(),
            amount=amount,
            currency=Currency.KRW,
            parties={"user": user_id},
            extra={},
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
        pool,
        ports,
        LedgerEvent(
            event_type=LedgerEventType.HOLD_PLACED,
            event_ref=f"{ref}:hold",
            tenant_id=None,
            actor_subject_id=None,
            trace_id=uuid4(),
            amount=amount,
            currency=Currency.KRW,
            parties={"buyer": buyer},
            extra={},
        ),
    )
    await _post(
        pool,
        ports,
        LedgerEvent(
            event_type=LedgerEventType.HOLD_CAPTURED,
            event_ref=f"{ref}:capture",
            tenant_id=None,
            actor_subject_id=None,
            trace_id=uuid4(),
            amount=amount,
            currency=Currency.KRW,
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
            conn,
            topup_id=topup_id,
            user_id=user,
            amount=amount,
            admin_id=None,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
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
            conn,
            topup_id=topup_id,
            user_id=user,
            amount=amount,
            admin_id=None,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
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
                conn,
                topup_id=topup_id,
                user_id=user,
                amount=Decimal("50.00"),
                admin_id=None,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
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
            conn,
            topup_id=topup_id,
            user_id=user,
            amount=Decimal("50.00"),
            admin_id=None,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
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
                conn,
                topup_id=topup_id,
                user_id=user,
                amount=Decimal("50.00"),
                admin_id=None,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
            )


# ---- negative(추가): 전역 동결 fail-closed, LC-17 extra 화이트리스트 ----


async def test_chargeback_rejected_when_ledger_frozen(pool, ports):
    """`ledger_control.write_frozen=true`(무결성 위반 감지 후 fail-closed,
    §4.4)면 관리자 차지백도 예외 없이 거부된다 — LC-15b가 전역 동결을
    우회하는 별도 경로를 두지 않았음을 증명한다."""
    user = await create_test_user(pool)
    topup_id = _new_topup_id()
    await _topup(pool, ports, user, Decimal("200.00"), f"test-chargeback:topup:{topup_id}")

    async with pool.acquire() as conn:
        await conn.execute("UPDATE ledger_control SET write_frozen = TRUE WHERE id = 1")
    try:
        with pytest.raises(LedgerWriteFrozenError):
            async with pool.acquire() as conn, conn.transaction():
                await post_chargeback(
                    conn,
                    topup_id=topup_id,
                    user_id=user,
                    amount=Decimal("50.00"),
                    admin_id=None,
                    trace_id=uuid4(),
                    journal=ports.journal,
                    balances=ports.balances,
                    audit=ports.audit,
                    clock=ports.clock,
                )
    finally:
        async with pool.acquire() as conn:
            await conn.execute("UPDATE ledger_control SET write_frozen = FALSE WHERE id = 1")

    assert await _balance(pool, ua(user, UserSub.AVAILABLE)) == Decimal("200.00")
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1",
            f"chargeback:topup:{topup_id}",
        )
    assert count == 0


async def test_chargeback_rejects_disallowed_extra_key(pool, ports):
    """LC-17 결함 A(§8.3) extra 화이트리스트가 CHARGEBACK에도 적용된다 —
    허용된 `user_available_amount` 외에 다른 사건 타입의 키(예:
    `commission_rate`)가 섞이면 포스팅 전체가 거부되고 DENIED 감사
    이벤트가 남는다. `post_chargeback`은 extra를 스스로 조립해 이 경로를
    직접 트리거할 수 없으므로 `post_entry`를 직접 호출한다."""
    user = await create_test_user(pool)
    topup_id = _new_topup_id()
    await _topup(pool, ports, user, Decimal("200.00"), f"test-chargeback:topup:{topup_id}")
    trace_id = uuid4()
    event = LedgerEvent(
        event_type=LedgerEventType.CHARGEBACK,
        event_ref=f"chargeback:topup:{topup_id}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=trace_id,
        amount=Decimal("50.00"),
        currency=Currency.KRW,
        parties={"user": user},
        extra={"user_available_amount": Decimal("200.00"), "commission_rate": Decimal("0.05")},
    )

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(LedgerEventExtraRejectedError):
            await post_entry(
                conn,
                event,
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
            )

    async with pool.acquire() as conn:
        outcome = await conn.fetchval(
            "SELECT outcome FROM foundation_audit_event WHERE aggregate_id = $1 "
            "AND outcome = 'DENIED'",
            trace_id,
        )
    assert outcome == "DENIED"
    assert await _balance(pool, ua(user, UserSub.AVAILABLE)) == Decimal("200.00")


# ---- 실패 주입(DB/감사 어댑터 결함) ----


async def test_chargeback_rolls_back_when_audit_append_fails(pool, ports):
    """감사 이벤트 기록이 실패하면(네트워크 단절·DB 장애를 흉내) 이미
    실행된 잔액조회 FOR UPDATE·저널 append까지 트랜잭션째 롤백돼야 한다 —
    증적 없는 원장 변경(entry는 있는데 audit이 없는 상태)이 절대 허용되지
    않는다(§6 실패 모드 "C")."""
    user = await create_test_user(pool)
    topup_id = _new_topup_id()
    await _topup(pool, ports, user, Decimal("200.00"), f"test-chargeback:topup:{topup_id}")

    with pytest.raises(RuntimeError, match="injected audit failure"):
        async with pool.acquire() as conn, conn.transaction():
            await post_chargeback(
                conn,
                topup_id=topup_id,
                user_id=user,
                amount=Decimal("50.00"),
                admin_id=None,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=_BoomAuditAppender(),
                clock=ports.clock,
            )

    assert await _balance(pool, ua(user, UserSub.AVAILABLE)) == Decimal("200.00")
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1",
            f"chargeback:topup:{topup_id}",
        )
    assert count == 0


async def test_chargeback_connection_failure_during_balance_apply_leaves_no_partial_write(
    pool, ports, monkeypatch
):
    """부족분 이연 시나리오(AVAILABLE + RECEIVABLE + CASH_CLEARING 3계정
    갱신)에서 두 번째 계정 갱신 도중 DB 커넥션이 끊기면(장애 주입) 이미
    성공한 첫 계정 갱신까지 트랜잭션째 롤백돼야 한다 — 일부 계정만 반영된
    채 남으면 Σ차=Σ대 분개 불변식이 실제 잔액과 어긋난다."""
    user, other = await create_test_user(pool), await create_test_user(pool)
    topup_id = _new_topup_id()
    await _topup(pool, ports, user, Decimal("150.00"), f"test-chargeback:topup:{topup_id}")
    await _spend_via_purchase(
        pool, ports, user, other, Decimal("110.00"), f"test-chargeback:spend:{topup_id}"
    )
    amount = Decimal("150.00")
    available_before = await _balance(pool, ua(user, UserSub.AVAILABLE))
    receivable_before = await _balance(pool, ua(user, UserSub.RECEIVABLE))
    cash_clearing_before = await _balance(pool, PLATFORM_CASH_CLEARING)

    real_apply = PostgresBalanceRepository.apply
    call_count = 0

    async def _boom_after_first(self, conn, account_id, delta_balance, delta_held, expected_seq):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncpg.PostgresConnectionError("injected connection failure")
        return await real_apply(self, conn, account_id, delta_balance, delta_held, expected_seq)

    monkeypatch.setattr(PostgresBalanceRepository, "apply", _boom_after_first)
    with pytest.raises(asyncpg.PostgresConnectionError):
        async with pool.acquire() as conn, conn.transaction():
            await post_chargeback(
                conn,
                topup_id=topup_id,
                user_id=user,
                amount=amount,
                admin_id=None,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
            )
    monkeypatch.undo()

    assert await _balance(pool, ua(user, UserSub.AVAILABLE)) == available_before
    assert await _balance(pool, ua(user, UserSub.RECEIVABLE)) == receivable_before
    assert await _balance(pool, PLATFORM_CASH_CLEARING) == cash_clearing_before
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1",
            f"chargeback:topup:{topup_id}",
        )
    assert count == 0


# ---- 성능 단언 ----


@pytest.mark.perf
async def test_post_chargeback_meets_latency_budget(pool, ports):
    """단일 CHARGEBACK 포스팅(잔액조회 FOR UPDATE + 분개 append + 잔액
    갱신 + 감사) 왕복이 절대시간 예산 내여야 한다 — 관리자 콘솔에서 동기
    호출되는 저빈도 경로이지만 상한 없는 지연 자체가 결함이다."""
    user = await create_test_user(pool)
    topup_id = _new_topup_id()
    await _topup(pool, ports, user, Decimal("200.00"), f"test-chargeback:topup:{topup_id}")
    budget_sec = 2.0  # 실측 로컬 <0.1s, CI 편차 감안

    start = time.perf_counter()
    async with pool.acquire() as conn, conn.transaction():
        result = await post_chargeback(
            conn,
            topup_id=topup_id,
            user_id=user,
            amount=Decimal("150.00"),
            admin_id=None,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
        )
    elapsed = time.perf_counter() - start

    print(f"[LC-15b post_chargeback] 왕복 {elapsed:.3f}s (budget<{budget_sec}s)")
    assert result.entry.replayed is False
    assert elapsed < budget_sec, (
        f"차지백 포스팅이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


# ---- 게이트 적색 재현 — 부분커버 -> 재생 -> 잔액변화 후 재생을 한 topup_id로 재생 ----


async def test_chargeback_lifecycle_replay_then_balance_drift_digest_mismatch_isolated(pool, ports):
    """DEPTH 감사가 지적한 게이트 적색을 시간축으로 재생한다: 전액 커버
    차지백(가용분만으로 충분) -> 잔액이 그대로인 동안 동일 요청 재생(멱등,
    새 분개 없음, LC-9 REPLAY는 매 호출 시 *현재* 가용잔액으로
    covered/shortfall을 재계산하므로 재생 사이에 가용분이 요청액 아래로
    떨어지지만 않으면 분할이 그대로 유지된다는 것 자체가 증명 대상이다) ->
    그 뒤 가용분을 요청액 아래로 떨어뜨리고 재요청(분할이 달라져
    DIGEST_MISMATCH, 거부는 상태를 남기지 않음). 각 단계의 상태가 다음
    단계로 새지 않는지 — 특히 거부된 3단계가 2단계 상태를 조금도 흔들지
    않는지 — 를 증명한다."""
    user, other = await create_test_user(pool), await create_test_user(pool)
    topup_id = _new_topup_id()
    await _topup(pool, ports, user, Decimal("200.00"), f"test-chargeback:topup:{topup_id}")
    amount = Decimal("50.00")

    # 1단계: 전액 커버(가용분 200 >= 요청액 50)로 최초 포스팅.
    async with pool.acquire() as conn, conn.transaction():
        stage1 = await post_chargeback(
            conn,
            topup_id=topup_id,
            user_id=user,
            amount=amount,
            admin_id=None,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
        )
    assert stage1.entry.replayed is False
    available_after_stage1 = await _balance(pool, ua(user, UserSub.AVAILABLE))
    receivable_after_stage1 = await _balance(pool, ua(user, UserSub.RECEIVABLE))
    assert available_after_stage1 == Decimal("150.00")
    assert receivable_after_stage1 == Decimal("0.00")

    # 2단계: 가용분이 여전히 요청액을 넘는 채(150 >= 50) 동일 요청 재생 —
    # 재계산해도 covered/shortfall이 같아 digest가 일치 -> REPLAY, 잔액 불변.
    async with pool.acquire() as conn, conn.transaction():
        stage2 = await post_chargeback(
            conn,
            topup_id=topup_id,
            user_id=user,
            amount=amount,
            admin_id=None,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
        )
    assert stage2.entry.replayed is True
    assert stage2.entry.entry_id == stage1.entry.entry_id
    assert await _balance(pool, ua(user, UserSub.AVAILABLE)) == available_after_stage1
    assert await _balance(pool, ua(user, UserSub.RECEIVABLE)) == receivable_after_stage1

    # 3단계: 가용분을 요청액(50) 아래(10)로 떨어뜨린 뒤 재요청 — 분할이
    # (covered=10, shortfall=40)으로 달라져 기존 분개 digest와 어긋난다.
    await _spend_via_purchase(
        pool, ports, user, other, Decimal("140.00"), f"test-chargeback:spend:{topup_id}"
    )
    available_before_stage3 = await _balance(pool, ua(user, UserSub.AVAILABLE))
    assert available_before_stage3 == Decimal("10.00")
    with pytest.raises(IdempotencyDigestMismatchError):
        async with pool.acquire() as conn, conn.transaction():
            await post_chargeback(
                conn,
                topup_id=topup_id,
                user_id=user,
                amount=amount,
                admin_id=None,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
            )

    # 거부된 3단계가 상태를 조금도 흔들지 않았는지 확인 — 소비 후 가용분
    # (10.00)과 RECEIVABLE(0.00)이 그대로고, 새 분개도 생기지 않았다.
    assert await _balance(pool, ua(user, UserSub.AVAILABLE)) == available_before_stage3
    assert await _balance(pool, ua(user, UserSub.RECEIVABLE)) == receivable_after_stage1
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1",
            f"chargeback:topup:{topup_id}",
        )
    assert count == 1
