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
from uuid import UUID, uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.entities.domain.defaults import default_portfolio_id
from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.positions.adapters.postgres_journal_repository import (
    PostgresJournalRepository,
)
from src.foundation.positions.adapters.postgres_snapshot_repository import (
    PostgresSnapshotRepository,
)
from src.foundation.positions.application.record_fill import UnknownPositionError, record_fill
from src.foundation.positions.contracts.v1 import RecordFillCommand
from src.foundation.positions.domain.cost_basis.fifo import NegativeQuantityError
from src.foundation.positions.domain.position_key import PositionKey
from tests.integration.conftest import create_test_tenant
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


def _key(tenant_id: UUID) -> str:
    return str(
        PositionKey(
            portfolio_id=default_portfolio_id(tenant_id),
            venue="TESTVENUE",
            instrument_id=f"INST{uuid4().hex[:8]}",
            strategy_id="default",
            execution_id="cross-tenant",
        )
    )


async def _delete_pos_snapshot(pool, *, position_key: str) -> None:
    """FA-0d(cdb114b6903f)는 `pos_snapshot`에 남은 행을 하나라도 보면 이후
    마이그레이션 왕복 테스트를 fail-closed로 거부한다(task-2543) — 이 행의
    `portfolio_id`는 합성값이라 실제 FA-4 백필로 재현할 수 없으므로,
    부트스트랩 대신 테스트가 끝나면 직접 지워 그 불변조건(0행)을 지킨다."""
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM pos_snapshot WHERE position_key = $1", position_key)


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
    owner_id = await create_test_tenant(pool)
    owner_account_id = await create_pos_account(pool, owner_id)
    position_key = _key(owner_id)
    await open_position(
        pool, tenant_id=owner_id, account_id=owner_account_id, position_key=position_key
    )
    try:
        attacker_id = await create_test_tenant(pool)
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
    finally:
        await _delete_pos_snapshot(pool, position_key=position_key)


async def test_same_tenant_different_account_position_key_rejected(pool):
    """같은 tenant 안에서도 `account_id`가 다르면 거부돼야 한다 — `position_key`가
    `tenant_id`만으로는 계정을 구분하지 못하므로(§4.3, 계좌는 tenant 아래
    복수 개일 수 있다) tenant 스코프만으로는 이 경계를 못 막는다."""
    owner_id = await create_test_tenant(pool)
    owner_account_id = await create_pos_account(pool, owner_id)
    position_key = _key(owner_id)
    await open_position(
        pool, tenant_id=owner_id, account_id=owner_account_id, position_key=position_key
    )
    try:
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
    finally:
        await _delete_pos_snapshot(pool, position_key=position_key)


