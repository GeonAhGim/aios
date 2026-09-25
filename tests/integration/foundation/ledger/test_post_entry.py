"""LC-9 `post_entry` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§6, §9 LC-9.
DoD(task-330): "감사 실패 주입 시 분개·라인·잔액 전부 롤백", "write_frozen=true면
거부", "REPLAY 무중복", "DIGEST_MISMATCH 거부+DENIED 감사" 필수.

DEEPEN task-2978: DEPTH 감사(task-2723, docs/audit/DEPTH_LA_LB_LC.md#701)가
원 task-701(post_entry의 journal.append replayed 무시 이중적용 결함 수정,
e894792)을 D1로 판정했다 — 부족했던 3가지: (1) 수치 성능 단언 없음,
(2) race 테스트(`test_post_entry_race_after_precheck_applies_balance_once`)가
두 호출을 순차 `await`해 레이스 창을 문자 그대로 재현했을 뿐 진짜
`asyncio.gather` 다중 동시호출이 아님, (3) 적대적/위조 시도 테스트 없음
(`_assert_extra_safe`/`LedgerEventExtraRejectedError`가 이 파일에서 한
번도 실행되지 않았다). 아래에 세 부류를 추가해 D1->D3로 올린다. 프로덕션
코드(`post_entry.py`)는 무수정이다.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import (
    LedgerEventExtraRejectedError,
    LedgerWriteFrozenError,
    post_entry,
)
from src.foundation.ledger.contracts.v1 import AccountType, LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account
from src.foundation.ledger.domain.idempotency import IdempotencyDigestMismatchError

_MAX_POST_ENTRY_ROUND_TRIPS = 17

_PLATFORM_CASH_CLEARING = "PLATFORM:CASH_CLEARING"


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _create_user_available_account(pool, user_id: UUID) -> str:
    """`USER:{user_id}:AVAILABLE` 계정을 만든다 — 시드 계정이 아니라 테스트마다
    새 `user_id`로 격리한다(공유 PLATFORM 계정과 달리 잔액 오염 걱정이 없다).
    잔액은 항상 0으로 시작한다(FA-15a/esc-2115: `ledger_balance`에 잔액을
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


class _RealPorts:
    def __init__(self, pool):
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs):
        raise RuntimeError("injected audit failure")


class _NoPrecheckJournal:
    """`find_by_idempotency_key` 사전체크가 항상 "없음"이라고 답하는 래퍼 —
    동시 재시도 두 건이 락 없는 사전체크를 모두 통과한 뒤 `append`(advisory
    lock)에서 처음 직렬화되는 레이스 창을, 순차 호출만으로 재현한다(LC-9
    회귀). `append` 자체는 실제 저장소에 그대로 위임하므로 두 번째 호출은
    `append` 내부 판정에 의해 `replayed=True`를 받는다."""

    def __init__(self, real: PostgresJournalRepository) -> None:
        self._real = real

    async def append(self, conn, entry, lines):
        return await self._real.append(conn, entry, lines)

    async def find_by_idempotency_key(self, conn, key):
        return None

    async def list_since(self, conn, seq):
        return await self._real.list_since(conn, seq)

    async def last(self, conn):
        return await self._real.last(conn)


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


async def test_post_entry_persists_journal_and_updates_balances(pool, ports):
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=user_id, amount=Decimal("10.00"))

    async with pool.acquire() as conn, conn.transaction():
        view = await post_entry(
            conn,
            event,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )

    assert view.replayed is False
    assert {line.account_code for line in view.lines} == {_PLATFORM_CASH_CLEARING, user_code}

    async with pool.acquire() as conn, conn.transaction():
        balances = await ports.balances.get_for_update(conn, [user_code])
    assert balances[user_code].balance == Decimal("10.00")


async def test_post_entry_replays_without_duplicate_journal_or_audit(pool, ports):
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    event_ref = f"topup:{uuid4().hex}"
    event = _topup_event(event_ref=event_ref, user_id=user_id, amount=Decimal("10.00"))

    async with pool.acquire() as conn, conn.transaction():
        first = await post_entry(
            conn,
            event,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )
    async with pool.acquire() as conn, conn.transaction():
        second = await post_entry(
            conn,
            event,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )

    assert second.replayed is True
    assert second.entry_id == first.entry_id

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            first.idempotency_key,
        )
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1",
            first.entry_id,
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            user_code,
        )
    assert entry_count == 1
    # 2 = journal.append의 FK 링크용 감사(LC-8b) + post_entry 자신의 커맨드 단위
    # 감사(LC-9) — 둘 다 첫 호출에서만 생기고, replay는 어느 쪽도 추가하지 않는다.
    assert audit_count == 2
    assert balance == Decimal("10.00")


