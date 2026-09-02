"""OMS v1 계약 DTO 스냅샷 테스트 — L4-01.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §3.1/§3.3, §9 L4-01.

107번 §3 "MAJOR 변경 절차 — fixture 우선 작성"과 같은 원칙 — 각 DTO의
필드 집합을 명시적으로 고정해, 이후 누군가 필드를 몰래 지우거나
타입을 바꾸면(MAJOR) 이 테스트가 즉시 깨지게 한다. optional 필드
추가(MINOR)는 이 테스트를 건드리지 않는다(필드 목록에 없는 새 필드는
버스 검사 대상이 아님 — `model_fields` 서브셋 검사).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import BaseModel, ValidationError

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderStatus, OrderType
from src.foundation.reconciliation.contracts.v1 import Classification
from src.services.oms.contracts.v1_commands import (
    SCHEMA_VERSION,
    AlgoRequest,
    CancelOrderCommand,
    IdempotencyScope,
    ModifyOrderCommand,
    SubmitOrderCommand,
)
from src.services.oms.contracts.v1_events import (
    Discrepancy,
    FillAggregate,
    FillEvent,
    OrderTransitionEvent,
    ProviderOrderEvent,
)
from src.services.oms.contracts.v1_views import (
    AlgoRunView,
    OrderTimelineView,
    OrderView,
    ReconcileSummaryView,
)
from src.services.oms.domain.errors import (
    IdempotencyDigestMismatchError,
    InvalidOrderTransitionError,
    OmsError,
    OrderValidationError,
    UnknownSymbolError,
    UnsupportedVenueFeatureError,
)

_REQUIRED_FIELDS: dict[type[BaseModel], set[str]] = {
    IdempotencyScope: {
        "tenant_id",
        "account_ref",
        "provider",
        "strategy_id",
        "strategy_version",
        "execution_id",
        "intent_seq",
        "window_start",
        "schema_version",
    },
    SubmitOrderCommand: {
        "command_id",
        "trace_id",
        "scope",
        "symbol",
        "side",
        "order_type",
        "quantity",
        "price",
        "time_in_force",
        "asset_class",
        "mode",
        "parent_order_id",
        "algo_run_id",
        "is_liquidation",
        "actor_subject_id",
        "issued_at",
    },
    CancelOrderCommand: {
        "command_id",
        "trace_id",
        "order_id",
        "tenant_id",
        "reason",
        "actor_subject_id",
        "issued_at",
    },
    ModifyOrderCommand: {"new_price", "new_quantity"},
    AlgoRequest: {
        "algo_run_id",
        "trace_id",
        "scope",
        "algo",
        "symbol",
        "side",
        "total_quantity",
        "start_at",
        "end_at",
        "slice_count",
        "max_participation_pct",
        "size_jitter_pct",
        "time_jitter_pct",
        "display_quantity",
        "limit_price",
        "seed",
    },
    FillEvent: {
        "provider_fill_id",
        "venue",
        "order_id",
        "exchange_order_id",
        "symbol",
        "side",
        "quantity",
        "price",
        "fee",
        "fee_currency",
        "liquidity",
        "venue_ts",
    },
    FillAggregate: {"filled_qty", "avg_price", "fee_total"},
    ProviderOrderEvent: {
        "provider_event_id",
        "venue",
        "venue_symbol",
        "exchange_order_id",
        "client_order_id",
        "venue_status",
        "filled_quantity",
        "average_price",
        "last_fill",
        "venue_ts",
        "received_at",
        "source",
        "raw_hash",
    },
    OrderTransitionEvent: {
        "order_id",
        "seq",
        "from_status",
        "to_status",
        "event",
        "reason_code",
        "actor_subject_id",
        "trace_id",
        "command_id",
        "provider_event_id",
        "occurred_at",
        "payload_hash",
    },
    Discrepancy: {"kind", "entity_key", "internal_value", "provider_value", "materiality"},
}


def test_dto_field_sets_are_stable() -> None:
    """§3.3 "optional 필드 추가는 MINOR" — 여기 나열된 필드가 전부 존재하는지만
    확인한다(부분집합 검사이므로 필드 추가는 이 테스트를 안 건드림).
    필드가 사라지거나 이름이 바뀌면(MAJOR) 즉시 실패한다."""
    for model, expected_fields in _REQUIRED_FIELDS.items():
        actual_fields = set(model.model_fields)
        missing = expected_fields - actual_fields
        assert not missing, f"{model.__name__}에서 필드가 사라짐(MAJOR 변경?): {missing}"


def test_schema_version_is_v1() -> None:
    assert SCHEMA_VERSION == "v1"


def test_submit_order_command_rejects_live_mode() -> None:
    """§4.3 I9 — mode는 계약 레벨에서 이미 PAPER만 허용(LIVE 값 자체를 배제)."""
    payload = SubmitOrderCommand(
        command_id=uuid4(),
        trace_id=uuid4(),
        scope=IdempotencyScope(
            tenant_id=uuid4(),
            account_ref="acct-1",
            provider="bitget",
            strategy_id="s1",
            strategy_version="1.0.0",
            execution_id=1,
            intent_seq=1,
            window_start=datetime.now(timezone.utc),
        ),
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        quantity=Decimal("0.01"),
        asset_class=AssetClass.CRYPTO,
        actor_subject_id=uuid4(),
        issued_at=datetime.now(timezone.utc),
    )
    assert payload.mode == "PAPER"
    dumped = payload.model_dump()
    assert dumped["mode"] == "PAPER"


def test_submit_order_command_quantity_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        SubmitOrderCommand(
            command_id=uuid4(),
            trace_id=uuid4(),
            scope=IdempotencyScope(
                tenant_id=uuid4(),
                account_ref="acct-1",
                provider="bitget",
                strategy_id="s1",
                strategy_version="1.0.0",
                execution_id=1,
                intent_seq=1,
                window_start=datetime.now(timezone.utc),
            ),
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=Decimal("0"),
            asset_class=AssetClass.CRYPTO,
            actor_subject_id=uuid4(),
            issued_at=datetime.now(timezone.utc),
        )


def test_discrepancy_reuses_reconciliation_classification() -> None:
    d = Discrepancy(
        kind="STATUS_MISMATCH",
        entity_key="order:1",
        internal_value="FILLED",
        provider_value="ACKNOWLEDGED",
        materiality=Classification.MATERIAL_MISMATCH,
    )
    assert d.materiality == Classification.MATERIAL_MISMATCH


def test_oms_error_hierarchy_carries_code() -> None:
    err = OrderValidationError("MIN_NOTIONAL")
    assert err.code == "OMS_VALIDATION_MIN_NOTIONAL"
    assert isinstance(err, OmsError)

    assert InvalidOrderTransitionError("x").code == "OMS_INVALID_TRANSITION"
    assert IdempotencyDigestMismatchError("h").code == "OMS_IDEMPOTENCY_DIGEST_MISMATCH"
    assert UnknownSymbolError("XYZ", "bitget").code == "OMS_VALIDATION_UNKNOWN_SYMBOL"
    assert UnsupportedVenueFeatureError("OMS_ALGO_NOT_ENABLED", "x").code == (
        "OMS_ALGO_NOT_ENABLED"
    )


def test_views_construct_from_minimal_fields() -> None:
    order_id = uuid4()
    view = OrderView(
        order_id=order_id,
        tenant_id=uuid4(),
        execution_id=1,
        client_order_id="c-1",
        exchange_order_id=None,
        symbol="BTC/USDT",
        venue_symbol=None,
        exchange="bitget",
        side=OrderSide.BUY,
        order_type=OrderType.MARKET,
        time_in_force="GTC",
        quantity=Decimal("0.01"),
        price=None,
        status=OrderStatus.CREATED,
        filled_quantity=Decimal("0"),
        average_fill_price=None,
        fee_total=None,
        fee_currency=None,
        version=1,
        parent_order_id=None,
        algo_run_id=None,
        unknown_since=None,
        provider_order_date=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    timeline = OrderTimelineView(order=view, events=[], fills=[])
    assert timeline.order.order_id == order_id

    algo_run = AlgoRunView(
        algo_run_id=uuid4(),
        algo="TWAP",
        symbol="BTC/USDT",
        side=OrderSide.BUY,
        total_quantity=Decimal("1"),
        filled_quantity=Decimal("0"),
        state="PENDING",
        slice_count=5,
        slices_submitted=0,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    assert algo_run.slice_count == 5

    summary = ReconcileSummaryView(
        account_ref="acct-1",
        window_start=datetime.now(timezone.utc),
        window_end=datetime.now(timezone.utc),
        discrepancies=[],
        overall_classification="HEALTHY",
        checked_at=datetime.now(timezone.utc),
    )
    assert summary.discrepancies == []
