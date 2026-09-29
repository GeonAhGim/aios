"""FA-15 `test_replay_verify*.py` 공용 픽스처/헬퍼.

`test_replay_verify.py`(핵심 일치·결정성·실패주입·성능예산)와
`test_replay_verify_tamper_detection.py`(이벤트 트레일 밖 변조 탐지)가
공유한다. 분할 이력: task-7810 (7e9cc090/task-7695가 490->583줄로 키운
파일을 관심사별로 재분할, check_code_ratchets.py loc_over_500 게이트 적색)."""

from __future__ import annotations

import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from src.data.models.base import Currency
from src.data.models.trading import OrderStatus
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.contracts.v1 import AccountType, LedgerEvent, LedgerEventType, UserSub
from src.foundation.ledger.domain.chart_of_accounts import user_account
from src.services.oms.adapters.order_repository import PostgresOrderRepository
from src.services.oms.contracts.v1_events import OrderTransitionEvent
from tests.integration.conftest import create_test_user
from tests.integration.oms.conftest import insert_order

_REPO_ROOT = Path(__file__).resolve().parents[3]


async def assert_no_replay_window_leftovers(pool, *, as_of: datetime, hours: int) -> None:
    """Fail loudly if the tables `scripts/replay_verify.py` reads already hold
    rows inside the [as_of - hours, as_of) window before this module wrote
    anything. Such rows were written outside this module (another test file
    that shares the worker DB, or a dirty local template) and would surface
    later as a StreamDiff on a key this module never created (PR #91 runs
    36224833535 / 36225868874). The message carries sample keys so the
    polluting writer can be traced (`grep -rn <order_id-or-account_code>`),
    since the database does not record which test module wrote a row."""
    start = as_of - timedelta(hours=hours)
    async with pool.acquire() as conn:
        orders = await conn.fetch(
            "SELECT DISTINCT order_id FROM order_events "
            "WHERE occurred_at >= $1 AND occurred_at < $2 LIMIT 5",
            start,
            as_of,
        )
        accounts = await conn.fetch(
            "SELECT DISTINCT la.account_code FROM ledger_posting_line lpl "
            "JOIN ledger_journal_entry lje ON lje.entry_id = lpl.entry_id "
            "JOIN ledger_account la ON la.account_id = lpl.account_id "
            "WHERE lje.posted_at >= $1 AND lje.posted_at < $2 LIMIT 5",
            start,
            as_of,
        )
    if orders or accounts:
        raise AssertionError(
            "replay_verify window is not clean before this module ran: "
            f"order_events order_ids={[str(r['order_id']) for r in orders]} "
            f"ledger account_codes={[r['account_code'] for r in accounts]} "
            f"(window {start.isoformat()} .. {as_of.isoformat()}). These rows were written "
            "outside this module -- another test module sharing the worker DB, or a dirty "
            "template database -- and replay_verify would report them as StreamDiff. Trace "
            "the writer by grepping the sample keys' fixtures/seeders."
        )


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _order_event(
    order_id, *, from_status: OrderStatus, to_status: OrderStatus, event: str
) -> OrderTransitionEvent:
    return OrderTransitionEvent(
        order_id=order_id,
        from_status=from_status,
        to_status=to_status,
        event=event,
        reason_code=None,
        actor_subject_id="system",
        trace_id=uuid4(),
        command_id=None,
        provider_event_id=None,
        occurred_at=_clock(),
        payload_hash="e" * 64,
    )


async def _seed_order(pool) -> None:
    repo = PostgresOrderRepository()
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id, status="CREATED")
        await repo.transition(
            conn,
            order_id=order_id,
            expected_status=OrderStatus.CREATED,
            expected_version=0,
            new_status=OrderStatus.VALIDATED,
            patch={},
            event=_order_event(
                order_id,
                from_status=OrderStatus.CREATED,
                to_status=OrderStatus.VALIDATED,
                event="VALIDATED",
            ),
        )


async def _seed_ledger_account(pool, *, allow_negative: bool) -> str:
    code = user_account(uuid4(), UserSub.RECEIVABLE if allow_negative else UserSub.AVAILABLE)
    async with pool.acquire() as conn:
        account_id = await conn.fetchval(
            "INSERT INTO ledger_account (account_code, account_type, currency, allow_negative) "
            "VALUES ($1, $2, $3, $4) RETURNING account_id",
            code,
            (AccountType.ASSET if allow_negative else AccountType.LIABILITY).value,
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


async def _seed_ledger_entry(pool) -> str:
    """One MANUAL_ADJUSTMENT between two fresh accounts -- debit_code is
    what the test asserts on, mirroring test_projections.py's precedent."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    audit = PostgresAuditEventRepository(pool)
    debit_code = await _seed_ledger_account(pool, allow_negative=True)
    credit_code = await _seed_ledger_account(pool, allow_negative=False)

    event = LedgerEvent(
        event_type=LedgerEventType.MANUAL_ADJUSTMENT,
        event_ref=f"adj:{uuid4().hex}",
        tenant_id=None,
        actor_subject_id=None,
        trace_id=uuid4(),
        amount=Decimal("10.00"),
        currency=Currency.KRW,
        parties={},
        extra={"debit_account": debit_code, "credit_account": credit_code},
    )
    async with pool.acquire() as conn, conn.transaction():
        await post_entry(conn, event, journal=journal, balances=balances, audit=audit, clock=_clock)
    return debit_code


def _run_script(
    *, hours: int, as_of: datetime, database_url: str
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": database_url, "PYTHONPATH": str(_REPO_ROOT)}
    return subprocess.run(
        [
            sys.executable,
            str(_REPO_ROOT / "scripts" / "replay_verify.py"),
            "--hours",
            str(hours),
            "--as-of",
            as_of.isoformat(),
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        encoding="utf-8",
        timeout=60,
    )
