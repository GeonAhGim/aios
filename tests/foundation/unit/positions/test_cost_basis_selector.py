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


def test_derivative_forces_weighted_even_when_fifo_requested() -> None:
    """불변식 LB-3: 파생상품은 method=FIFO를 명시적으로 거부하고 WEIGHTED 강제."""
    for asset_class in (
        AssetClass.KR_FUTURES,
        AssetClass.KR_OPTION,
        AssetClass.OVERSEAS_FUTURES,
        AssetClass.OVERSEAS_OPTION,
    ):
        result = cost_basis_for(CostMethod.FIFO, asset_class)
        assert isinstance(result, WeightedAverage), (
            f"{asset_class}에 FIFO 요청 시 WEIGHTED 강제 위반"
        )


def test_unknown_asset_class_via_monkeypatch_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: _SPOT_ASSET_CLASSES에서 KR_EQUITY를 제거해 unknown으로
    만드는 시나리오. 실제 운영에서 asset_class 집합 정의가 누락되면
    UnknownAssetClassError가 raise 돼야 한다."""
    from src.foundation.positions.domain.cost_basis import selector as mod

    original = mod._SPOT_ASSET_CLASSES
    monkeypatch.setattr(
        mod,
        "_SPOT_ASSET_CLASSES",
        frozenset(cls for cls in original if cls is not AssetClass.KR_EQUITY),
    )
    with pytest.raises(UnknownAssetClassError):
        cost_basis_for(CostMethod.FIFO, AssetClass.KR_EQUITY)


def test_cost_basis_instances_are_fresh_on_each_call() -> None:
    """불변식: 매 호출마다 새 인스턴스를 반환한다 — 공유 가변 상태 금지."""
    result_a = cost_basis_for(CostMethod.FIFO, AssetClass.CRYPTO)
    result_b = cost_basis_for(CostMethod.FIFO, AssetClass.CRYPTO)
    assert result_a is not result_b


def test_derivative_rejects_fifo_with_weighted_forced() -> None:
    """불변식 LB-3 negative: 파생상품에 FIFO를 요청해도 WEIGHTED로 강제 —
    호출부가 FIFO를 명시했더라도 침묵으로 FIFO를 반환하면 안 된다."""
    for asset_class in (
        AssetClass.KR_FUTURES,
        AssetClass.KR_OPTION,
        AssetClass.OVERSEAS_FUTURES,
        AssetClass.OVERSEAS_OPTION,
    ):
        result = cost_basis_for(CostMethod.FIFO, asset_class)
        assert isinstance(result, WeightedAverage), (
            f"{asset_class}에 FIFO 요청 시 WEIGHTED 강제 위반"
        )
        # FifoLots 인스턴스가 반환되지 않았는지 명시적 확인
        assert not isinstance(result, FifoLots), (
            f"{asset_class}에 FifoLots 반환 — LB-3 파생상품 WEIGHTED 강제 위반"
        )


def test_selector_module_empty_spot_set_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패주입: _SPOT_ASSET_CLASSES 가 빈 집합이면 모든 현물 asset_class가
    UnknownAssetClassError를 일으켜야 한다(분류 규칙 누락 사고)."""
    from src.foundation.positions.domain.cost_basis import selector as mod

    monkeypatch.setattr(mod, "_SPOT_ASSET_CLASSES", frozenset())
    with pytest.raises(UnknownAssetClassError):
        cost_basis_for(CostMethod.FIFO, AssetClass.CRYPTO)


def test_weighted_average_init_raises_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패주입: WeightedAverage().__init__ 가 예외를 던지면 cost_basis_for 도
    그대로 전파해야 한다(의존성 고장 시 침묵 금지)."""
    monkeypatch.setattr(
        WeightedAverage,
        "__init__",
        lambda self: (_ for _ in ()).throw(RuntimeError("weighted init fail")),
    )
    with pytest.raises(RuntimeError, match="weighted init fail"):
        cost_basis_for(CostMethod.WEIGHTED, AssetClass.CRYPTO)


@pytest.mark.slow
def test_cost_basis_selector_performance() -> None:
    """성능 단언: 1000회 호출 시 p95 < 1ms (selector 순수 도메인, I/O 없음)."""
    import time

    iterations = 1000
    start = time.perf_counter_ns()
    for _ in range(iterations):
        cost_basis_for(CostMethod.FIFO, AssetClass.CRYPTO)
        cost_basis_for(CostMethod.WEIGHTED, AssetClass.US_EQUITY)
        cost_basis_for(CostMethod.FIFO, AssetClass.KR_FUTURES)
    elapsed_ms = (time.perf_counter_ns() - start) / 1e6
    p95_ms = elapsed_ms / (iterations // 2)  # rough p95 proxy
    assert p95_ms < 1.0, f"selector p95 {p95_ms:.3f}ms — 1ms 한도 초과"


def test_red_gate_lb3_invariant_end_to_end() -> None:
    """레드 게이트: LB-3 불변식 전체 재현 — 현물 FIFO/WEIGHTED 존중,
    파생상품 WEIGHTED 강제, 미분류 예외. 세 시나리오가 한 함수 호출에서
    동시에 성립하는지 검증."""
    # 1) 현물: FIFO 요청 → FifoLots
    assert isinstance(cost_basis_for(CostMethod.FIFO, AssetClass.KR_EQUITY), FifoLots)
    # 2) 현물: WEIGHTED 요청 → WeightedAverage
    assert isinstance(cost_basis_for(CostMethod.WEIGHTED, AssetClass.US_EQUITY), WeightedAverage)
    # 3) 파생: FIFO 요청 → 여전히 WeightedAverage
    assert isinstance(cost_basis_for(CostMethod.FIFO, AssetClass.KR_OPTION), WeightedAverage)
    # 4) 파생: WEIGHTED 요청 → WeightedAverage
    assert isinstance(
        cost_basis_for(CostMethod.WEIGHTED, AssetClass.OVERSEAS_FUTURES), WeightedAverage
    )
    # 5) 미분류: 예외
    unknown = cast(AssetClass, "NONEXISTENT")
    with pytest.raises(UnknownAssetClassError):
        cost_basis_for(CostMethod.FIFO, unknown)
