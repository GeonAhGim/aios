"""LB-3 — cost_basis_for selector 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-3
(파생상품은 method와 무관하게 WEIGHTED 강제, 현물은 method 기본 FIFO).
"""
from __future__ import annotations

from typing import cast

import pytest

from src.data.models.base import AssetClass
from src.foundation.positions.contracts.v1 import CostMethod
from src.foundation.positions.domain.cost_basis.fifo import FifoLots
from src.foundation.positions.domain.cost_basis.selector import (
    UnknownAssetClassError,
    cost_basis_for,
)
from src.foundation.positions.domain.cost_basis.weighted import WeightedAverage


@pytest.mark.parametrize(
    "asset_class",
    [
        AssetClass.CRYPTO,
        AssetClass.KR_EQUITY,
        AssetClass.KR_ETF,
        AssetClass.KR_ETN,
        AssetClass.US_EQUITY,
        AssetClass.US_ETF,
        AssetClass.US_ETN,
    ],
)
def test_spot_asset_class_defaults_to_fifo(asset_class: AssetClass) -> None:
    result = cost_basis_for(CostMethod.FIFO, asset_class)
    assert isinstance(result, FifoLots)


@pytest.mark.parametrize(
    "asset_class",
    [
        AssetClass.CRYPTO,
        AssetClass.KR_EQUITY,
        AssetClass.US_ETF,
    ],
)
def test_spot_asset_class_honors_weighted_method(asset_class: AssetClass) -> None:
    result = cost_basis_for(CostMethod.WEIGHTED, asset_class)
    assert isinstance(result, WeightedAverage)


@pytest.mark.parametrize(
    "asset_class",
    [
        AssetClass.KR_FUTURES,
        AssetClass.KR_OPTION,
        AssetClass.OVERSEAS_FUTURES,
        AssetClass.OVERSEAS_OPTION,
    ],
)
def test_derivative_asset_class_forces_weighted_regardless_of_method(
    asset_class: AssetClass,
) -> None:
    result = cost_basis_for(CostMethod.FIFO, asset_class)
    assert isinstance(result, WeightedAverage)


def test_unknown_asset_class_raises_instead_of_silent_fallback() -> None:
    unknown = cast(AssetClass, "PERPETUAL")
    with pytest.raises(UnknownAssetClassError):
        cost_basis_for(CostMethod.FIFO, unknown)
