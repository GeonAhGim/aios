"""LB-18 적대적 — 다른 tenant의 position_key로 record_fill.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.3 LB-18
("다른 tenant의 position_key로 record_fill → 거부").

실결함(task-489 note, 이 커밋에서 함께 고침): `record_fill`이 `SnapshotRepository.
get`을 `position_key`만으로 조회해 `command.tenant_id`/`account_id`를 소유자와
대조하지 않았다 — 다른 tenant가 남의 `position_key`를 알아내면(주문·전략
로그 등으로 유출될 수 있다) 예외 없이 체결이 그대로 기록됐다.

수정: `SnapshotRepository.get`이 `tenant_id`로 스코프하고([[snapshot_repository]]),
`record_fill`은 그 결과가 `None`이거나 `account_id`가 다르면 기존
`UnknownPositionError`(POS_ACCOUNT_UNKNOWN)로 거부한다 — 신규 에러코드를
만들지 않는다(계약 변경 없음), "존재하지만 남의 것"과 "아예 없음"을 같은
예외로 합쳐 존재 자체를 흘리지 않는다."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_fill import UnknownPositionError, record_fill
from src.foundation.positions.contracts.v1 import RecordFillCommand
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_user
from tests.integration.foundation.positions.conftest import create_pos_account, open_position

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _clock() -> datetime:
    return datetime.now(timezone.utc)


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


def _key() -> str:
    return str(
        PositionKey(
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="cross-tenant",
        )
    )


async def _attack_command(*, tenant_id, account_id, position_key) -> RecordFillCommand:
    return RecordFillCommand(
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        order_id=uuid4(),
        fill_seq=1,
        side=OrderSide.BUY,
        quantity=Decimal("1000"),
        price=Money(amount=Decimal("1"), currency=Currency.KRW),
        fee=None,
        occurred_at=_OCCURRED_AT,
        trace_id=uuid4(),
    )


async def test_cross_tenant_position_key_rejected(pool):
    owner_id = await create_test_user(pool)
    owner_account_id = await create_pos_account(pool, owner_id)
    position_key = _key()
    await open_position(
        pool, tenant_id=owner_id, account_id=owner_account_id, position_key=position_key
    )

    attacker_id = await create_test_user(pool)
    attacker_account_id = await create_pos_account(pool, attacker_id)
    journal = PostgresJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)

    with pytest.raises(UnknownPositionError):
        async with pool.acquire() as conn, conn.transaction():
            await record_fill(
                conn,
                await _attack_command(
                    tenant_id=attacker_id,
                    account_id=attacker_account_id,
                    position_key=position_key,
                ),
                asset_class=AssetClass.CRYPTO,
                journal=journal,
                snapshots=snapshots,
                audit=audit,
                clock=_clock,
            )

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, last_journal_seq, tenant_id, account_id FROM pos_snapshot "
            "WHERE position_key = $1",
            position_key,
        )
    assert journal_count == 0, "공격자의 체결이 저널에 그대로 기록됐습니다"
    assert snapshot_row["quantity"] == Decimal("0")
    assert snapshot_row["last_journal_seq"] == 0
    assert snapshot_row["tenant_id"] == owner_id
    assert snapshot_row["account_id"] == owner_account_id


async def test_same_tenant_different_account_position_key_rejected(pool):
    """같은 tenant 안에서도 `account_id`가 다르면 거부돼야 한다 — `position_key`가
    `tenant_id`만으로는 계정을 구분하지 못하므로(§4.3, 계좌는 tenant 아래
    복수 개일 수 있다) tenant 스코프만으로는 이 경계를 못 막는다."""
    owner_id = await create_test_user(pool)
    owner_account_id = await create_pos_account(pool, owner_id)
    position_key = _key()
    await open_position(
        pool, tenant_id=owner_id, account_id=owner_account_id, position_key=position_key
    )

    other_account_id = await create_pos_account(pool, owner_id, venue="OTHERVENUE")
    journal = PostgresJournalRepository(pool)
    snapshots = PostgresSnapshotRepository(pool)
    audit = PostgresAuditEventRepository(pool)

    with pytest.raises(UnknownPositionError):
        async with pool.acquire() as conn, conn.transaction():
            await record_fill(
                conn,
                await _attack_command(
                    tenant_id=owner_id,
                    account_id=other_account_id,
                    position_key=position_key,
                ),
                asset_class=AssetClass.CRYPTO,
                journal=journal,
                snapshots=snapshots,
                audit=audit,
                clock=_clock,
            )

    async with pool.acquire() as conn:
        journal_count = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
    assert journal_count == 0
