"""거래소 원시 체결 정규화·집계 단위테스트 — L4-05. DB 없음."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.data.models.trading import OrderSide
from src.services.oms.contracts.v1_events import FillEvent
from src.services.oms.domain.errors import UnknownSymbolError
from src.services.oms.domain.fill_normalizer import aggregate, normalize_fill
from src.services.oms.domain.symbol_registry import SymbolRegistry
from src.services.oms.domain.venue_profile import TimeoutBudget, VenueCapabilityProfile


@pytest.fixture
def registry() -> SymbolRegistry:
    reg = SymbolRegistry()
    reg.register(
        "BTC/USDT",
        "bitget",
        "BTCUSDT",
        tick=Decimal("0.01"),
        lot=Decimal("0.0001"),
        min_notional=Decimal("5"),
        quote_ccy="USDT",
    )
    return reg


@pytest.fixture
def profile() -> VenueCapabilityProfile:
    return VenueCapabilityProfile(
        venue="bitget",
        asset_classes=[],
        order_types=set(),
        time_in_force=set(),
        supports_client_order_id=True,
        client_order_id_max_len=40,
        client_order_id_charset="ABC",
        id_policy="STABLE",
        supports_modify=True,
        supports_cancel="YES",
        supports_ws_orders=True,
        supports_batch=False,
        price_tick={},
        qty_lot={},
        min_notional={},
        rate_limits={},
        submit_timeout=TimeoutBudget(),
        query_timeout=TimeoutBudget(),
        market_hours=None,
        max_open_orders_per_symbol=20,
        verified="DOC_ONLY",
    )


def _raw_fill(**overrides: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "fill_id": "f-1",
        "exchange_order_id": "ex-1",
        "venue_symbol": "BTCUSDT",
        "side": "BUY",
        "quantity": "0.01",
        "price": "50000",
        "fee": "0.5",
        "fee_currency": "USDT",
        "venue_ts": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return defaults


def test_normalize_fill_converts_to_decimal_and_canonical_symbol(
    registry: SymbolRegistry, profile: VenueCapabilityProfile
) -> None:
    event = normalize_fill(_raw_fill(), venue="bitget", profile=profile, registry=registry)
    assert event.symbol == "BTC/USDT"
    assert event.quantity == Decimal("0.01")
    assert event.price == Decimal("50000")
    assert event.fee == Decimal("0.5")
    assert event.side == OrderSide.BUY
    assert event.liquidity == "UNKNOWN"


def test_normalize_fill_rejects_venue_mismatch(
    registry: SymbolRegistry, profile: VenueCapabilityProfile
) -> None:
    with pytest.raises(ValueError, match="venue"):
        normalize_fill(_raw_fill(), venue="kis", profile=profile, registry=registry)


def test_normalize_fill_rejects_missing_required_key(
    registry: SymbolRegistry, profile: VenueCapabilityProfile
) -> None:
    raw = _raw_fill()
    del raw["fee"]
    with pytest.raises(ValueError, match="필수 키"):
        normalize_fill(raw, venue="bitget", profile=profile, registry=registry)


def test_normalize_fill_unregistered_symbol_fails_closed(
    registry: SymbolRegistry, profile: VenueCapabilityProfile
) -> None:
    with pytest.raises(UnknownSymbolError):
        normalize_fill(
            _raw_fill(venue_symbol="ETHUSDT"), venue="bitget", profile=profile, registry=registry
        )


def _fill(quantity: str, price: str, fee: str = "0", fee_currency: str = "USDT") -> FillEvent:
    return FillEvent(
        provider_fill_id="f",
        venue="bitget",
        order_id=None,
        exchange_order_id="ex-1",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=Decimal(quantity),
        price=Decimal(price),
        fee=Decimal(fee),
        fee_currency=fee_currency,
        liquidity="TAKER",
        venue_ts=datetime.now(timezone.utc),
    )


def test_aggregate_empty_returns_zero() -> None:
    result = aggregate([])
    assert result.filled_qty == Decimal("0")
    assert result.avg_price == Decimal("0")
    assert result.fee_total == {}


def test_aggregate_single_fill_matches_its_price() -> None:
    result = aggregate([_fill("1", "100")])
    assert result.filled_qty == Decimal("1")
    assert result.avg_price == Decimal("100")


def test_aggregate_weighted_average_price() -> None:
    """0.5@100 + 0.5@200 => 평균 150."""
    result = aggregate([_fill("0.5", "100"), _fill("0.5", "200")])
    assert result.filled_qty == Decimal("1.0")
    assert result.avg_price == Decimal("150")


def test_aggregate_sums_fees_per_currency() -> None:
    result = aggregate(
        [
            _fill("1", "100", fee="0.1", fee_currency="USDT"),
            _fill("1", "100", fee="0.2", fee_currency="USDT"),
            _fill("1", "100", fee="0.001", fee_currency="BTC"),
        ]
    )
    assert result.fee_total["USDT"] == Decimal("0.3")
    assert result.fee_total["BTC"] == Decimal("0.001")
