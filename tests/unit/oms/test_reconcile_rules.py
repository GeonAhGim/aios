"""3자 대사 비교 규칙 단위테스트 — L4-05. DB 없음."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.foundation.reconciliation.contracts.v1 import Classification
from src.foundation.reconciliation.domain.models import MaterialityPolicy
from src.services.oms.contracts.v1_events import FillEvent
from src.services.oms.contracts.v1_views import OrderView
from src.services.oms.domain.reconcile_rules import classify, compare_triple

_POLICY = MaterialityPolicy(
    absolute_tolerance=Decimal("0.001"), relative_tolerance_pct=Decimal("0.1")
)


def _order_view(**overrides: object) -> OrderView:
    defaults: dict[str, object] = {
        "order_id": uuid4(),
        "tenant_id": uuid4(),
        "execution_id": 1,
        "client_order_id": "c-1",
        "exchange_order_id": "ex-1",
        "symbol": "BTC/USDT",
        "venue_symbol": "BTCUSDT",
        "exchange": "bitget",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "time_in_force": "GTC",
        "quantity": Decimal("1"),
        "price": None,
        "status": OrderStatus.FILLED,
        "filled_quantity": Decimal("1"),
        "average_fill_price": Decimal("100"),
        "fee_total": None,
        "fee_currency": None,
        "version": 1,
        "parent_order_id": None,
        "algo_run_id": None,
        "unknown_since": None,
        "provider_order_date": None,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return OrderView(**defaults)  # type: ignore[arg-type]


def test_matching_order_produces_no_discrepancy() -> None:
    order_id = uuid4()
    internal = [_order_view(order_id=order_id)]
    provider = [_order_view(order_id=order_id)]
    result = compare_triple(internal, provider, [], {}, {}, _POLICY)
    assert result == []


def test_order_missing_at_provider_is_flagged() -> None:
    internal = [_order_view()]
    result = compare_triple(internal, [], [], {}, {}, _POLICY)
    assert len(result) == 1
    assert result[0].kind == "ORDER_MISSING_AT_PROVIDER"
    assert classify(result[0]) == Classification.MATERIAL_MISMATCH


def test_order_missing_internal_is_flagged() -> None:
    provider = [_order_view()]
    result = compare_triple([], provider, [], {}, {}, _POLICY)
    assert len(result) == 1
    assert result[0].kind == "ORDER_MISSING_INTERNAL"


def test_status_mismatch_is_flagged() -> None:
    order_id = uuid4()
    internal = [_order_view(order_id=order_id, status=OrderStatus.ACKNOWLEDGED)]
    provider = [_order_view(order_id=order_id, status=OrderStatus.FILLED)]
    result = compare_triple(internal, provider, [], {}, {}, _POLICY)
    kinds = [d.kind for d in result]
    assert "STATUS_MISMATCH" in kinds


def test_filled_qty_mismatch_is_flagged() -> None:
    order_id = uuid4()
    internal = [_order_view(order_id=order_id, filled_quantity=Decimal("1"))]
    provider = [_order_view(order_id=order_id, filled_quantity=Decimal("0.5"))]
    result = compare_triple(internal, provider, [], {}, {}, _POLICY)
    kinds = [d.kind for d in result]
    assert "FILLED_QTY_MISMATCH" in kinds


def test_fill_missing_internal_when_order_unknown() -> None:
    fill = FillEvent(
        provider_fill_id="f-1",
        venue="bitget",
        order_id=None,
        exchange_order_id="ex-999",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        quantity=Decimal("1"),
        price=Decimal("100"),
        fee=Decimal("0"),
        fee_currency="USDT",
        liquidity="TAKER",
        venue_ts=datetime.now(timezone.utc),
    )
    result = compare_triple([], [], [fill], {}, {}, _POLICY)
    assert any(d.kind == "FILL_MISSING_INTERNAL" for d in result)


def test_balance_mismatch_is_flagged() -> None:
    result = compare_triple(
        [],
        [],
        [],
        balances={"USDT": Decimal("100")},
        ledger_balances={"USDT": Decimal("90")},
        policy=_POLICY,
    )
    assert any(d.kind == "BALANCE_MISMATCH" for d in result)


def test_balance_missing_from_provider_is_unavailable_not_zero() -> None:
    """I11(80번 §2) — provider 값이 없으면 0으로 단정하지 않는다."""
    result = compare_triple(
        [], [], [], balances={}, ledger_balances={"USDT": Decimal("90")}, policy=_POLICY
    )
    assert len(result) == 1
    assert result[0].kind == "BALANCE_MISMATCH"
    assert classify(result[0]) == Classification.PROVIDER_UNAVAILABLE


def test_balance_matching_within_tolerance_is_not_flagged() -> None:
    result = compare_triple(
        [],
        [],
        [],
        balances={"USDT": Decimal("100")},
        ledger_balances={"USDT": Decimal("100")},
        policy=_POLICY,
    )
    assert result == []
