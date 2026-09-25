"""EM-14 -- application/compute_tca.py: assemble a TCA result and persist it.

Spec: docs/specs/L4_ems_routing_algos_and_tca_v1.0.md #2 module table
(`application/compute_tca.py` + `tca_results` storage + integration), #9
EM-14 (depends on EM-13 `decomposition.py`, EM-3 child-order settlement).

This leaf does not compute benchmark prices or cost components itself --
that stays EM-12 (`domain/tca/benchmarks.py`) and EM-13 (`domain/tca/
decomposition.py`)'s job. `compute_tca` only assembles their already-pure
outputs into the `TcaResult` contract shape (EM-1) and persists a
reproducible record (`TcaResultRepository.insert_or_get`) -- assembly,
persistence, and evidence-recording, nothing else, same division of
responsibility as EM-6's `route_order.py`.

Bridging `CostDecomposition`'s five dollar-cost fields (`spread_cost`,
`market_impact`, `delay_cost`, `fees`, `residual`) onto `TcaResult`'s five
bps fields (`arrival_bps`, `vwap_bps`, `impact_bps`, `fees_bps`,
`opportunity_bps`) is this leaf's own design decision (no external
"unverified fact" is involved, so no ratchet-allow applies):

- `impact_bps` = `market_impact` normalized to bps of arrival notional --
  the cost of executing at the realized fill VWAP instead of the arrival
  benchmark.
- `fees_bps` = `fees` normalized to bps of arrival notional.
- `opportunity_bps` = `delay_cost` normalized to bps of arrival notional --
  the cost of price drift between the realized fill VWAP and the close
  benchmark, i.e. the cost of not having finished sooner.
- `arrival_bps` is *constructed*, not independently benchmarked, as
  `impact_bps + fees_bps + opportunity_bps` -- this is the identity
  `tests/foundation/unit/ems/test_contracts_v1.py::
  test_tca_decomposition_identity` already pins at the contract level
  (0.01bps tolerance for `Decimal` division rounding).
- `vwap_bps` is a second, independent benchmark: the realized fill VWAP
  measured against the *market's* interval VWAP (volume-weighted over
  `bars`, reusing `benchmarks.compute_vwap` on synthetic `Fill(price=
  bar.close, qty=bar.volume)` entries rather than duplicating a
  volume-weighting formula here) -- a standard "did we beat the tape"
  metric that is deliberately not part of the arrival/impact/fees/
  opportunity reconciliation (§8: TCA decomposition sums to arrival, the
  VWAP benchmark is reported alongside it, not folded into it).

No caller-supplied price is trusted blindly: an arrival price that is not
strictly positive would divide the entire bps conversion by a
zero-or-negative notional, so it is rejected before any EM-12/EM-13 call
happens (fail-closed, EM_ALGO_CONSTRAINT). `revision` must be `>= 1`
(EM-A5: revision `1` is the first computation, `2+` are recomputes
following a data correction) -- `(parent_id, revision)` is the repository's
idempotency key (spec §5).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal
from typing import ClassVar
from uuid import UUID

from src.data.models.market_data import Candle
from src.data.models.trading import OrderSide
from src.foundation.ems.contracts.v1 import EmsErrorCode, TcaResult
from src.foundation.ems.domain.tca.benchmarks import Fill, arrival_price, close_price, compute_vwap
from src.foundation.ems.domain.tca.decomposition import decompose_cost
from src.foundation.ems.ports.tca_result_repository import TcaResultRecord, TcaResultRepository

_BPS_SCALE = Decimal(10000)
_SIDE_SIGN: dict[OrderSide, Decimal] = {
    OrderSide.BUY: Decimal("1"),
    OrderSide.SELL: Decimal("-1"),
}


class InvalidArrivalPriceError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- `price_at_arrival_ts` is not strictly
    positive.

    A non-positive arrival price would divide every bps figure by a
    zero-or-negative notional, silently producing a nonsensical or
    infinite TCA result instead of surfacing the bad input -- fail-closed,
    checked before any EM-12/EM-13 call.
    """

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT

    def __init__(self, price_at_arrival_ts: Decimal) -> None:
        super().__init__(
            f"price_at_arrival_ts must be > 0, got {price_at_arrival_ts} (EM_ALGO_CONSTRAINT)."
        )


class InvalidRevisionError(ValueError):
    """EM_ALGO_CONSTRAINT(400) -- `revision` is not `>= 1`.

    Spec §5: `(parent_id, revision)` is the idempotency key and revision
    `1` is the first computation -- a `0` or negative revision has no
    valid place in the recompute history (EM-A5).
    """

    code: ClassVar[EmsErrorCode] = EmsErrorCode.ALGO_CONSTRAINT

    def __init__(self, revision: int) -> None:
        super().__init__(f"revision must be >= 1, got {revision} (EM_ALGO_CONSTRAINT).")


def _market_interval_vwap(bars: Sequence[Candle]) -> Decimal:
    """The market's volume-weighted average price over `bars`, reusing
    `benchmarks.compute_vwap` on one synthetic `Fill` per bar instead of
    re-deriving a volume-weighting formula in this module."""
    return compute_vwap([Fill(price=bar.close, qty=bar.volume) for bar in bars])


async def compute_tca(
    repo: TcaResultRepository,
    *,
    parent_id: UUID,
    side: OrderSide,
    fills: Sequence[Fill],
    price_at_arrival_ts: Decimal,
    bars: Sequence[Candle],
    spread_cost: Decimal,
    fees: Decimal,
    total_cost: Decimal,
    revision: int,
    computed_at: datetime,
) -> TcaResultRecord:
    """Assemble a `TcaResult` from `fills`/`bars` (EM-12/EM-13) and persist
    it as revision `revision` of `parent_id`'s TCA history.

    Idempotent: a second call with the same `(parent_id, revision)`
    returns the previously stored record rather than creating a second row
    (DoD -- enforced by the repository's `insert_or_get`, not recomputed
    here, same contract as `route_order`).
    """
    if price_at_arrival_ts <= 0:
        raise InvalidArrivalPriceError(price_at_arrival_ts)
    if revision < 1:
        raise InvalidRevisionError(revision)

    arrival = arrival_price(price_at_arrival_ts)
    execution_vwap = compute_vwap(fills)
    close = close_price(bars)
    market_vwap = _market_interval_vwap(bars)

    decomposition = decompose_cost(
        fills=fills,
        side=side,
        arrival_price=arrival,
        vwap_price=execution_vwap,
        close_price=close,
        spread_cost=spread_cost,
        fees=fees,
        total_cost=total_cost,
    )

    total_qty = sum((fill.qty for fill in fills), start=Decimal("0"))
    notional = arrival * total_qty

    def _to_bps(amount: Decimal) -> Decimal:
        return amount / notional * _BPS_SCALE

    impact_bps = _to_bps(decomposition.market_impact)
    fees_bps = _to_bps(decomposition.fees)
    opportunity_bps = _to_bps(decomposition.delay_cost)
    arrival_bps = impact_bps + fees_bps + opportunity_bps

    sign = _SIDE_SIGN[side]
    vwap_bps = (execution_vwap - market_vwap) / market_vwap * sign * _BPS_SCALE

    result = TcaResult(
        arrival_bps=arrival_bps,
        vwap_bps=vwap_bps,
        impact_bps=impact_bps,
        fees_bps=fees_bps,
        opportunity_bps=opportunity_bps,
    )

    return await repo.insert_or_get(
        parent_id=parent_id,
        revision=revision,
        result=result,
        computed_at=computed_at,
    )
