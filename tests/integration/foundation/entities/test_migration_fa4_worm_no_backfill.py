"""FA-4(963d5f3cfb1b) 마이그레이션 — WORM 테이블(백필 없음) + 원장 대차 불변 회귀.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-4 DoD.
decision(task-1794): `pos_journal`·`ledger_journal_entry`·`ledger_posting_line`은
이미 append-only WORM 트리거가 걸려 있어(`4a1d0c0de004`/`4a1d0c0de005`) 물리적으로
재기록 불가하다 — 컬럼/FK/인덱스만 추가하고 백필은 하지 않는다(영구 NULL).
`pos_journal`은 FA-3 `fills` 테스트와 동일 패턴으로 "부트스트랩된 tenant라도
백필되지 않음"을 증명한다(eligible한 데이터가 있어도 WORM이라 스킵됨을
분명히 하기 위해). `ledger_journal_entry`/`ledger_posting_line`은 tenant_id
컬럼 자체가 없어 이 마이그레이션의 백필 대상 목록에 애초에 들어가지 않는다
— 컬럼 추가 후에도 NULL로 남는지만 확인한다.

`ledger_journal_entry`/`ledger_posting_line` 행은 `post_entry`(LC-9)
실제 애플리케이션 경로로만 만든다 — 손으로 만든 placeholder
digest/entry_hash로 직접 INSERT하면 `ledger_entry_balanced_trg`는 통과해도
`hash_chain.verify_chain`(LC-3, `verify_ledger_integrity`가 TEST_DATABASE_URL
전체 저널에 대해 주기적으로 재계산)이 깨진 체인으로 오판할 위험이 있고,
WORM이라 이 행은 절대 지울 수 없다 — 한 번 잘못 심으면 이 워커의
테스트 DB가 영구히 오염된다(실제로 이 파일의 이전 버전이 그렇게 오염시켜
`ledger_balance`가 없는 계정을 만들었고, 수동 복구가 필요했다). `post_entry`는
해시체인·잔액을 모두 올바르게 채우므로 이 위험이 없다.

마지막 테스트는 이 마이그레이션이 `ledger_posting_line`에 붙은 기존
DEFERRABLE 제약 트리거(`ledger_entry_balanced_trg`, Σ차변=Σ대변 강제)를
건드리지 않았음을 회귀로 확인한다 — 이미 균형 잡힌 실제 entry(post_entry로
생성)에 한쪽만 있는 라인을 추가로 끼워 넣으면 커밋 시 거부돼야 한다(그
INSERT 자체는 롤백되므로 원 entry는 손상되지 않는다)."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import asyncpg
import pytest

from src.data.models.base import Currency
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.domain.models import Classification, Outcome
from src.foundation.evidence.domain.rules import assert_safe_payload, compute_payload_hash
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import AccountType, LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain import posting_rules
from src.foundation.ledger.domain.chart_of_accounts import PLATFORM_CASH_CLEARING, user_account
from src.foundation.ledger.domain.hash_chain import entry_hash, lines_digest
from src.foundation.ledger.domain.idempotency import idempotency_key
from tests.integration.conftest import create_test_tenant
from tests.support.deep_downgrade import purge_position_snapshots

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_DOWN_REVISION = "789c138f13fe"


def _asyncpg_dsn() -> str:
    return os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


def _run_alembic(*args: str) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *args],
        cwd=_PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=100,
    )
    assert result.returncode == 0, (
        f"alembic {' '.join(args)} 실패:\n{result.stdout}\n{result.stderr}"
    )


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


def _sweep_synthetic_snapshots(prefix: str) -> None:
    """FA-0d-fix (task-771991202): rows this module inserts below FA-4 carry
    synthetic non-5-part keys that `cdb114b6903f` (FA-0d) refuses fail-closed,
    so they are removed before the schema is brought back to head."""
    import asyncio

    async def _sweep() -> None:
        conn = await asyncpg.connect(_asyncpg_dsn())
        try:
            await conn.execute(
                "DELETE FROM pos_snapshot WHERE position_key LIKE $1", f"{prefix}%"
            )
        finally:
            await conn.close()

    asyncio.run(_sweep())


@pytest.fixture(autouse=True)
def _ensure_head():
    _run_alembic("upgrade", "head")
    yield
    _sweep_synthetic_snapshots("fa4-worm-test-")
    _run_alembic("upgrade", "head")


async def test_pos_journal_never_backfilled_because_worm_blocks_update(pool):
    tenant_id = await create_test_tenant(pool)

    await purge_position_snapshots(pool)  # deep downgrade: see tests/support/deep_downgrade.py
    _run_alembic("downgrade", _DOWN_REVISION)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO pos_account (tenant_id, venue, base_currency, cost_method) "
            "VALUES ($1, 'TESTVENUE', 'KRW', 'FIFO') RETURNING account_id",
            tenant_id,
        )
        position_key = f"fa4-worm-test-{uuid4().hex}"
        await conn.execute(
            "INSERT INTO pos_snapshot (position_key, tenant_id, account_id, instrument_id, "
            "quantity, cost_method) VALUES ($1, $2, $3, $4, 0, 'FIFO')",
            position_key,
            tenant_id,
            account_id,
            uuid4(),
        )
        journal_id = await conn.fetchval(
            "INSERT INTO pos_journal (tenant_id, account_id, position_key, sequence_no, "
            "entry_type, qty_delta, source_event_type, source_event_id, idempotency_key, "
            "digest, entry_hash, occurred_at) "
            "VALUES ($1, $2, $3, 1, 'FILL', 1, 'fill', 'fa4-worm-test', $4, "
            "'digest-placeholder', 'hash-placeholder', now()) RETURNING id",
            tenant_id,
            account_id,
            position_key,
            f"fa4-worm-test-{uuid4().hex}",
        )

    _run_alembic("upgrade", "963d5f3cfb1b")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM pos_journal WHERE id = $1", journal_id
        )
        backfilled = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE id = $1 AND fund_id IS NOT NULL", journal_id
        )
        null_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE id = $1 AND fund_id IS NULL", journal_id
        )

    assert row["fund_id"] is None
    assert row["portfolio_id"] is None
    assert backfilled == 0
    assert null_count == 1


def _clock() -> datetime:
    return datetime.now(timezone.utc)


async def _create_user_available_account(pool: asyncpg.Pool, user_id: UUID) -> str:
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


def _topup_event(*, event_ref: str, user_id: UUID) -> LedgerEvent:
    return LedgerEvent(
        event_type=LedgerEventType.TOPUP_CONFIRMED,
        event_ref=event_ref,
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=Decimal("10.00"),
        currency=Currency.KRW,
        parties={"user": user_id},
        extra={},
    )


class _RealPorts:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self.journal = PostgresJournalRepository(pool)
        self.balances = PostgresBalanceRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


async def _post_topup(pool: asyncpg.Pool, ports: _RealPorts, event_ref: str):
    user_id = uuid4()
    await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=event_ref, user_id=user_id)
    async with pool.acquire() as conn, conn.transaction():
        return await post_entry(
            conn, event, journal=ports.journal, balances=ports.balances,
            audit=ports.audit, clock=_clock,
        )


async def _insert_pre_fa4_ledger_entry(pool: asyncpg.Pool, event_ref: str, user_id: UUID) -> UUID:
    """FA-4 이전 스키마(`fund_id`/`portfolio_id` 컬럼 없음)에서 `post_entry`
    (LC-9)가 만들었을 행과 정확히 같은 모양을 손으로 재현한다.

    FA-8(task-1796)부터 `journal.append`(LC-8b)의 INSERT가 `fund_id`/
    `portfolio_id`를 무조건 함께 쓰므로, 그 함수는 이제 이 컬럼이 있는
    스키마(FA-4 이후)에서만 실행 가능하다 — 다운그레이드된(FA-4 이전)
    스키마에서 `post_entry`를 직접 호출할 수 없게 된 것은 회귀가 아니라
    FA-8이 의도한 배선이다. 그래도 "그 시절 코드가 만들었을 행"은 여전히
    재현해야 하므로, `post_entry`가 내부에서 쓰는 것과 같은 순수 함수
    (`posting_rules.lines_for`/`hash_chain.lines_digest`/`entry_hash`)로
    체인이 유효한 값을 계산해 직접 INSERT한다 — 모듈 docstring이 경고하는
    "손으로 만든 placeholder digest/hash로 해시체인을 오염시키는" 위험을
    피하면서도 FA-4 이전 스키마(컬럼 자체가 없음)를 그대로 흉내낸다."""
    code = await _create_user_available_account(pool, user_id)
    event = _topup_event(event_ref=event_ref, user_id=user_id)
    lines = posting_rules.lines_for(event)
    digest = lines_digest(lines)
    key = idempotency_key(event)
    entry_id = uuid4()
    posted_at = datetime.now(timezone.utc)

    async with pool.acquire() as conn, conn.transaction():
        last = await conn.fetchrow(
            "SELECT sequence_no, entry_hash FROM ledger_journal_entry "
            "ORDER BY sequence_no DESC LIMIT 1"
        )
        next_seq = 1 if last is None else last["sequence_no"] + 1
        prev_hash = None if last is None else last["entry_hash"]
        new_hash = entry_hash(
            prev_hash, next_seq, event.event_type, event.event_ref, digest, posted_at
        )

        payload: dict[str, object] = {"event_ref": event.event_ref, "line_count": len(lines)}
        assert_safe_payload(payload)
        audit = await PostgresAuditEventRepository(pool).append_event_in(
            conn,
            tenant_id=event.tenant_id,
            aggregate_type="ledger_journal_entry",
            aggregate_id=entry_id,
            aggregate_revision=None,
            action=event.event_type.value,
            outcome=Outcome.SUCCESS,
            actor_subject_id=event.actor_subject_id,
            trace_id=event.trace_id,
            payload_hash=compute_payload_hash(payload),
            payload=payload,
            classification=Classification.INTERNAL,
        )
        await conn.execute(
            "INSERT INTO ledger_journal_entry "
            "(entry_id, sequence_no, event_type, event_ref, idempotency_key, "
            " lines_digest, prev_hash, entry_hash, audit_event_id, posted_by, posted_at) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
            entry_id, next_seq, event.event_type.value, event.event_ref, key,
            digest, prev_hash, new_hash, audit.id, event.actor_subject_id, posted_at,
        )
        account_ids = {
            row["account_code"]: row["account_id"]
            for row in await conn.fetch(
                "SELECT account_code, account_id FROM ledger_account "
                "WHERE account_code = ANY($1::text[])",
                [code, PLATFORM_CASH_CLEARING],
            )
        }
        for line in lines:
            await conn.execute(
                "INSERT INTO ledger_posting_line "
                "(entry_id, line_no, account_id, side, amount, currency) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                entry_id, line.line_no, account_ids[line.account_code],
                line.side.value, line.amount, line.currency.value,
            )
    return entry_id


async def test_ledger_journal_entry_and_posting_line_never_backfilled(pool):
    await purge_position_snapshots(pool)  # deep downgrade: see tests/support/deep_downgrade.py
    _run_alembic("downgrade", _DOWN_REVISION)
    entry_id = await _insert_pre_fa4_ledger_entry(
        pool, f"fa4-worm-test:{uuid4().hex}", uuid4()
    )

    _run_alembic("upgrade", "head")

    async with pool.acquire() as conn:
        entry_row = await conn.fetchrow(
            "SELECT fund_id, portfolio_id FROM ledger_journal_entry WHERE entry_id = $1",
            entry_id,
        )
        line_rows = await conn.fetch(
            "SELECT fund_id, portfolio_id FROM ledger_posting_line WHERE entry_id = $1",
            entry_id,
        )
        entry_backfilled = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry "
            "WHERE entry_id = $1 AND fund_id IS NOT NULL",
            entry_id,
        )
        line_backfilled = await conn.fetchval(
            "SELECT count(*) FROM ledger_posting_line "
            "WHERE entry_id = $1 AND fund_id IS NOT NULL",
            entry_id,
        )
        line_null = await conn.fetchval(
            "SELECT count(*) FROM ledger_posting_line WHERE entry_id = $1 AND fund_id IS NULL",
            entry_id,
        )

    assert entry_row["fund_id"] is None
    assert entry_row["portfolio_id"] is None
    assert entry_backfilled == 0
    assert len(line_rows) == 2
    assert all(row["fund_id"] is None and row["portfolio_id"] is None for row in line_rows)
    assert line_backfilled == 0
    assert line_null == 2


async def test_ledger_balance_invariant_regression_still_enforced(pool):
    """이미 균형 잡힌 실제 entry(post_entry)에 한쪽만 있는 라인을 추가로
    끼워 넣으면 `ledger_entry_balanced_trg`가 커밋 시점에 거부해야 한다."""
    ports = _RealPorts(pool)
    view = await _post_topup(pool, ports, f"fa4-balance-test:{uuid4().hex}")

    async with pool.acquire() as conn:
        next_line_no = await conn.fetchval(
            "SELECT COALESCE(MAX(line_no), 0) + 1 FROM ledger_posting_line WHERE entry_id = $1",
            view.entry_id,
        )
        account_id = await conn.fetchval(
            "SELECT account_id FROM ledger_posting_line WHERE entry_id = $1 LIMIT 1",
            view.entry_id,
        )

    with pytest.raises(asyncpg.exceptions.RaiseError):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO ledger_posting_line "
                "(entry_id, line_no, account_id, side, amount, currency) "
                "VALUES ($1, $2, $3, 'DEBIT', 999.99, 'KRW')",
                view.entry_id,
                next_line_no,
                account_id,
            )
