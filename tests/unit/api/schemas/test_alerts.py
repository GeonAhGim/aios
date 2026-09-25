"""FD-14 alerts 스키마 — AlertCreateRequest 검증 커버리지."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.api.schemas.alerts import AlertCreateRequest


def test_alert_create_request_defaults() -> None:
    req = AlertCreateRequest(
        exchange="bitget",
        symbol="BTC/USDT",
        indicator="RSI",
        operator="<",
        threshold=30,
    )
    assert req.timeframe == "1h"
    assert req.params == {}


def test_alert_create_request_explicit_fields() -> None:
    req = AlertCreateRequest(
        exchange="bitget",
        symbol="BTC/USDT",
        timeframe="4h",
        indicator="RSI",
        params={"timeperiod": 14},
        operator=">=",
        threshold=70.5,
    )
    assert req.timeframe == "4h"
    assert req.params == {"timeperiod": 14}
    assert req.threshold == 70.5


def test_alert_create_request_missing_required_field_raises() -> None:
    with pytest.raises(ValidationError):
        AlertCreateRequest.model_validate(
            {
                "exchange": "bitget",
                "symbol": "BTC/USDT",
                "indicator": "RSI",
                "threshold": 30,
            }
        )


def test_alert_create_request_invalid_operator_raises() -> None:
    with pytest.raises(ValidationError):
        AlertCreateRequest(
            exchange="bitget",
            symbol="BTC/USDT",
            indicator="RSI",
            operator="not-a-valid-operator",
            threshold=30,
        )


def test_alert_create_request_invalid_threshold_type_raises() -> None:
    with pytest.raises(ValidationError):
        AlertCreateRequest(
            exchange="bitget",
            symbol="BTC/USDT",
            indicator="RSI",
            operator="<",
            threshold="not-a-number",
        )


def test_alert_create_request_invalid_params_value_type_raises() -> None:
    with pytest.raises(ValidationError):
        AlertCreateRequest(
            exchange="bitget",
            symbol="BTC/USDT",
            indicator="RSI",
            params={"timeperiod": "not-an-int"},
            operator="<",
            threshold=30,
        )
