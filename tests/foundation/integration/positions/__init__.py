"""Negative and failure-injection coverage for the positions integration leaf."""

from __future__ import annotations

import importlib
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import OrderSide
from src.foundation.positions.contracts.v1 import RecordFillCommand
from src.foundation.positions.domain.position_key import (
    InvalidPositionKeyError,
    PositionKey,
)


def test_position_key_rejects_legacy_four_part_value() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("TESTVENUE:BTC-USD:default:paper")


def test_position_key_rejects_delimiter_in_component() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey(
            venue="TEST:VENUE",
            instrument_id="BTC-USD",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=uuid4(),
        )


def test_position_key_rejects_non_uuid_portfolio_component() -> None:
    with pytest.raises(InvalidPositionKeyError):
        PositionKey.parse("TESTVENUE:BTC-USD:default:paper:not-a-uuid")


@pytest.mark.asyncio
async def test_record_fill_propagates_lock_failure_without_writing(monkeypatch) -> None:
    record_fill_module = importlib.import_module(
        "src.foundation.positions.application.record_fill"
    )

    async def fail_to_lock(_conn, _position_key: str) -> None:
        raise RuntimeError("injected advisory-lock failure")

    monkeypatch.setattr(record_fill_module, "_acquire_position_lock", fail_to_lock)
    command = RecordFillCommand(
        tenant_id=uuid4(),
        account_id=uuid4(),
        position_key=str(
            PositionKey(
                venue="TESTVENUE",
                instrument_id="BTC-USD",
                strategy_id="default",
                execution_id="paper",
                portfolio_id=uuid4(),
            )
        ),
        order_id=uuid4(),
        fill_seq=1,
        side=OrderSide.BUY,
        quantity=1,
        price=Money(amount=100, currency=Currency.KRW),
        fee=None,
        occurred_at=datetime.now(timezone.utc),
        trace_id=uuid4(),
    )

    with pytest.raises(RuntimeError, match="injected advisory-lock failure"):
        await record_fill_module.record_fill(
            object(),
            command,
            asset_class=AssetClass.CRYPTO,
            journal=object(),
            snapshots=object(),
            audit=object(),
            clock=lambda: datetime.now(timezone.utc),
        )
