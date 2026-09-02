"""LB-13 `record_funding_fee` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §9.3 LB-13.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_funding_fee import (
    UnknownPositionError,
    record_funding_fee,
)
from src.foundation.positions.contracts.v1 import RecordFundingCommand
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_user
from tests.integration.foundation.positions.conftest import create_pos_account, open_position

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _key() -> str:
    return str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="paper",
        )
    )


class _RealPorts:
    def __init__(self, pool):
        self.journal = PostgresJournalRepository(pool)
        self.snapshots = PostgresSnapshotRepository(pool)
        self.audit = PostgresAuditEventRepository(pool)


class _BoomAuditAppender:
    async def append_event_in(self, conn, **kwargs):
        raise RuntimeError("injected audit failure")


@pytest.fixture
def ports(pool):
    return _RealPorts(pool)


def _command(
    *,
    tenant_id,
    account_id,
    position_key,
    amount: Decimal = Decimal("-5"),
    rate: Decimal = Decimal("0.0001"),
    funding_id: str | None = None,
    occurred_at: datetime = _OCCURRED_AT,
) -> RecordFundingCommand:
    return RecordFundingCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        funding_id=funding_id or str(uuid4()),
        amount=Money(amount=amount, currency=Currency.KRW),
        rate=rate,
        occurred_at=occurred_at,
        trace_id=uuid4(),
    )


async def _open(pool):
    tenant_id = await create_test_user(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key()
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    return tenant_id, account_id, position_key


async def _record(pool, ports, command, *, audit=None):
    async with pool.acquire() as conn, conn.transaction():
        return await record_funding_fee(
            conn,
            command,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            audit=audit or ports.audit,
            clock=_clock,
        )


async def test_funding_accrues_into_funding_base(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id, account_id=account_id, position_key=position_key,
        amount=Decimal("-5"),
    )

    snapshot = await _record(pool, ports, command)

    assert snapshot.funding_base == Decimal("-5")
    assert snapshot.quantity == Decimal("0")
    assert snapshot.last_journal_seq == 1


async def test_multiple_settlements_accumulate(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    await _record(
        pool, ports,
        _command(
            tenant_id=tenant_id, account_id=account_id, position_key=position_key,
            amount=Decimal("-5"),
        ),
    )

    snapshot = await _record(
        pool, ports,
        _command(
            tenant_id=tenant_id, account_id=account_id, position_key=position_key,
            amount=Decimal("3"),
        ),
    )

    assert snapshot.funding_base == Decimal("-2")
    assert snapshot.last_journal_seq == 2


async def test_unknown_position_rejected(pool, ports):
    command = _command(
        tenant_id=uuid4(), account_id=uuid4(), position_key=_key(), amount=Decimal("-1"),
    )

    with pytest.raises(UnknownPositionError):
        await _record(pool, ports, command)


async def test_replay_same_funding_id_returns_existing_without_duplicate(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id, account_id=account_id, position_key=position_key, amount=Decimal("-5"),
    )

    first = await _record(pool, ports, command)
    second = await _record(pool, ports, command)

    assert second.funding_base == first.funding_base
    assert second.last_journal_seq == first.last_journal_seq

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        audit_count = await conn.fetchval(
            "SELECT count(*) FROM foundation_audit_event WHERE aggregate_id = $1", account_id
        )
    assert journal_count == 1
    assert audit_count == 1


async def test_audit_failure_rolls_back_journal_and_snapshot(pool, ports):
    tenant_id, account_id, position_key = await _open(pool)
    command = _command(
        tenant_id=tenant_id, account_id=account_id, position_key=position_key, amount=Decimal("-5"),
    )

    with pytest.raises(RuntimeError):
        await _record(pool, ports, command, audit=_BoomAuditAppender())

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT funding_base, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert journal_count == 0
    assert snapshot_row["funding_base"] == Decimal("0")
    assert snapshot_row["last_journal_seq"] == 0
