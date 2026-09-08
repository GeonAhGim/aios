"""FA-12 `application/{ibor_view,abor_snapshot}.py` 통합테스트 -- 실 DB
(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-12
(task-2059 decision, PM 2026-09-08).

DoD: IBOR은 `posted_at` cutoff로 필터링한 재계산이 결정론적이어야 하고,
ABOR 마감은 (1) 그 재계산 값을 그대로 영속화하고 (2) 같은
`(fund_id, as_of_date)` 재마감을 거부하며(adversarial) (3) 마감 이후 새
posting이 생기면 `is_correction_pending`이 이를 표시해야 한다.

`ledger_journal_entry`/`ledger_posting_line`은 WORM이라 `posted_at`을
호출자가 임의로 고를 수 있는 공개 API가 없다(실제 코드는 항상
`datetime.now()`) -- 이 파일의 `_insert_dated_entry`가 실제
`postgres_journal_repository.append`와 동일한 삽입 형태를 감사 이벤트부터
직접 재현해, "과거 cutoff"를 결정론적으로 통제한다.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import cast
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.entities.adapters.postgres_repository import PostgresEntityRepository
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.domain.models import Classification, Outcome
from src.foundation.evidence.domain.rules import compute_payload_hash
from src.foundation.ledger.application import abor_snapshot, ibor_view
from src.foundation.ledger.application.abor_snapshot import (
    AlreadyClosedError,
    SnapshotNotFoundError,
)
from src.foundation.ledger.application.ibor_view import NaiveCutoffError
from src.foundation.ledger.contracts.v1 import (
    AccountType,
    LedgerEventType,
    PostingLine,
    Side,
    UserSub,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account
from src.foundation.ledger.domain.hash_chain import entry_hash as compute_entry_hash
from src.foundation.ledger.domain.hash_chain import lines_digest
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.entities.conftest import build_hierarchy


async def _account_id(pool: asyncpg.Pool, account_code: str) -> UUID:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT account_id FROM ledger_account WHERE account_code = $1", account_code
        )
    assert value is not None
    return value


async def _insert_dated_entry(
    pool: asyncpg.Pool,
    *,
    fund_id: UUID,
    portfolio_id: UUID,
    posted_at: datetime,
    lines: list[PostingLine],
    account_ids: dict[str, UUID],
) -> UUID:
    """`ledger_journal_entry`/`ledger_posting_line`에 `posted_at`을 직접
    지정해 삽입한다 -- 실제 `PostgresJournalRepository.append`와 같은 순서
    (advisory lock -> 감사 이벤트 -> 저널 -> 포스팅 행)를 그대로 밟되,
    `posted_at`만 테스트가 통제한다. 잔액 트리거(`ledger_entry_balanced_trg`,
    DEFERRABLE)가 커밋 시점에 균형을 검증하므로 `lines`는 항상 균형이어야
    한다.

    FA-15a(esc-2115) 정리 중 발견: `prev_hash`/`entry_hash`를 placeholder
    문자열로 심으면 `hash_chain.verify_chain`이 재계산한 값과 영원히
    어긋난다 -- `ledger_journal_entry`는 WORM이라 한 번 이렇게 심으면 이
    워커의 테스트 DB가 영구히 오염된다(`test_migration_fa4_worm_no_backfill.
    py` 모듈 docstring이 경고하는 바로 그 사고). 실제 체인을 그대로
    재현한다: 직전 sequence_no의 `entry_hash`를 `prev_hash`로 읽고,
    `hash_chain.entry_hash`로 진짜 값을 계산한다."""
    audit_repo = PostgresAuditEventRepository(pool)
    entry_id = uuid4()
    event_ref = f"test-fa12:{entry_id}"
    digest = lines_digest(lines)
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('ledger_journal'))")
        next_seq = await conn.fetchval(
            "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM ledger_journal_entry"
        )
        prev_hash = await conn.fetchval(
            "SELECT entry_hash FROM ledger_journal_entry WHERE sequence_no = $1", next_seq - 1
        )
        new_hash = compute_entry_hash(
            prev_hash, next_seq, LedgerEventType.MANUAL_ADJUSTMENT, event_ref, digest, posted_at
        )
        audit_event = await audit_repo.append_event_in(
            conn,
            tenant_id=None,
            aggregate_type="ledger_journal_entry",
            aggregate_id=entry_id,
            aggregate_revision=None,
            action=LedgerEventType.MANUAL_ADJUSTMENT.value,
            outcome=Outcome.SUCCESS,
            actor_subject_id=None,
            trace_id=uuid4(),
            payload_hash=compute_payload_hash({"test": "fa12_dated_entry"}),
            payload={"test": "fa12_dated_entry"},
            classification=Classification.INTERNAL,
        )
        await conn.execute(
            "INSERT INTO ledger_journal_entry "
            "(entry_id, sequence_no, event_type, event_ref, idempotency_key, "
            " lines_digest, prev_hash, entry_hash, audit_event_id, posted_at, "
            " fund_id, portfolio_id) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)",
            entry_id,
            next_seq,
            LedgerEventType.MANUAL_ADJUSTMENT.value,
            event_ref,
            f"test-fa12-idem:{entry_id}",
            digest,
            prev_hash,
            new_hash,
            audit_event.id,
            posted_at,
            fund_id,
            portfolio_id,
        )
        for line in lines:
            await conn.execute(
                "INSERT INTO ledger_posting_line "
                "(entry_id, line_no, account_id, side, amount, currency, "
                " fund_id, portfolio_id) "
                "VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                entry_id,
                line.line_no,
                account_ids[line.account_code],
                line.side.value,
                line.amount,
                line.currency.value,
                fund_id,
                portfolio_id,
            )
    return entry_id


def _balanced_pair(
    debit_code: str, credit_code: str, amount: Decimal
) -> list[PostingLine]:
    return [
        PostingLine(
            line_no=1, account_code=debit_code, side=Side.DEBIT,
            amount=amount, currency=Currency.KRW,
        ),
        PostingLine(
            line_no=2, account_code=credit_code, side=Side.CREDIT,
            amount=amount, currency=Currency.KRW,
        ),
    ]


@pytest.fixture
async def fund_id(pool: asyncpg.Pool) -> UUID:
    tenant_id = await create_test_tenant(pool)
    repo = PostgresEntityRepository(pool)
    hierarchy = await build_hierarchy(pool, repo, tenant_id=tenant_id)
    return hierarchy.fund.fund_id


@pytest.fixture
async def portfolio_id(pool: asyncpg.Pool, fund_id: UUID) -> UUID:
    async with pool.acquire() as conn:
        value = await conn.fetchval(
            "SELECT portfolio_id FROM portfolio WHERE fund_id = $1", fund_id
        )
    assert value is not None
    return value


async def _create_user_ledger_account(
    pool: asyncpg.Pool, sub: UserSub, *, allow_negative: bool
) -> str:
    """FA-12 IBOR/ABOR 전용 계정 생성 — `PLATFORM:TEST_*`(다른 디렉터리의
    `create_ledger_account`)는 `chart_of_accounts`가 모르는 이름이라,
    `_insert_dated_entry`가 남긴 실제 `ledger_posting_line`을
    `scripts/replay_verify.py`의 원장 프로젝션(`account_type()` 필요)이
    되짚을 때 `InvalidAccountCodeError`로 죽는다(esc-2115와 같은 계열의
    결함 — FA-15a 정리 과정에서 발견). `USER:*` 계정코드는 항상 인식되므로
    이 값을 쓴다. `allow_negative`는 `ledger_balance` 행의 컬럼값이라
    `chart_of_accounts.allows_negative()`(코드 레벨, RECEIVABLE만 True)와
    무관하게 이 테스트가 원하는 대로 설정할 수 있다 — 이 파일은 `post_entry`/
    `balance_rules.apply`를 거치지 않고 원장 행을 직접 읽고 쓰기 때문이다."""
    code = user_account(uuid4(), sub)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, $2, $3, $4) RETURNING account_id",
            code,
            (AccountType.ASSET if sub is UserSub.RECEIVABLE else AccountType.LIABILITY).value,
            Currency.KRW.value,
            allow_negative,
        )
        await conn.execute(
            "INSERT INTO ledger_balance (account_id, allow_negative, last_entry_seq) "
            "VALUES ($1, $2, 0)",
            account_id,
            allow_negative,
        )
    return code


@pytest.fixture
async def accounts(pool: asyncpg.Pool) -> tuple[str, str]:
    debit_code = await _create_user_ledger_account(pool, UserSub.RECEIVABLE, allow_negative=True)
    credit_code = await _create_user_ledger_account(pool, UserSub.AVAILABLE, allow_negative=True)
    return debit_code, credit_code


def _t(base: datetime, minutes: int) -> datetime:
    return base + timedelta(minutes=minutes)


async def test_ibor_view_recomputes_deterministically_as_of_cutoff(
    pool, fund_id, portfolio_id, accounts
):
    debit_code, credit_code = accounts
    account_ids = {
        debit_code: await _account_id(pool, debit_code),
        credit_code: await _account_id(pool, credit_code),
    }
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    t1, t2 = _t(base, 0), _t(base, 10)

    await _insert_dated_entry(
        pool, fund_id=fund_id, portfolio_id=portfolio_id, posted_at=t1,
        lines=_balanced_pair(debit_code, credit_code, Decimal("100.00")),
        account_ids=account_ids,
    )
    await _insert_dated_entry(
        pool, fund_id=fund_id, portfolio_id=portfolio_id, posted_at=t2,
        lines=_balanced_pair(debit_code, credit_code, Decimal("50.00")),
        account_ids=account_ids,
    )

    as_of_t1 = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=t1)
    assert as_of_t1.balances[debit_code] == Decimal("100.00")
    assert as_of_t1.balances[credit_code] == Decimal("-100.00")

    as_of_t2 = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=t2)
    assert as_of_t2.balances[debit_code] == Decimal("150.00")
    assert as_of_t2.balances[credit_code] == Decimal("-150.00")

    # 재계산은 결정론적이다 -- 같은 cutoff는 언제 다시 물어도 같은 값.
    replay = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=t1)
    assert replay.balances == as_of_t1.balances


async def test_ibor_view_rejects_naive_cutoff():
    with pytest.raises(NaiveCutoffError):
        await ibor_view.compute_ibor_view(
            pool=cast(asyncpg.Pool, None), fund_id=uuid4(), cutoff=datetime(2026, 1, 1)
        )


async def test_close_period_persists_the_recomputed_ibor_value(
    pool, fund_id, portfolio_id, accounts
):
    debit_code, credit_code = accounts
    account_ids = {
        debit_code: await _account_id(pool, debit_code),
        credit_code: await _account_id(pool, credit_code),
    }
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    await _insert_dated_entry(
        pool, fund_id=fund_id, portfolio_id=portfolio_id, posted_at=cutoff,
        lines=_balanced_pair(debit_code, credit_code, Decimal("42.00")),
        account_ids=account_ids,
    )

    as_of_date = date(2026, 9, 1)
    closed = await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
    )

    expected = await ibor_view.compute_ibor_view(pool, fund_id=fund_id, cutoff=cutoff)
    assert closed.balances == expected.balances
    assert closed.closing_recorded_at == cutoff

    reread = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert reread is not None
    assert reread.balances == closed.balances
    assert reread.snapshot_hash == closed.snapshot_hash


async def test_close_period_rejects_reclose_of_same_as_of_date(
    pool, fund_id, portfolio_id, accounts
):
    """DoD(3) adversarial: 같은 (fund_id, as_of_date) 2회 마감은 두 번째가
    거부되어야 한다 -- UNIQUE 위반이 그대로 새지 않고 도메인 오류로 표면화."""
    debit_code, credit_code = accounts
    account_ids = {
        debit_code: await _account_id(pool, debit_code),
        credit_code: await _account_id(pool, credit_code),
    }
    cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
    await _insert_dated_entry(
        pool, fund_id=fund_id, portfolio_id=portfolio_id, posted_at=cutoff,
        lines=_balanced_pair(debit_code, credit_code, Decimal("10.00")),
        account_ids=account_ids,
    )
    as_of_date = date(2026, 9, 2)

    await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
    )

    with pytest.raises(AlreadyClosedError) as exc_info:
        await abor_snapshot.close_period(
            pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=cutoff
        )
    assert exc_info.value.fund_id == fund_id
    assert exc_info.value.as_of_date == as_of_date

    # 재마감 거부가 스냅샷 값을 건드리지 않았는지 확인.
    still_there = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert still_there is not None
    assert still_there.balances[debit_code] == Decimal("10.00")


async def test_correction_pending_true_after_closing_when_new_posting_recorded(
    pool, fund_id, portfolio_id, accounts
):
    debit_code, credit_code = accounts
    account_ids = {
        debit_code: await _account_id(pool, debit_code),
        credit_code: await _account_id(pool, credit_code),
    }
    base = datetime.now(timezone.utc) - timedelta(hours=1)
    t1, t2 = _t(base, 0), _t(base, 30)

    await _insert_dated_entry(
        pool, fund_id=fund_id, portfolio_id=portfolio_id, posted_at=t1,
        lines=_balanced_pair(debit_code, credit_code, Decimal("5.00")),
        account_ids=account_ids,
    )

    as_of_date = date(2026, 9, 3)
    await abor_snapshot.close_period(
        pool, fund_id=fund_id, as_of_date=as_of_date, closing_recorded_at=t1
    )

    not_yet = await abor_snapshot.is_correction_pending(
        pool, fund_id=fund_id, as_of_date=as_of_date
    )
    assert not_yet is False

    # 마감 이후(t1) 새로 기록된 posting -- 마감 스냅샷 값 자체는 바꾸지
    # 않지만 correction_pending은 True로 표시되어야 한다.
    await _insert_dated_entry(
        pool, fund_id=fund_id, portfolio_id=portfolio_id, posted_at=t2,
        lines=_balanced_pair(debit_code, credit_code, Decimal("1.00")),
        account_ids=account_ids,
    )

    now_pending = await abor_snapshot.is_correction_pending(
        pool, fund_id=fund_id, as_of_date=as_of_date
    )
    assert now_pending is True

    unchanged = await abor_snapshot.get_snapshot(pool, fund_id=fund_id, as_of_date=as_of_date)
    assert unchanged is not None
    assert unchanged.balances[debit_code] == Decimal("5.00")  # 마감 값은 불변


async def test_correction_pending_raises_when_period_never_closed(pool, fund_id):
    with pytest.raises(SnapshotNotFoundError):
        await abor_snapshot.is_correction_pending(
            pool, fund_id=fund_id, as_of_date=date(2026, 9, 4)
        )
