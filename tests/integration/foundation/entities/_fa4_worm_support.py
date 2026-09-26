"""FA-4(963d5f3cfb1b) WORM 회귀 테스트 공용 픽스처/헬퍼.

`test_migration_fa4_worm_no_backfill.py`(백필 금지 + WORM guard UPDATE/DELETE
거부)와 `test_migration_fa4_worm_failure_injection.py`(balance invariant
회귀 + 실패주입·롤백)가 공유한다. 분할 이력: task-7810
(782e05f9/task-7707가 524줄로 키운 단일 파일을 관심사별로 쪼갬).

`ledger_journal_entry`/`ledger_posting_line` 행은 `post_entry`(LC-9) 실제
애플리케이션 경로로만 만든다 — 손으로 만든 placeholder digest/entry_hash로
직접 INSERT하면 `ledger_entry_balanced_trg`는 통과해도
`hash_chain.verify_chain`(LC-3)이 깨진 체인으로 오판할 위험이 있고, WORM이라
이 행은 절대 지울 수 없다(한 번 잘못 심으면 테스트 DB가 영구히 오염된다).
`post_entry`는 해시체인·잔액을 모두 올바르게 채우므로 이 위험이 없다.
"""

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
from src.foundation.ledger.contracts.v1 import (
    AccountType,
    LedgerEvent,
    LedgerEventType,
    Side,
    UserSub,
)
from src.foundation.ledger.domain import posting_rules
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    account_type,
    user_account,
)
from src.foundation.ledger.domain.hash_chain import entry_hash, lines_digest
from src.foundation.ledger.domain.idempotency import idempotency_key

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
            await conn.execute("DELETE FROM pos_snapshot WHERE position_key LIKE $1", f"{prefix}%")
        finally:
            await conn.close()

    asyncio.run(_sweep())


@pytest.fixture(autouse=True)
def _ensure_head():
    _run_alembic("upgrade", "head")
    yield
    _sweep_synthetic_snapshots("fa4-worm-test-")
    _run_alembic("upgrade", "head")


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
            conn,
            event,
            journal=ports.journal,
            balances=ports.balances,
            audit=ports.audit,
            clock=_clock,
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
            entry_id,
            next_seq,
            event.event_type.value,
            event.event_ref,
            key,
            digest,
            prev_hash,
            new_hash,
            audit.id,
            event.actor_subject_id,
            posted_at,
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
                entry_id,
                line.line_no,
                account_ids[line.account_code],
                line.side.value,
                line.amount,
                line.currency.value,
            )

        # task-5687: keep `ledger_balance` in lockstep with the hand-written
        # journal entry above, same as the real (pre-FA8) `post_entry` write
        # path would have -- otherwise PLATFORM_CASH_CLEARING (shared, seeded
        # by LC-6, never reset across CI runs) drifts from the journal fold
        # forever, and FA-15 replay_verify reports a permanent false
        # MISMATCH on it (same bug class as task-5309).
        balances = PostgresBalanceRepository(pool)
        deltas: dict[str, Decimal] = {}
        for line in lines:
            debit_increases = account_type(line.account_code) in {
                AccountType.ASSET,
                AccountType.EXPENSE,
            }
            increases = (line.side is Side.DEBIT) == debit_increases
            signed = line.amount if increases else -line.amount
            deltas[line.account_code] = deltas.get(line.account_code, Decimal("0")) + signed
        current = await balances.get_for_update(conn, list(deltas))
        for account_code, delta in deltas.items():
            await balances.apply(
                conn, account_code, delta, Decimal("0"), current[account_code].last_entry_seq
            )
    return entry_id


class _FailingAudit:
    """실패주입: `AuditAppender` 포트를 구현하되 항상 던진다."""

    async def append_event_in(self, *args: object, **kwargs: object) -> None:
        raise RuntimeError("injected audit append failure")
