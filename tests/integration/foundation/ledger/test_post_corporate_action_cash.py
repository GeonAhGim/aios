"""LA-25b `application/post_corporate_action_cash.py` 통합테스트 — 실 DB
(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4, §9 LA-25b,
ADR-2026-09-06-G §9.
DoD(task-1753): amount>0인 CASH_DIVIDEND가 보유자 AVAILABLE에 전기,
(instrument, ex_date, holder)에 멱등, 중복 호출 시 분개 1건 유지, 시산표 합 0
유지 + negative(action_type 불일치, amount<=0 미전기)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_corporate_action_cash import (
    UnsupportedCorporateActionError,
    post_corporate_action_cash,
)
from src.foundation.ledger.application.post_entry import LedgerWriteFrozenError
from src.foundation.ledger.contracts.v1 import UserSub
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_CASH_CLEARING
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.idempotency import IdempotencyDigestMismatchError
from src.foundation.market_data.contracts.v1 import CorporateAction
from tests.integration.conftest import create_test_user


def _clock() -> datetime:
    return datetime.now(timezone.utc)


class _RealPorts:
    def __init__(self, pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)
        self.clock = _clock


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


def _dividend_action(
    *, instrument_id: UUID | None = None, ex_date: date | None = None
) -> CorporateAction:
    return CorporateAction(
        action_type="CASH_DIVIDEND",
        instrument_id=instrument_id or uuid4(),
        ex_date=ex_date or date(2026, 9, 7),
        ratio=Decimal("1"),
        cash_amount=Decimal("500.00"),
        source_ref="test:dividend",
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


async def test_posts_cash_dividend_to_holder_available(pool, ports):
    holder = await create_test_user(pool)
    action = _dividend_action()
    amount = Decimal("500.00")
    cash_clearing_before = await _balance(pool, PLATFORM_CASH_CLEARING)

    async with pool.acquire() as conn, conn.transaction():
        entry = await post_corporate_action_cash(
            conn,
            action,
            holder_id=holder,
            amount=amount,
            admin_id=None,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
        )

    assert entry is not None
    assert entry.replayed is False
    lines = await _entry_lines(pool, entry.entry_id)
    debit_total = sum((a for side, a in lines if side == "DEBIT"), Decimal("0"))
    credit_total = sum((a for side, a in lines if side == "CREDIT"), Decimal("0"))
    assert debit_total == credit_total == amount  # 시산표 Σ차=Σ대(직접 단언)

    assert await _balance(pool, ua(holder, UserSub.AVAILABLE)) == amount
    cash_clearing_after = await _balance(pool, PLATFORM_CASH_CLEARING)
    # 배당 현금은 TOPUP_CONFIRMED와 같은 모양으로 외부에서 들어온다 — 자산
    # 계정(PLATFORM:CASH_CLEARING)이 debit으로 증가한다(§4.4 부호 규약).
    assert cash_clearing_after - cash_clearing_before == amount


async def test_duplicate_call_keeps_single_journal_entry(pool, ports):
    holder = await create_test_user(pool)
    action = _dividend_action()
    amount = Decimal("120.00")

    async def _post():
        async with pool.acquire() as conn, conn.transaction():
            return await post_corporate_action_cash(
                conn,
                action,
                holder_id=holder,
                amount=amount,
                admin_id=None,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
            )

    first = await _post()
    second = await _post()

    assert first is not None and second is not None
    assert first.replayed is False
    assert second.replayed is True
    assert second.entry_id == first.entry_id

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref = $1",
            first.event_ref,
        )
    assert count == 1
    # REPLAY라 두 번째 호출은 잔액을 다시 건드리지 않았다 — 최초 1회분만 반영.
    assert await _balance(pool, ua(holder, UserSub.AVAILABLE)) == amount


async def test_different_holder_same_action_is_independent(pool, ports):
    """멱등 범위는 (instrument, ex_date, holder) — 보유자가 다르면 별개
    분개여야 한다."""
    holder_a, holder_b = await create_test_user(pool), await create_test_user(pool)
    action = _dividend_action()

    async def _post(holder: UUID, amount: Decimal):
        async with pool.acquire() as conn, conn.transaction():
            return await post_corporate_action_cash(
                conn,
                action,
                holder_id=holder,
                amount=amount,
                admin_id=None,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
            )

    entry_a = await _post(holder_a, Decimal("300.00"))
    entry_b = await _post(holder_b, Decimal("70.00"))

    assert entry_a is not None and entry_b is not None
    assert entry_a.entry_id != entry_b.entry_id
    assert await _balance(pool, ua(holder_a, UserSub.AVAILABLE)) == Decimal("300.00")
    assert await _balance(pool, ua(holder_b, UserSub.AVAILABLE)) == Decimal("70.00")


async def test_amount_changed_between_calls_is_digest_mismatch(pool, ports):
    """같은 (instrument, ex_date, holder)로 다른 금액이 재전송되면 거부
    (negative case) — 조용히 덮어쓰지 않는다(fail-closed)."""
    holder = await create_test_user(pool)
    action = _dividend_action()

    async with pool.acquire() as conn, conn.transaction():
        await post_corporate_action_cash(
            conn,
            action,
            holder_id=holder,
            amount=Decimal("100.00"),
            admin_id=None,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
        )

    with pytest.raises(IdempotencyDigestMismatchError):
        async with pool.acquire() as conn, conn.transaction():
            await post_corporate_action_cash(
                conn,
                action,
                holder_id=holder,
                amount=Decimal("999.00"),
                admin_id=None,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
            )


async def test_non_cash_dividend_action_type_is_rejected(pool, ports):
    """negative case — SPLIT 등 다른 기업행위 타입은 이 함수의 책임이 아니다."""
    holder = await create_test_user(pool)
    split_action = CorporateAction(
        action_type="SPLIT",
        instrument_id=uuid4(),
        ex_date=date(2026, 9, 7),
        ratio=Decimal("2"),
        source_ref="test:split",
    )

    with pytest.raises(UnsupportedCorporateActionError):
        async with pool.acquire() as conn, conn.transaction():
            await post_corporate_action_cash(
                conn,
                split_action,
                holder_id=holder,
                amount=Decimal("100.00"),
                admin_id=None,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
            )


async def test_zero_amount_does_not_post(pool, ports):
    """negative case — amount<=0은 분개를 만들지 않는다(§3.3 PostingLine.amount>0)."""
    holder = await create_test_user(pool)
    action = _dividend_action()

    async with pool.acquire() as conn, conn.transaction():
        result = await post_corporate_action_cash(
            conn,
            action,
            holder_id=holder,
            amount=Decimal("0.00"),
            admin_id=None,
            trace_id=uuid4(),
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=ports.clock,
        )

    assert result is None
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1",
            f"corp_action_cash:{action.instrument_id}:%",
        )
    assert count == 0


# --- DEEPEN task-2998 — DEPTH 감사(task-2723, docs/audit/DEPTH_LA_LB_LC.md#1753)가
# D1로 판정한 증빙 공백을 메운다: failure-injection(모의 DB/어댑터 예외),
# 수치 성능 단언, 게이트 적색 재현(문서화된 실사고 연계). 프로덕션 코드는
# 무수정 — 이 리프는 증거만 보강한다. ---


async def test_rolls_back_entirely_when_audit_append_fails(pool, ports, monkeypatch):
    """failure-injection — `post_entry`(LC-9)는 저널·잔액을 쓴 *뒤에* 감사
    append(`AuditAppender.append_event_in`)가 실패하면 트랜잭션 전체가
    롤백돼야 한다(§6 "감사 append 실패 → 포스팅 전체 롤백", test_refund.py의
    동일 결정과 같은 근거). 모의 DB/어댑터 예외(OSError)를 주입해 보유자
    AVAILABLE 잔액과 `ledger_journal_entry` 모두 원상태로 남는지 확인한다."""
    holder = await create_test_user(pool)
    action = _dividend_action()
    available_before = await _balance(pool, ua(holder, UserSub.AVAILABLE))
    cash_clearing_before = await _balance(pool, PLATFORM_CASH_CLEARING)

    async def _boom(*args, **kwargs):
        raise OSError("injected audit append failure")

    monkeypatch.setattr(ports.audit, "append_event_in", _boom)

    with pytest.raises(OSError, match="injected audit append failure"):
        async with pool.acquire() as conn, conn.transaction():
            await post_corporate_action_cash(
                conn,
                action,
                holder_id=holder,
                amount=Decimal("500.00"),
                admin_id=None,
                trace_id=uuid4(),
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=ports.clock,
            )

    assert await _balance(pool, ua(holder, UserSub.AVAILABLE)) == available_before
    assert await _balance(pool, PLATFORM_CASH_CLEARING) == cash_clearing_before
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1",
            f"corp_action_cash:{action.instrument_id}:%",
        )
    assert count == 0


async def test_rejected_and_gate_turns_red_when_ledger_frozen(pool, ports):
    """게이트 적색 재현(DoD) — `ledger_control.write_frozen`이 true면 §4.4
    fail-closed 전역 게이트가 배당 전기 자체를 막는다(`LedgerWriteFrozenError`).
    문서화된 실사고 연계: task-312/커밋 b120c35c가 이 전역 단일 행이 테스트
    사이에 잔류해 무관한 `post_entry` 호출을 오염시킨 사고였다(DEPTH 감사
    task-2723 #420) — 이 테스트는 같은 방식으로 적색을 실제로 만들되,
    `finally`로 반드시 원상복구해 그 사고를 재현하지 않는다."""
    holder = await create_test_user(pool)
    action = _dividend_action()

    async with pool.acquire() as conn:
        await conn.execute("UPDATE ledger_control SET write_frozen = TRUE WHERE id = 1")
    try:
        with pytest.raises(LedgerWriteFrozenError):
            async with pool.acquire() as conn, conn.transaction():
                await post_corporate_action_cash(
                    conn,
                    action,
                    holder_id=holder,
                    amount=Decimal("500.00"),
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

    assert await _balance(pool, ua(holder, UserSub.AVAILABLE)) == Decimal("0")
    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT COUNT(*) FROM ledger_journal_entry WHERE event_ref LIKE $1",
            f"corp_action_cash:{action.instrument_id}:%",
        )
    assert count == 0


_MAX_POST_ROUND_TRIPS = 30


@pytest.mark.perf
async def test_round_trip_count_regression_guard(pool, ports):
    """수치 성능 단언(DoD) — `test_refund.py::test_refund_r1_round_trip_count_
    regression_guard`/`test_post_entry.py`와 동일한 결정을 따른다: 공유
    CI 환경의 절대 지연(ms)은 이 파일이 통제할 수 없는 변동성을 이미 여러
    리프(task-920/1029/1038/2962)에서 드러냈으므로 게이트로 쓰지 않고,
    구조 회귀(호출당 순차 DB 왕복 수가 늘어나는 실제 코드 결함)만 차단
    게이트로 잡는다."""
    holder = await create_test_user(pool)
    action = _dividend_action()

    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    async with pool.acquire() as conn:
        conn.add_query_logger(_log)
        try:
            async with conn.transaction():
                entry = await post_corporate_action_cash(
                    conn,
                    action,
                    holder_id=holder,
                    amount=Decimal("500.00"),
                    admin_id=None,
                    trace_id=uuid4(),
                    journal=ports.journal,
                    balances=ports.balances,
                    audit=ports.audit,
                    clock=ports.clock,
                )
        finally:
            conn.remove_query_logger(_log)

    assert entry is not None and entry.replayed is False
    print(
        f"[LA-25b post_corporate_action_cash] sequential DB round trips={len(queries)} "
        f"(max={_MAX_POST_ROUND_TRIPS})"
    )
    assert len(queries) <= _MAX_POST_ROUND_TRIPS, (
        f"post_corporate_action_cash 순차 DB 왕복 수({len(queries)})가 상한"
        f"({_MAX_POST_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
