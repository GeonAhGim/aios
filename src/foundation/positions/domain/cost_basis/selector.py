"""LB-3 — Select cost-basis implementation by account `cost_method` and asset class.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LB-3
(`domain/cost_basis/selector.py`: "Select implementation by account
cost_method and asset class (derivatives force weighted average, spot defaults
to FIFO — Draft)"),
`unit/positions/test_cost_basis_selector.py`.

§4.3 (`unit/positions/test_ports_protocol.py` adjacent rule, journal invariant table
"spot (`asset_class ∈ {CRYPTO, *_EQUITY, *_ETF, *_ETN}`) quantity ≥ 0")
explicitly defines the spot asset-class set, so this leaf treats its complement
(futures · options) as the derivatives set — `AssetClass` has no `PERPETUAL`
member (crypto perpetuals are classified as `CRYPTO`=spot in this spec;
**unverified** — if a crypto-derivatives-only asset_class is added later, this
set must be updated).

`AssetClass` is a closed enum so the two sets currently cover the whole space,
but if a new value arrives that belongs to neither set, we raise
`UnknownAssetClassError` instead of silently falling back to spot (FIFO) — to
prevent misclassification from silently choosing the wrong cost-basis method.
Pure domain (0 I/O imports) — returns a freshly created [[fifo]] or [[weighted]]
instance.
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
    """`asset_class` does not belong to either the spot or derivatives set —
    silent fallback is forbidden; the caller must update the classification rules."""


def cost_basis_for(method: CostMethod, asset_class: AssetClass) -> CostBasis:
    """Select the cost-basis implementation by `method` and `asset_class`.

    For derivatives (`_DERIVATIVE_ASSET_CLASSES`), force `WeightedAverage`
    regardless of the account `cost_method`. For spot, follow `method` as-is
    (the caller typically passes `CostMethod.FIFO`).
    """
    if asset_class in _DERIVATIVE_ASSET_CLASSES:
        return WeightedAverage()
    if asset_class not in _SPOT_ASSET_CLASSES:
        raise UnknownAssetClassError(f"알 수 없는 asset_class: {asset_class!r}")

    if method is CostMethod.WEIGHTED:
        return WeightedAverage()
    return FifoLots()
