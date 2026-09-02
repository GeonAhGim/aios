"""L4_risk_and_safety_v1.0.md#2.1, #3.2 — 노출 한도 값 객체 테스트.

`check_exposure_limits(inputs, limits)`(R-14)는 아직 없다 — 이 파일은
값 객체(`ExposureLimit`/`LimitScope`/`LimitMetric`)만 검증한다.
"""
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.core.risk.limits import ExposureLimit, LimitMetric, LimitScope


def test_exposure_limit_is_frozen():
    limit = ExposureLimit(
        scope=LimitScope.SYMBOL,
        scope_ref="BTC/USDT",
        metric=LimitMetric.GROSS_NOTIONAL_PCT,
        limit_value=Decimal("20"),
        hard=True,
        limit_id=uuid4(),
    )
    with pytest.raises(ValidationError):
        limit.hard = False  # type: ignore[misc]


def test_limit_scope_values():
    assert {s.value for s in LimitScope} == {
        "TENANT",
        "ACCOUNT",
        "STRATEGY",
        "SYMBOL",
        "ASSET_CLASS",
        "PROVIDER",
    }


def test_limit_metric_values():
    assert {m.value for m in LimitMetric} == {
        "GROSS_NOTIONAL_PCT",
        "NET_NOTIONAL_PCT",
        "MAX_ORDER_NOTIONAL",
        "MAX_OPEN_POSITIONS",
        "MAX_TRADES_PER_HOUR",
        "MAX_LEVERAGE",
    }


def test_invalid_scope_rejected():
    with pytest.raises(ValidationError):
        ExposureLimit(
            scope="NOT_A_SCOPE",  # type: ignore[arg-type]
            scope_ref="BTC/USDT",
            metric=LimitMetric.GROSS_NOTIONAL_PCT,
            limit_value=Decimal("20"),
            hard=True,
            limit_id=uuid4(),
        )