async def test_post_entry_race_after_precheck_applies_balance_once(pool, ports):
    """LC-9 결함 수정 회귀: 사전체크(`find_by_idempotency_key`)를 우회해 두 요청
    모두 `append`에 진입시킨다. 잔액 적용·SUCCESS 감사 스킵 여부의 유일한
    근거는 `journal.append` 반환값의 `replayed`여야 한다 — 그렇지 않으면
    (수정 전처럼) 두 번째 요청도 `balances.apply`를 실행해 잔액이 두 번
    적용된다."""
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    event_ref = f"topup:{uuid4().hex}"
    event = _topup_event(event_ref=event_ref, user_id=user_id, amount=Decimal("10.00"))
    racy_journal = _NoPrecheckJournal(ports.journal)

    async with pool.acquire() as conn, conn.transaction():
        first = await post_entry(
            conn,
            event,
            journal=racy_journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )
    async with pool.acquire() as conn, conn.transaction():
        second = await post_entry(
            conn,
            event,
            journal=racy_journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )

    assert first.replayed is False
    assert second.replayed is True
    assert second.entry_id == first.entry_id

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            first.idempotency_key,
        )
        line_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_posting_line WHERE entry_id = $1",
            first.entry_id,
        )
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1",
            first.entry_id,
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            user_code,
        )
    assert entry_count == 1
    assert line_count == len(first.lines)
    # 1 = journal.append의 FK 링크용 감사(LC-8b) + 1 = post_entry 자신의
    # 커맨드 단위 SUCCESS 감사(LC-9, 첫 호출에서만) — replay된 두 번째
    # 호출은 어느 쪽도 추가하지 않는다.
    assert audit_count == 2
    assert balance == Decimal("10.00")


async def test_post_entry_digest_mismatch_denies_and_emits_denied_audit(pool, ports):
    user_id = uuid4()
    await _create_user_available_account(pool, user_id)
    event_ref = f"topup:{uuid4().hex}"
    first_event = _topup_event(event_ref=event_ref, user_id=user_id, amount=Decimal("10.00"))

    async with pool.acquire() as conn, conn.transaction():
        first = await post_entry(
            conn,
            first_event,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )

    # DENIED 감사 이벤트는 post_entry가 여는 게 아니라 호출자의 트랜잭션과
    # 함께 커밋된다 — 예외를 트랜잭션 경계 *안에서* 잡아야 살아남는다(모듈
    # docstring 참고). 경계 밖에서 raises하면 그 커밋 전체가 롤백돼 DENIED
    # 행도 함께 사라진다.
    mismatched_event = _topup_event(event_ref=event_ref, user_id=user_id, amount=Decimal("99.00"))
    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(IdempotencyDigestMismatchError):
            await post_entry(
                conn,
                mismatched_event,
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=_clock,
            )

    async with pool.acquire() as conn:
        outcome = await conn.fetchval(
            "SELECT outcome FROM foundation_audit_event "
            "WHERE aggregate_id = $1 AND outcome = 'DENIED'",
            first.entry_id,
        )
    assert outcome == "DENIED"


async def test_post_entry_rejects_when_ledger_frozen(pool, ports):
    user_id = uuid4()
    await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=user_id)

    async with pool.acquire() as conn:
        await conn.execute("UPDATE ledger_control SET write_frozen = TRUE WHERE id = 1")
    try:
        with pytest.raises(LedgerWriteFrozenError):
            async with pool.acquire() as conn, conn.transaction():
                await post_entry(
                    conn,
                    event,
                    journal=ports.journal,
                    balances=ports.balances,
                    audit=ports.audit,
                    clock=_clock,
                )
    finally:
        async with pool.acquire() as conn:
            await conn.execute("UPDATE ledger_control SET write_frozen = FALSE WHERE id = 1")

    async with pool.acquire() as conn:
        found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"{event.event_type.value}:{event.event_ref}",
        )
    assert found is None


async def test_post_entry_audit_failure_rolls_back_journal_lines_and_balance(pool, ports):
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=user_id, amount=Decimal("10.00"))

    with pytest.raises(RuntimeError):
        async with pool.acquire() as conn, conn.transaction():
            await post_entry(
                conn,
                event,
                journal=ports.journal,
                balances=ports.balances,
                audit=_BoomAuditAppender(),
                clock=_clock,
            )

    async with pool.acquire() as conn:
        entry_found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"{event.event_type.value}:{event.event_ref}",
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            user_code,
        )
    assert entry_found is None
    assert balance == Decimal("0")


