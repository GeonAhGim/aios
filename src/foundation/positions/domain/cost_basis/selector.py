"""LB-3 — 계좌 `cost_method`·자산군으로 원가법 구현 선택(selector).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-3
(`domain/cost_basis/selector.py`: "계좌 cost_method·자산군으로 구현
선택(파생상품은 가중평균 강제, 현물 기본 FIFO — Draft)"),
`unit/positions/test_cost_basis_selector.py`.

§4.3(`unit/positions/test_ports_protocol.py` 인접 규칙, 저널 불변조건 표
"현물(`asset_class ∈ {CRYPTO, *_EQUITY, *_ETF, *_ETN}`) 수량 ≥ 0")이
현물 자산군 집합을 명시하므로, 이 리프는 그 여집합(선물·옵션)을 파생상품
집합으로 다룬다 — `AssetClass`에 `PERPETUAL` 멤버는 존재하지 않는다(크립토
무기한선물은 이 스펙에서 `CRYPTO`=현물로 분류된다; **미검증** — 향후
크립토 파생 전용 asset_class가 추가되면 이 집합도 갱신해야 한다).

`AssetClass`는 닫힌 enum이라 현재는 두 집합의 합이 전체를 덮지만, 새
값이 추가돼 어느 집합에도 없는 상태로 들어오면 침묵으로 현물(FIFO)에
떨어뜨리지 않고 `UnknownAssetClassError`를 던진다 — 자산군 오분류가
원가법을 조용히 틀리게 만드는 사고를 막기 위함이다. 순수 도메인(I/O
import 0) — 반환값은 새로 생성한 [[fifo]]/[[weighted]] 인스턴스다.
"""
from __future__ import annotations

from src.data.models.base import AssetClass
from src.foundation.positions.contracts.v1 import CostMethod
from src.foundation.positions.domain.cost_basis.fifo import FifoLots
from src.foundation.positions.domain.cost_basis.weighted import WeightedAverage

CostBasis = FifoLots | WeightedAverage

_SPOT_ASSET_CLASSES: frozenset[AssetClass] = frozenset(
    {
        AssetClass.CRYPTO,
        AssetClass.KR_EQUITY,
        AssetClass.KR_ETF,
        AssetClass.KR_ETN,
        AssetClass.US_EQUITY,
        AssetClass.US_ETF,
        AssetClass.US_ETN,
    }
)

_DERIVATIVE_ASSET_CLASSES: frozenset[AssetClass] = frozenset(
    {
        AssetClass.KR_FUTURES,
        AssetClass.KR_OPTION,
        AssetClass.OVERSEAS_FUTURES,
        AssetClass.OVERSEAS_OPTION,
    }
)


class UnknownAssetClassError(ValueError):
    """현물·파생상품 어느 집합에도 속하지 않는 `asset_class` — 침묵
    fallback 금지, 호출자가 분류 규칙을 갱신해야 한다."""


def cost_basis_for(method: CostMethod, asset_class: AssetClass) -> CostBasis:
    """`method`·`asset_class`로 원가법 구현을 고른다.

    파생상품(`_DERIVATIVE_ASSET_CLASSES`)이면 계좌 `cost_method`와 무관하게
    `WeightedAverage`를 강제한다. 현물이면 `method`를 그대로 따른다(기본은
    호출부가 `CostMethod.FIFO`를 넘긴다).
    """
    if asset_class in _DERIVATIVE_ASSET_CLASSES:
        return WeightedAverage()
    if asset_class not in _SPOT_ASSET_CLASSES:
        raise UnknownAssetClassError(f"알 수 없는 asset_class: {asset_class!r}")

    if method is CostMethod.WEIGHTED:
        return WeightedAverage()
    return FifoLots()
