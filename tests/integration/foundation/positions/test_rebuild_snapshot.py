"""LB-13 `rebuild_snapshot` 통합테스트 — 실 DB(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.3, §9.3 LB-13.
DoD: "재빌드 drift ∅" — 정상 스냅샷은 dry-run이든 아니든 drift가 비어야
하고, 스냅샷이 저널과 어긋나면(변조·버그) 재빌드가 그 차이를 drift로
보고하고 `dry_run=False`일 때만 실제로 고친다. `pos_journal`은 이 리프가
절대 건드리지 않는다(WORM) — 아래 테스트는 재빌드 전후로 저널 행 수가
그대로임을 확인해 이를 검증한다.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import asyncpg
import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide
from src.foundation.positions.application.rebuild_snapshot import (
    UnknownPositionError,
    rebuild_snapshot,
)
from tests.integration.conftest import create_test_tenant
from tests.integration.foundation.positions.conftest import (
    force_row_replace,
)
from tests.integration.foundation.positions.rebuild_snapshot_fixtures import (
    _clock,
    _fill,
    _funding,
    _key,
    _open,
    _RealPorts,
)
from tests.integration.foundation.positions.rebuild_snapshot_fixtures import (
    ports as ports,
)


async def test_unknown_position_rejected(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    tenant_id = await create_test_tenant(pool)
    with pytest.raises(UnknownPositionError):
        await rebuild_snapshot(
            _key(tenant_id),
            tenant_id=tenant_id,
            asset_class=AssetClass.CRYPTO,
            journal=ports.journal,
            snapshots=ports.snapshots,
            pool=pool,
            clock=_clock,
        )


async def test_healthy_snapshot_has_no_drift(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=order_id,
    )
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.SELL,
        quantity=Decimal("4"),
        price=Decimal("120"),
        fill_seq=2,
        order_id=order_id,
    )
    await _funding(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        amount=Decimal("-2"),
        funding_id=str(uuid4()),
    )

    report = await rebuild_snapshot(
        position_key,
        tenant_id=tenant_id,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=True,
    )

    assert report.drift == {}
    assert report.applied is False
    assert report.entries == 3


async def test_dry_run_reports_drift_without_writing(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=order_id,
    )

    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=Decimal("999"),
        realized_pnl_base=Decimal("42"),
    )

    report = await rebuild_snapshot(
        position_key,
        tenant_id=tenant_id,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=True,
    )

    assert report.applied is False
    assert report.drift["quantity"] == (Decimal("999"), Decimal("10"))
    assert report.drift["realized_pnl_base"] == (Decimal("42"), Decimal("0"))

    async with pool.acquire() as conn:
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, realized_pnl_base FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert snapshot_row["quantity"] == Decimal("999")
    assert snapshot_row["realized_pnl_base"] == Decimal("42")


async def test_apply_fixes_drift_without_touching_journal(
    pool: asyncpg.Pool, ports: _RealPorts
) -> None:
    tenant_id, account_id, position_key = await _open(pool)
    order_id = uuid4()
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=order_id,
    )
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.SELL,
        quantity=Decimal("4"),
        price=Decimal("120"),
        fill_seq=2,
        order_id=order_id,
    )

    async with pool.acquire() as conn:
        journal_count_before = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        quantity=Decimal("0"),
        realized_pnl_base=Decimal("0"),
    )

    report = await rebuild_snapshot(
        position_key,
        tenant_id=tenant_id,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=False,
    )

    assert report.applied is True
    assert report.drift["quantity"] == (Decimal("0"), Decimal("6"))
    assert report.drift["realized_pnl_base"] == (
        Decimal("0"),
        (Decimal("120") - Decimal("100")) * Decimal("4"),
    )

    async with pool.acquire() as conn:
        journal_count_after = await conn.fetchval(
            "SELECT count(*) FROM pos_journal WHERE position_key = $1", position_key
        )
        snapshot_row = await conn.fetchrow(
            "SELECT quantity, realized_pnl_base, last_journal_seq FROM pos_snapshot "
            "WHERE position_key = $1",
            position_key,
        )
    assert journal_count_after == journal_count_before == 2
    assert snapshot_row["quantity"] == Decimal("6")
    assert snapshot_row["realized_pnl_base"] == (Decimal("120") - Decimal("100")) * Decimal("4")
    assert snapshot_row["last_journal_seq"] == 2


async def test_apply_with_no_drift_is_noop(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    tenant_id, account_id, position_key = await _open(pool)
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=uuid4(),
    )

    async with pool.acquire() as conn:
        before = await conn.fetchrow(
            "SELECT updated_at FROM pos_snapshot WHERE position_key = $1", position_key
        )

    report = await rebuild_snapshot(
        position_key,
        tenant_id=tenant_id,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=False,
    )

    assert report.applied is False
    assert report.drift == {}

    async with pool.acquire() as conn:
        after = await conn.fetchrow(
            "SELECT updated_at FROM pos_snapshot WHERE position_key = $1", position_key
        )
    assert before["updated_at"] == after["updated_at"]


async def test_funding_fee_rebuild_with_fee_applied(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    """음성 테스트 — 펀딩피가 기록된 포지션에 rebuild_snapshot(dry_run=False)
    을 호출하면 funding_base drift 가 정확히 보고되고 적용된다.
    fundings가 fold 에 제대로 반영되는지 검증."""
    tenant_id, account_id, position_key = await _open(pool)
    # Buy 10 @ 100
    await _fill(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        side=OrderSide.BUY,
        quantity=Decimal("10"),
        price=Decimal("100"),
        fill_seq=1,
        order_id=uuid4(),
    )

    # Record funding fee: positive amount
    funding_id = str(uuid4())
    await _funding(
        pool,
        ports,
        tenant_id=tenant_id,
        account_id=account_id,
        position_key=position_key,
        amount=Decimal("5"),
        funding_id=funding_id,
    )

    # Now corrupt snapshot's funding_base to create drift
    await force_row_replace(
        pool,
        table="pos_snapshot",
        id_column="position_key",
        id_value=position_key,
        funding_base=Decimal("0"),
    )

    report = await rebuild_snapshot(
        position_key,
        tenant_id=tenant_id,
        asset_class=AssetClass.CRYPTO,
        journal=ports.journal,
        snapshots=ports.snapshots,
        pool=pool,
        clock=_clock,
        dry_run=False,
    )

    assert report.applied is True
    assert "funding_base" in report.drift, (
        "펀팅피가 기록된 포지션에서 funding_base drift 가 없으면 fold 가 펀팅피를 누락한다"
    )
    assert report.drift["funding_base"] == (Decimal("0"), Decimal("5")), (
        "funding_base drift 값이 펀딩피 금액과 일치해야 한다"
    )

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT funding_base, last_journal_seq FROM pos_snapshot WHERE position_key = $1",
            position_key,
        )
    assert row["funding_base"] == Decimal("5"), (
        "rebuild_snapshot 적용 후 funding_base 가 펀딩피 금액으로 고쳐져야 한다"
    )
    assert row["last_journal_seq"] == 2, "펀딩피 1건 + 체결 1건 = last_journal_seq 2 여야 한다"


async def test_invalid_position_key_format_rejected(pool: asyncpg.Pool, ports: _RealPorts) -> None:
    """음성 테스트 — 유효하지 않은 position_key format을 전달하면
    rebuild_snapshot이 PositionKey.parse() 단계에서 ValueError를 던진다.
    (1) separator 개수 부족, (2) UUID parsing 실패 등의 케이스를 확인."""
    tenant_id = await create_test_tenant(pool)

    invalid_keys = [
        "",  # empty string
        "TESTVENUE",  # too few parts
        "TESTVENUE:INST001:default",  # only 3 parts
        "TESTVENUE:INST001:default:paper",  # only 4 parts (missing portfolio_id)
        "TESTVENUE:INST001:default:paper:not-a-uuid",  # invalid UUID format
        "TESTVENUE:INST001:default:paper:",  # empty portfolio_id
    ]

    for invalid_key in invalid_keys:
        with pytest.raises(ValueError):
            await rebuild_snapshot(
                invalid_key,
                tenant_id=tenant_id,
                asset_class=AssetClass.CRYPTO,
                journal=ports.journal,
                snapshots=ports.snapshots,
                pool=pool,
                clock=_clock,
                dry_run=True,
            )
