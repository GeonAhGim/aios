"""FA-4(963d5f3cfb1b) 마이그레이션 — balance invariant 회귀 + 실패주입·롤백.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-4 DoD.
`test_migration_fa4_worm_no_backfill.py`(백필 금지 + WORM guard UPDATE/DELETE
거부)에서 분리했다(task-7810, 782e05f9/task-7707가 원 파일을 524줄로 키운 것을
관심사별로 재분할). 공용 픽스처/헬퍼는 `_fa4_worm_support.py`에 있다.

마지막 테스트는 이 마이그레이션이 `ledger_posting_line`에 붙은 기존
DEFERRABLE 제약 트리거(`ledger_entry_balanced_trg`, Σ차변=Σ대변 강제)를
건드리지 않았음을 회귀로 확인한다 — 이미 균형 잡힌 실제 entry(post_entry로
생성)에 한쪽만 있는 라인을 추가로 끼워 넣으면 커밋 시 거부돼야 한다(그
INSERT 자체는 롤백되므로 원 entry는 손상되지 않는다)."""

from __future__ import annotations

from uuid import uuid4

import asyncpg
import pytest

from src.foundation.ledger.adapters.postgres_balance_repository import PostgresBalanceRepository
from src.foundation.ledger.adapters.postgres_journal_repository import PostgresJournalRepository
from src.foundation.ledger.application.post_entry import post_entry
from src.foundation.ledger.domain.idempotency import idempotency_key
from tests.integration.foundation.entities._fa4_worm_support import (
    _clock,
    _create_user_available_account,
    _ensure_head,  # noqa: F401 -- re-exported autouse fixture
    _FailingAudit,
    _post_topup,
    _RealPorts,
    _topup_event,
    pool,  # noqa: F401 -- re-exported fixture
)

__all__ = ["pool", "_ensure_head"]


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


async def test_post_entry_audit_failure_rolls_back_worm_journal(pool):
    """실패주입: 모듈 docstring이 인용하는 §6 실패 모드("C 감사 append 실패
    → 포스팅 전체 롤백")를 직접 유발한다. `journal.append`(WORM 테이블
    insert)가 `audit.append_event_in` *이전에* 같은 트랜잭션에서 실행되므로,
    감사 append가 던지면 그 WORM insert도 함께 롤백되어야 한다 — 그렇지
    않으면 감사 로그 없는 반쪽짜리 원장 행이 영구히 남는다(WORM이라 지울
    수 없다)."""
    journal = PostgresJournalRepository(pool)
    balances = PostgresBalanceRepository(pool)
    user_id = uuid4()
    await _create_user_available_account(pool, user_id)
    event_ref = f"fa4-worm-test:audit-fail:{uuid4().hex}"
    event = _topup_event(event_ref=event_ref, user_id=user_id)
    key = idempotency_key(event)

    with pytest.raises(RuntimeError, match="injected audit append failure"):
        async with pool.acquire() as conn, conn.transaction():
            await post_entry(
                conn,
                event,
                journal=journal,
                balances=balances,
                audit=_FailingAudit(),
                clock=_clock,
            )

    async with pool.acquire() as conn:
        entry_count = await conn.fetchval(
            "SELECT count(*) FROM ledger_journal_entry WHERE idempotency_key = $1", key
        )
    assert entry_count == 0
