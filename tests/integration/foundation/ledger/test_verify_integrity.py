"""LC-10 `verify_ledger_integrity` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§6, §9 LC-10.
DoD(task-335): "정상 리포트; 행 변조(superuser로) → 체인 실패 →
write_frozen=true → post_entry 거부"(spec 605행 test 목록과 동일 시나리오).

경로는 기존 LC-8/LC-9 통합테스트와 같은 `tests/integration/foundation/ledger/`
디렉터리를 쓴다 — task 파일의 `tests/foundation/integration/ledger/`는 이
저장소에 존재하지 않는 경로 순서이고, 실제 원장 통합테스트는 전부
`tests/integration/foundation/ledger/`에 모여 그 디렉터리의 `conftest.py`
`pool` 픽스처를 공유한다.

변조는 `ledger_journal_entry`가 WORM(L0-3, `BEFORE UPDATE OR DELETE` 트리거)
이라 트리거를 일시적으로 `DISABLE`해야 한다 — "superuser가 트리거까지
우회해 직접 행을 바꿨다"는 시나리오를 그대로 재현한다(REVOKE만으로는 막지
못하는 이유가 정확히 이것, `src/core/db/append_only.py` 참고). `lines_digest`
컬럼 하나만 건드려 이 엔트리의 체인 검증만 깨뜨리고 `entry_hash`는
그대로 둔다 — 그래야 이후 엔트리들의 `prev_hash` 연결은 안 깨져서 실패
지점(`first_broken_seq`)이 정확히 이 엔트리로 특정된다. 손상 주입부터
`lines_digest` 원복까지를 하나의 try/finally로 감싸 빈틈없이 복원한다
(`test_post_entry.py`의 동결 테스트와 동일 관행) — `ledger_control`은
전체 스위트가 공유하는 전역 상태라, 이 테스트가 도중에 죽어 자체 복원을
못 밟는 경우까지 대비해 `conftest.py`의 `_ledger_control_clean_slate`
autouse fixture가 매 테스트 전후로 무조건 원복하는 전역 안전망 역할을
한다(task-312 QA가 재현한 "이전 실행의 write_frozen 잔류가 재실행을
깨뜨리는" 격리 결함의 근본 수정).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from src.core.observability.metrics_registry import MetricsRegistry
from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import LedgerWriteFrozenError, post_entry
from src.foundation.ledger.application.verify_integrity import verify_ledger_integrity
from src.foundation.ledger.contracts.v1 import AccountType, LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account

_WORM_TRIGGER = "ledger_journal_entry_worm_guard_trg"


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _create_user_available_account(
    pool, user_id: UUID, *, initial_balance: Decimal = Decimal("0")
) -> str:
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
            "INSERT INTO ledger_balance (account_id, balance, allow_negative, last_entry_seq) "
            "VALUES ($1, $2, FALSE, 0)",
            account_id,
            initial_balance,
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


class _Ports:
    def __init__(self, pool):
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


@pytest.fixture
def ports(pool):
    return _Ports(pool)


async def _post_topup(pool, ports, user_id: UUID, amount: Decimal = Decimal("10.00")):
    user_code = await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=user_id, amount=amount)
    async with pool.acquire() as conn, conn.transaction():
        view = await post_entry(
            conn, event, journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=_clock,
        )
    return user_code, view


async def test_verify_ledger_integrity_reports_ok_when_chain_intact(pool, ports):
    await _post_topup(pool, ports, uuid4())

    report = await verify_ledger_integrity(
        journal=ports.journal, balances=ports.balances, audit=ports.audit,
        pool=pool, registry=MetricsRegistry(),
    )

    # `report.drifts`는 여기서 단언하지 않는다 — 이 디렉터리의 다른 리프
    # 테스트(`test_postgres_journal_repository.py`)가 `journal.append`만
    # 직접 호출해 `PLATFORM:CASH_CLEARING`에 영구적이고 정상적인
    # fold-vs-balance 드리프트를 남기기 때문(모듈 docstring 참고). 여기서
    # 확인할 안전 불변은 "체인·시산표가 멀쩡하면 절대 동결되지 않는다"는
    # 것뿐이다 — 드리프트만으로는 동결되지 않는다.
    assert report.chain_ok is True
    assert report.zero_sum_ok is True
    assert report.first_broken_seq is None

    async with pool.acquire() as conn:
        frozen = await conn.fetchval("SELECT write_frozen FROM ledger_control WHERE id = 1")
    assert frozen is False


async def test_verify_ledger_integrity_freezes_and_blocks_posting_on_tamper(pool, ports):
    _, view = await _post_topup(pool, ports, uuid4())
    entry_id = view.entry_id

    async with pool.acquire() as conn:
        original_digest = await conn.fetchval(
            "SELECT lines_digest FROM ledger_journal_entry WHERE entry_id = $1", entry_id
        )

    async def _set_digest(new_digest: str) -> None:
        # WORM 트리거를 우회해 값을 바꾸는 유일한 목적이 "변조 후 원상복구"라,
        # 손상 주입과 복원을 같은 헬퍼로 묶는다 — 복원도 결국 같은 종류의
        # 트리거-우회 UPDATE라 로직을 둘로 나눌 이유가 없고, 이렇게 하나로
        # 합쳐야 아래 try/finally 안에서 손상 주입 시점부터 복원까지 빈틈없이
        # 감싸진다(예전 버전은 손상 주입이 try 진입 *전*에 일어나 그 사이에
        # 예외가 나면 finally가 아예 실행되지 않는 구간이 있었다).
        async with pool.acquire() as conn:
            await conn.execute(f"ALTER TABLE ledger_journal_entry DISABLE TRIGGER {_WORM_TRIGGER}")
            try:
                await conn.execute(
                    "UPDATE ledger_journal_entry SET lines_digest = $1 WHERE entry_id = $2",
                    new_digest,
                    entry_id,
                )
            finally:
                await conn.execute(
                    f"ALTER TABLE ledger_journal_entry ENABLE TRIGGER {_WORM_TRIGGER}"
                )

    try:
        await _set_digest("tampered" * 8)

        report = await verify_ledger_integrity(
            journal=ports.journal, balances=ports.balances, audit=ports.audit,
            pool=pool, registry=MetricsRegistry(),
        )
        assert report.chain_ok is False
        assert report.first_broken_seq == view.sequence_no

        async with pool.acquire() as conn:
            frozen = await conn.fetchval("SELECT write_frozen FROM ledger_control WHERE id = 1")
            reason = await conn.fetchval("SELECT frozen_reason FROM ledger_control WHERE id = 1")
        assert frozen is True
        assert reason is not None and "chain_broken" in reason

        rejected_event = _topup_event(event_ref=f"topup:{uuid4().hex}", user_id=uuid4())
        with pytest.raises(LedgerWriteFrozenError):
            async with pool.acquire() as conn, conn.transaction():
                await post_entry(
                    conn, rejected_event, journal=ports.journal, balances=ports.balances,
                    audit=ports.audit, clock=_clock,
                )
    finally:
        # `ledger_control.write_frozen` 원복은 `conftest.py`의
        # `_ledger_control_clean_slate` autouse fixture teardown이 전역
        # 안전망으로 보장한다(이 테스트가 여기서 죽어도 커버) — 여기서는
        # 이 테스트가 직접 손상시킨 `lines_digest`만 원복한다.
        await _set_digest(original_digest)