async def test_oversell_beyond_available_lots_rejected(pool):
    """DEEPEN(task-2970) — 적대적 페이로드 3번째 경계: 신원 위조(cross-tenant,
    다른 계정)가 아니라 보유 로트 합보다 큰 매도(공매도 시도)다. 현물
    (`AssetClass.CRYPTO`)은 공매도가 금지돼 있으므로(§4.3, `cost_basis.fifo.
    NegativeQuantityError`, POS_NEGATIVE_QUANTITY) `record_fill`은 저널에
    쓰기 전 원가법 계산 단계에서 이를 거부해야 한다 — 절반만 반영된 매도가
    저널에 남으면 "스냅샷 = fold(저널)" 불변이 깨지므로, 거부가 DB 쓰기
    없이 일어나는지(저널 미증가·스냅샷 불변)까지 함께 본다."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    try:
        journal = PostgresJournalRepository(pool)
        snapshots = PostgresSnapshotRepository(pool)
        audit = PostgresAuditEventRepository(pool)

        buy_command = RecordFillCommand(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            order_id=uuid4(),
            fill_seq=1,
            side=OrderSide.BUY,
            quantity=Decimal("5"),
            price=Money(amount=Decimal("100"), currency=Currency.KRW),
            fee=None,
            occurred_at=_OCCURRED_AT,
            trace_id=uuid4(),
        )
        async with pool.acquire() as conn, conn.transaction():
            await record_fill(
                conn,
                buy_command,
                asset_class=AssetClass.CRYPTO,
                journal=journal,
                snapshots=snapshots,
                audit=audit,
                clock=_clock,
            )

        oversell_command = RecordFillCommand(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            order_id=uuid4(),
            fill_seq=1,
            side=OrderSide.SELL,
            quantity=Decimal("1000"),
            price=Money(amount=Decimal("100"), currency=Currency.KRW),
            fee=None,
            occurred_at=_OCCURRED_AT,
            trace_id=uuid4(),
        )
        with pytest.raises(NegativeQuantityError):
            async with pool.acquire() as conn, conn.transaction():
                await record_fill(
                    conn,
                    oversell_command,
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
                "SELECT quantity, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
                position_key,
            )
        assert journal_count == 1, "공매도 시도가 저널에 그대로 기록됐습니다"
        assert snapshot_row["quantity"] == Decimal("5")
        assert snapshot_row["last_journal_seq"] == 1
    finally:
        await _delete_pos_snapshot(pool, position_key=position_key)


async def test_snapshot_adapter_failure_during_record_fill_leaves_no_partial_journal_entry(
    pool, monkeypatch
):
    """DEEPEN(task-2970) — failure-injection(실 DB/어댑터 결함 시뮬레이션):
    저널 append가 이미 성공한 직후(같은 트랜잭션 안), 스냅샷 어댑터가 DB
    연결 끊김을 겪으면 이미 쓴 저널 엔트리까지 트랜잭션째 롤백돼야 한다.
    `PostgresSnapshotRepository.upsert`를 monkeypatch로 후킹해
    `asyncpg.PostgresConnectionError`를 던지게 하는 방식은 이 저장소의
    다른 어댑터 결함 주입 테스트들과 같은 패턴이다(
    `tests/integration/foundation/ledger/test_chargeback.py`의
    `test_chargeback_connection_failure_during_balance_apply_leaves_no_partial_write`
    참고) — 실제 커넥션을 물리적으로 끊는 대신 어댑터 계층에서 그 예외가
    발생했을 때 호출부(`record_fill`)가 트랜잭션 원자성을 지키는지만
    검증한다(`get`은 실제 어댑터 그대로 두고 `upsert`만 결함을 주입한다)."""
    tenant_id = await create_test_tenant(pool)
    account_id = await create_pos_account(pool, tenant_id)
    position_key = _key(tenant_id)
    await open_position(pool, tenant_id=tenant_id, account_id=account_id, position_key=position_key)
    try:
        journal = PostgresJournalRepository(pool)
        snapshots = PostgresSnapshotRepository(pool)
        audit = PostgresAuditEventRepository(pool)

        async def _boom_upsert(self, conn, snapshot, expected_seq):
            raise asyncpg.PostgresConnectionError("simulated snapshot adapter connection failure")

        monkeypatch.setattr(PostgresSnapshotRepository, "upsert", _boom_upsert)
        command = RecordFillCommand(
            tenant_id=tenant_id,
            account_id=account_id,
            position_key=position_key,
            order_id=uuid4(),
            fill_seq=1,
            side=OrderSide.BUY,
            quantity=Decimal("1"),
            price=Money(amount=Decimal("100"), currency=Currency.KRW),
            fee=None,
            occurred_at=_OCCURRED_AT,
            trace_id=uuid4(),
        )
        with pytest.raises(asyncpg.PostgresConnectionError):
            async with pool.acquire() as conn, conn.transaction():
                await record_fill(
                    conn,
                    command,
                    asset_class=AssetClass.CRYPTO,
                    journal=journal,
                    snapshots=snapshots,
                    audit=audit,
                    clock=_clock,
                )
        monkeypatch.undo()

        async with pool.acquire() as conn:
            journal_count = await conn.fetchval(
                "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
            )
            snapshot_row = await conn.fetchrow(
                "SELECT quantity, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
                position_key,
            )
        assert journal_count == 0, "스냅샷 어댑터 결함에도 저널 엔트리가 커밋됐습니다(원자성 위반)"
        assert snapshot_row["quantity"] == Decimal("0")
        assert snapshot_row["last_journal_seq"] == 0
    finally:
        await _delete_pos_snapshot(pool, position_key=position_key)