# --- DEEPEN task-2978 — 진짜 asyncio.gather 다중 동시호출 증명(D3). ---


async def test_post_entry_concurrent_gather_retries_apply_balance_exactly_once(pool, ports):
    """`test_post_entry_race_after_precheck_applies_balance_once`는 두 호출을
    순차 `await`해 레이스 창을 문자 그대로 재현했을 뿐 진짜 동시성은
    아니었다(DEPTH 감사 task-2723 지적). 여기서는 서로 다른 실제 커넥션
    두 개로 `asyncio.gather`를 통해 같은 사건(같은 `event_ref`·같은 내용)을
    진짜로 동시에 재시도시켜, `ledger_balance` 행 `FOR UPDATE` 잠금과
    `journal.append`의 advisory lock(`pg_advisory_xact_lock`)이 실제
    PostgreSQL 동시성 하에서도 정확히 한 번만 잔액을 적용함을 증명한다
    (`_NoPrecheckJournal`로 락 없는 멱등 사전체크를 우회해, 둘 다 그
    체크를 통과한 뒤 `FOR UPDATE`/advisory lock에서 처음 직렬화되는
    LC-9 회귀 창을 연다)."""
    user_id = uuid4()
    user_code = await _create_user_available_account(pool, user_id)
    event_ref = f"topup:{uuid4().hex}"
    racy_journal = _NoPrecheckJournal(ports.journal)

    async def _attempt() -> object:
        event = _topup_event(event_ref=event_ref, user_id=user_id, amount=Decimal("10.00"))
        async with pool.acquire() as conn, conn.transaction():
            return await post_entry(
                conn,
                event,
                journal=racy_journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=_clock,
            )

    results = await asyncio.gather(_attempt(), _attempt(), return_exceptions=True)

    for result in results:
        if isinstance(result, BaseException):
            raise result

    replayed_flags = sorted(result.replayed for result in results)
    assert replayed_flags == [False, True], f"정확히 하나만 replayed=False여야 합니다: {results}"
    assert results[0].entry_id == results[1].entry_id

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE idempotency_key = $1",
            results[0].idempotency_key,
        )
        balance = await conn.fetchval(
            "SELECT lb.balance FROM ledger_balance lb JOIN ledger_account la "
            "ON la.account_id = lb.account_id WHERE la.account_code = $1",
            user_code,
        )
    assert entry_count == 1
    assert balance == Decimal("10.00")


# --- DEEPEN task-2978 — 적대적/위조 시도 테스트. `_assert_extra_safe`/
# `LedgerEventExtraRejectedError`(LC-17 결함 A)가 이 파일에서 한 번도
# 실행되지 않았다. ---


async def test_post_entry_denies_forged_secret_like_extra_key_and_emits_denied_audit(pool, ports):
    """공격자가 `event.extra`에 secret류로 보이는 키(`api_key`)를 실어
    원문을 감사 payload에 영구 기록시키려는 시도. `assert_safe_payload`가
    저장 이전에 거부해야 하고(`UnsafePayloadError` -> `LedgerEventExtraRejectedError`),
    거부 자체는 DENIED 감사로 남되 원문 값은 어디에도 저장되지 않는다."""
    user_id = uuid4()
    await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=user_id)
    forged_event = event.model_copy(update={"extra": {"api_key": "sk-live-forged-secret"}})

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(LedgerEventExtraRejectedError):
            await post_entry(
                conn,
                forged_event,
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=_clock,
            )

    async with pool.acquire() as conn:
        denied_outcome = await conn.fetchval(
            "SELECT outcome FROM foundation_audit_event "
            "WHERE aggregate_id = $1 AND outcome = 'DENIED'",
            forged_event.trace_id,
        )
        payload_text = await conn.fetchval(
            "SELECT payload::text FROM foundation_audit_event "
            "WHERE aggregate_id = $1 AND outcome = 'DENIED'",
            forged_event.trace_id,
        )
        entry_found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"{forged_event.event_type.value}:{forged_event.event_ref}",
        )
    assert denied_outcome == "DENIED"
    assert "sk-live-forged-secret" not in payload_text
    assert entry_found is None


