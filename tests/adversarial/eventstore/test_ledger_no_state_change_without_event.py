"""FA-16 — adversarial: `post_entry` never changes `ledger_balance` without
a matching `ledger_journal_entry` row, in the same transaction.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-16
(선행 FA-14=task-2050, FA-15=task-2060). FA-14가 정한 이벤트 원천을 그대로
쓴다 — 새 이벤트 테이블은 없다: 원장의 이벤트 원천은 `ledger_journal_entry`
(`post_entry.py`, LC-9)이다. `journal.append(conn, event, lines)`가
돌려주는 `entry_view`가 있어야만 잔액 갱신·감사 payload를 만들 수 있어,
저널 append 없이 잔액이 바뀌는 경로는 코드 구조상 존재하지 않는다.

DoD(1) "배선증명 없이 통과하는 테스트는 반려" —
`test_ledger_post_entry_each_call_produces_exactly_one_journal_entry`는
`ledger_journal_entry` 행 수를 잔액 증가분과 직접 비교하므로,
`post_entry.py`의 `entry_view = await journal.append(conn, event, lines)`
줄을 지우면 다음 줄(`entry_view.replayed`)이 `AttributeError`로 즉시
죽어 이 테스트가 FAIL한다 — 실제로 그 줄을 임시로 주석 처리하고 이
테스트가 FAIL하는 것을 확인한 뒤 원복했다(회귀 방지를 위해 sabotage
코드는 커밋에 남기지 않는다).

DEPTH 감사(task-2724, docs/audit/DEPTH_FA.md #task-2061) 보강분(이 파일
하단): 위 확인은 커밋 메시지 서술로만 남고 리포에 자동 재현 가능한 형태로
보존되지 않았다 — 그리고 이 파일에는 성능 단언이 전혀 없었다(`test_perf_
journal.py`의 왕복수 가드는 `PostgresJournalRepository.append()` 단독만
재고, 잔액 갱신까지 포함한 `post_entry()` 전체는 재지 않는다 — 그 파일
모듈 docstring 참고). `test_ledger_bypassed_journal_write_leaves_balance_
changed_with_no_entry`가 소스를 건드리지 않고 "append가 실제로 쓰지
않으면서 그럴듯한 값만 돌려주면?" 우회를 이중체로 영구 재현하고,
`test_ledger_post_entry_round_trip_count_stays_bounded`가 `post_entry()`
전체 왕복 수 회귀 가드를 더한다(절대 ms 대신 왕복 수 — task-920/1029
전례와 동일 이유).
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import (
    AccountType,
    JournalEntryView,
    LedgerEvent,
    LedgerEventType,
    UserSub,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account

_MAX_POST_ENTRY_ROUND_TRIPS = 23


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _create_user_available_account(pool: asyncpg.Pool, user_id: UUID) -> str:
    """잔액은 항상 0으로 시작한다(FA-15a/esc-2115: `ledger_balance`에 잔액을
    raw로 심지 않는다 — 이 파일의 테스트가 실제로 필요로 하는 잔액은 전부
    이후 `post_entry` 호출이 만든다)."""
    code = user_account(user_id, UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, $2, $3, FALSE) RETURNING account_id",
            code,
            AccountType.LIABILITY.value,
            Currency.KRW.value,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, allow_negative, last_entry_seq) "
            "VALUES ($1, FALSE, 0)",
            account_id,
        )
    return code


def _topup_event(
    *, event_ref: str, user_id: UUID, amount: Decimal = Decimal("10.00")
) -> LedgerEvent:
    return LedgerEvent(
        event_type=LedgerEventType.TOPUP_CONFIRMED,
        event_ref=event_ref,
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=amount,
        currency=Currency.KRW,
        parties={"user": user_id},
        extra={},
    )


class _BoomLedgerJournal:
    async def find_by_idempotency_key(self, conn, key):  # noqa: ANN001, ARG002
        return None

    async def append(self, conn, entry, lines):  # noqa: ANN001, ARG002 -- 테스트 전용
        raise RuntimeError("injected ledger_journal_entry append failure")


async def test_ledger_post_entry_each_call_produces_exactly_one_journal_entry(pool):
    """Positive — DoD(1). 서로 다른 두 topup 각각이 정확히 하나의
    `ledger_journal_entry` 행을 남기고 사용자 잔액이 정확히 그만큼 늘어난다."""
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    refs = [f"fa16-adv-topup:{uuid4().hex}" for _ in range(2)]

    for ref in refs:
        async with pool.acquire() as conn, conn.transaction():
            await post_entry(
                conn,
                _topup_event(event_ref=ref, user_id=user_id, amount=Decimal("10.00")),
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
            )

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE event_ref = ANY($1::text[])", refs
        )
        balance = await conn.fetchval(
            "SELECT balance FROM ledger_balance WHERE account_id = "
            "(SELECT account_id FROM ledger_account WHERE account_code = $1)",
            user_code,
        )
    assert entry_count == 2
    assert balance == Decimal("20.00")


async def test_ledger_journal_append_failure_blocks_balance_change(pool):
    """Negative — DoD(3) fail-closed. `journal.append`(이벤트=`ledger_journal_
    entry` append)가 실패하면 잔액 갱신은 이 함수까지 도달하지 못한다(§6
    순서: 저널 append 성공 후에야 잔액을 갱신, `post_entry.py` 모듈
    docstring)."""
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    ref = f"fa16-adv-topup-boom:{uuid4().hex}"

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(RuntimeError, match="injected ledger_journal_entry append failure"):
            await post_entry(
                conn,
                _topup_event(event_ref=ref, user_id=user_id, amount=Decimal("10.00")),
                journal=_BoomLedgerJournal(),
                balances=balances,
                audit=audit,
                clock=_clock,
            )

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE event_ref = $1", ref
        )
        balance = await conn.fetchval(
            "SELECT balance FROM ledger_balance WHERE account_id = "
            "(SELECT account_id FROM ledger_account WHERE account_code = $1)",
            user_code,
        )
    assert entry_count == 0
    assert balance == Decimal("0")


async def test_ledger_transaction_rollback_removes_journal_and_balance_together(pool):
    """DoD(2) — 실DB. `post_entry`가 성공적으로 저널·잔액을 쓴 뒤에도
    (아직 커밋 전) 호출자의 트랜잭션이 롤백되면 둘 다 함께 사라진다."""
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    ref = f"fa16-adv-topup-rollback:{uuid4().hex}"

    async with pool.acquire() as conn:
        tx = conn.transaction()
        await tx.start()
        await post_entry(
            conn,
            _topup_event(event_ref=ref, user_id=user_id, amount=Decimal("10.00")),
            journal=journal,
            balances=balances,
            audit=audit,
            clock=_clock,
        )
        await tx.rollback()

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE event_ref = $1", ref
        )
        balance = await conn.fetchval(
            "SELECT balance FROM ledger_balance WHERE account_id = "
            "(SELECT account_id FROM ledger_account WHERE account_code = $1)",
            user_code,
        )
    assert entry_count == 0
    assert balance == Decimal("0")


class _LyingLedgerJournal:
    """`append()`가 실제 `ledger_journal_entry` 행을 쓰지 않으면서 정상처럼
    보이는 `entry_view`만 돌려준다 — task-2061 원 커밋이 소스의 `entry_view
    = await journal.append(conn, event, lines)` 줄을 손으로 지웠다가 원복
    하며 수기로만 확인한 우회("append가 사라져도 뒤 코드가 계속 진행되면?")
    를 소스를 건드리지 않고 자동·영구 재현하는 이중체."""

    async def find_by_idempotency_key(self, conn, key):  # noqa: ANN001, ARG002
        return None

    async def append(self, conn, entry, lines):  # noqa: ANN001, ARG002 -- 테스트 전용
        return JournalEntryView(
            entry_id=uuid4(),
            sequence_no=1,
            event_type=entry.event_type,
            event_ref=entry.event_ref,
            idempotency_key=f"{entry.event_type.value}:{entry.event_ref}",
            lines=lines,
            lines_digest="d" * 64,
            prev_hash=None,
            entry_hash="f" * 64,
            audit_event_id=uuid4(),
            posted_at=_clock(),
            replayed=False,
        )


async def test_ledger_bypassed_journal_write_leaves_balance_changed_with_no_entry(pool):
    """우회재현(자동) — DEPTH task-2724 보강. task-2061 원 커밋은 이 우회를
    소스의 `entry_view = await journal.append(conn, event, lines)` 줄을
    손으로 지웠다가 복원하는 방식으로 한 번 확인하고 커밋 메시지에만
    서술했다(리포에 재현 가능한 형태로 남지 않음). 여기서는
    `_LyingLedgerJournal`로 그 상황("append가 실제 쓰기 없이 그럴듯한 값만
    돌려주면?")을 소스 변경 없이 항상 재현한다 — `post_entry`는
    `entry_view`가 존재하기만 하면 계속 진행해 `ledger_balance`를 갱신하므로
    `ledger_journal_entry`에는 행이 하나도 없는데 잔액은 바뀐다(I-10이
    금지하는 "이벤트 없는 상태 변경")."""
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    ref = f"fa16-adv-topup-lying:{uuid4().hex}"

    async with pool.acquire() as conn, conn.transaction():
        await post_entry(
            conn,
            _topup_event(event_ref=ref, user_id=user_id, amount=Decimal("10.00")),
            journal=_LyingLedgerJournal(),
            balances=balances,
            audit=audit,
            clock=_clock,
        )

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE event_ref = $1", ref
        )
        balance = await conn.fetchval(
            "SELECT balance FROM ledger_balance WHERE account_id = "
            "(SELECT account_id FROM ledger_account WHERE account_code = $1)",
            user_code,
        )
    assert balance == Decimal("10.00"), "이중체가 돌려준 entry_view로 잔액은 갱신됐어야 한다"
    assert entry_count == 0, (
        f"FA-16/I-10 위반 재현: ledger_balance가 10.00 늘었는데 ledger_journal_entry는 "
        f"{entry_count}행 — 이벤트 없는 잔액 변경."
    )


async def test_ledger_post_entry_round_trip_count_stays_bounded(pool):
    """성능단언 — DEPTH task-2724 보강(이 파일에 성능 단언이 전혀 없었다).
    `post_entry()`(저널 append + 잔액 갱신 + 감사, `test_perf_journal.py`는
    `journal.append()` 단독만 잰다 — 그 파일 모듈 docstring) 1회의 순차 DB
    왕복 수 회귀 가드. 절대 ms 대신 왕복 수를 재는 이유는 이 저장소의 CI
    절대지연 게이트 금지 전례(task-920/1029)와 동일하다. 워밍업을 같은
    커넥션에서 먼저 흘려보내 asyncpg의 최초 코덱 탐색 비용을 흡수한다
    (`test_perf_journal.py::_count_append_round_trips`와 동일한 이유)."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)

    warmup_user = uuid4()
    await _create_user_available_account(pool, warmup_user)
    measured_user = uuid4()
    await _create_user_available_account(pool, measured_user)

    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    async with pool.acquire() as conn:
        async with conn.transaction():
            await post_entry(
                conn,
                _topup_event(
                    event_ref=f"perf-warmup:{uuid4().hex}",
                    user_id=warmup_user,
                    amount=Decimal("1.00"),
                ),
                journal=journal,
                balances=balances,
                audit=audit,
                clock=_clock,
            )

        conn.add_query_logger(_log)
        try:
            async with conn.transaction():
                await post_entry(
                    conn,
                    _topup_event(
                        event_ref=f"perf-measured:{uuid4().hex}",
                        user_id=measured_user,
                        amount=Decimal("1.00"),
                    ),
                    journal=journal,
                    balances=balances,
                    audit=audit,
                    clock=_clock,
                )
        finally:
            conn.remove_query_logger(_log)

    print(f"\npost_entry() round trips: {len(queries)} (max={_MAX_POST_ENTRY_ROUND_TRIPS})")
    assert len(queries) <= _MAX_POST_ENTRY_ROUND_TRIPS, (
        f"post_entry() 순차 DB 왕복 수({len(queries)})가 상한"
        f"({_MAX_POST_ENTRY_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
