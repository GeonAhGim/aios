"""거래소 capability 프로파일 단위테스트 — L4-04. DB 없음."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import AssetClass
from src.data.models.trading import OrderSide, OrderType
from src.services.oms.contracts.v1_commands import OrderIdempotencyScope, SubmitOrderCommand
from src.services.oms.domain.errors import OrderValidationError
from src.services.oms.domain.venue_profile import (
    TimeoutBudget,
    VenueCapabilityProfile,
    assert_supported,
)


def _profile(**overrides: object) -> VenueCapabilityProfile:
    defaults: dict[str, object] = {
        "venue": "bitget",
        "asset_classes": [AssetClass.CRYPTO],
        "order_types": {OrderType.MARKET, OrderType.LIMIT},
        "time_in_force": {"GTC", "IOC"},
        "supports_client_order_id": True,
        "client_order_id_max_len": 40,
        "client_order_id_charset": "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
        "id_policy": "STABLE",
        "supports_modify": True,
        "supports_cancel": "YES",
        "supports_ws_orders": True,
        "supports_batch": False,
        "price_tick": {},
        "qty_lot": {},
        "min_notional": {},
        "rate_limits": {},
        "submit_timeout": TimeoutBudget(),
        "query_timeout": TimeoutBudget(),
        "market_hours": None,
        "max_open_orders_per_symbol": 20,
        "verified": "DOC_ONLY",
    }
    defaults.update(overrides)
    return VenueCapabilityProfile.model_validate(defaults)


def _command(**overrides: object) -> SubmitOrderCommand:
    defaults: dict[str, object] = {
        "command_id": uuid4(),
        "trace_id": uuid4(),
        "scope": OrderIdempotencyScope(
            tenant_id=uuid4(),
            account_ref="acct-1",
            provider="bitget",
            strategy_id="s1",
            strategy_version="1.0.0",
            execution_id=1,
            intent_seq=1,
            window_start=datetime.now(timezone.utc),
        ),
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "order_type": OrderType.MARKET,
        "quantity": Decimal("0.01"),
        "price": None,
        "time_in_force": "GTC",
        "asset_class": AssetClass.CRYPTO,
        "actor_subject_id": uuid4(),
        "issued_at": datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    return SubmitOrderCommand(**defaults)  # type: ignore[arg-type]


def test_assert_supported_passes_for_supported_combination() -> None:
    assert_supported(_profile(), _command())  # 예외 없이 통과


def test_assert_supported_rejects_unsupported_asset_class() -> None:
    profile = _profile(asset_classes=[AssetClass.KR_EQUITY])
    with pytest.raises(OrderValidationError) as exc_info:
        assert_supported(profile, _command())
    assert exc_info.value.code == "OMS_VALIDATION_UNSUPPORTED_TYPE"


def test_assert_supported_rejects_unsupported_order_type() -> None:
    profile = _profile(order_types={OrderType.LIMIT})
    with pytest.raises(OrderValidationError) as exc_info:
        assert_supported(profile, _command(order_type=OrderType.MARKET))
    assert exc_info.value.code == "OMS_VALIDATION_UNSUPPORTED_TYPE"


def test_assert_supported_rejects_unsupported_tif() -> None:
    profile = _profile(time_in_force={"GTC"})
    with pytest.raises(OrderValidationError) as exc_info:
        assert_supported(profile, _command(time_in_force="FOK"))
    assert exc_info.value.code == "OMS_VALIDATION_UNSUPPORTED_TIF"


def test_assert_supported_rejects_trigger_price_not_wired() -> None:
    """QA(task-1961) — EM-19 trigger_price는 계약 필드로만 존재하고 submit_order
    실행 경로에 배선되지 않았다(I-10). 배선 전까지는 조용히 시장가/지정가로
    나가지 않도록 fail-closed로 거부해야 한다."""
    with pytest.raises(OrderValidationError) as exc_info:
        assert_supported(_profile(), _command(trigger_price=Decimal("100")))
    assert exc_info.value.code == "OMS_VALIDATION_TRIGGER_ORDER_NOT_WIRED"


def test_assert_supported_rejects_trailing_offset_not_wired() -> None:
    with pytest.raises(OrderValidationError) as exc_info:
        assert_supported(
            _profile(),
            _command(trigger_price=Decimal("100"), trailing_offset=Decimal("5")),
        )
    assert exc_info.value.code == "OMS_VALIDATION_TRIGGER_ORDER_NOT_WIRED"


def test_verified_field_records_confidence_level() -> None:
    """§10 정직 표기 — 라이브 미검증 값은 DOC_ONLY/ESTIMATED로 구분."""
    assert _profile(verified="ESTIMATED").verified == "ESTIMATED"
    assert _profile(verified="LIVE_VERIFIED").verified == "LIVE_VERIFIED"