async def test_post_entry_denies_forged_non_whitelisted_extra_key_and_emits_denied_audit(
    pool, ports
):
    """키 이름 자체는 secret 패턴에 걸리지 않지만(`assert_safe_payload` 통과)
    `TOPUP_CONFIRMED`의 `EXTRA_ALLOWED_KEYS` 화이트리스트(빈 집합)에는 없는
    키를 실어, 이 사건 타입이 실제로 읽지 않는 필드로 하위 로직을 속이려는
    위조 시도. 화이트리스트 검사가 거부해야 한다."""
    user_id = uuid4()
    await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=user_id)
    forged_event = event.model_copy(update={"extra": {"promo_code": "FREE100"}})

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(LedgerEventExtraRejectedError):
            await post_entry(
                conn,
                forged_event,
                journal=ports.journal,
                balances=ports.balances,
                audit=ports.audit,
                clock=_clock,
            )

    async with pool.acquire() as conn:
        denied_outcome = await conn.fetchval(
            "SELECT outcome FROM foundation_audit_event "
            "WHERE aggregate_id = $1 AND outcome = 'DENIED'",
            forged_event.trace_id,
        )
        entry_found = await conn.fetchval(
            "SELECT 1 FROM ledger_journal_entry WHERE idempotency_key = $1",
            f"{forged_event.event_type.value}:{forged_event.event_ref}",
        )
    assert denied_outcome == "DENIED"
    assert entry_found is None


# --- DEEPEN task-2978 — 수치 성능 단언. test_perf_journal.py가 이미 겪은
# 대로(task-920/1029, esc-ci-d5723ce4366d 종결), 이 CI 환경의 절대 지연(ms)
# 단언은 인프라 변동성에 좌우돼 상시 적색을 유발한 전례가 있어 여기서도
# 같은 처방(환경 독립적인 순차 DB 왕복 수 상한)을 쓴다. ---


async def _count_post_entry_round_trips(pool, ports) -> int:
    """`post_entry` 1회(topup, 2-라인: `USER:*:AVAILABLE`/`PLATFORM:
    CASH_CLEARING`)가 쓰는 순차 DB 왕복 수. 측정 전 별도 사용자로 워밍업
    호출을 먼저 흘려보내 asyncpg의 커넥션별 1회성 코덱 조회 오버헤드를
    흡수시킨다(`test_perf_journal.py`의 `_count_append_round_trips`와 동일
    이유)."""
    warmup_user_id = uuid4()
    await _create_user_available_account(pool, warmup_user_id)
    warmup_event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=warmup_user_id)
    async with pool.acquire() as conn, conn.transaction():
        await post_entry(
            conn,
            warmup_event,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
        )

    counted_user_id = uuid4()
    await _create_user_available_account(pool, counted_user_id)
    event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=counted_user_id)

    queries: list[str] = []

    def _log(record: object) -> None:
        queries.append(getattr(record, "query", ""))

    async with pool.acquire() as conn:
        async with conn.transaction():
            conn.add_query_logger(_log)
            try:
                await post_entry(
                    conn,
                    event,
                    journal=ports.journal,
                    balances=ports.balances,
                    audit=ports.audit,
                    clock=_clock,
                )
            finally:
                conn.remove_query_logger(_log)

    return len(queries)


@pytest.mark.perf
async def test_post_entry_round_trip_count_under_budget(pool, ports):
    """`post_entry` 전체 경로(동결확인 + `extra` 안전성 확인 + 멱등 사전조회 +
    `FOR UPDATE` 잠금 + `journal.append`의 advisory lock/CTE/감사체인/저널
    INSERT/분개행 멀티행 INSERT + 잔액 반영 2회(계정 2개) + SUCCESS 감사)가
    topup 2-라인 흐름에서 쓰는 순차 DB 왕복 수(실측 17회)의 구조 회귀 가드.
    왕복 수가 늘면(예: 사전체크·잠금 순서가 바뀌어 왕복이 중복되는 회귀) 이
    게이트가 적색이 된다. 절대 지연(ms) 대신 왕복 수를 게이트로 쓰는 이유는
    `test_perf_journal.py`가 이미 겪은 CI 인프라 변동성 상시 적색 전례
    (task-920/1029, esc-ci-d5723ce4366d) 때문이다."""
    round_trip_count = await _count_post_entry_round_trips(pool, ports)

    print(
        f"\npost_entry round trips (topup, 2 lines)={round_trip_count} "
        f"(max={_MAX_POST_ENTRY_ROUND_TRIPS})"
    )

    assert round_trip_count <= _MAX_POST_ENTRY_ROUND_TRIPS, (
        f"post_entry 순차 DB 왕복 수({round_trip_count})가 상한"
        f"({_MAX_POST_ENTRY_ROUND_TRIPS})을 초과했습니다 — 왕복 수 회귀입니다."
    )
