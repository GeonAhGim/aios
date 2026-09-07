"""BT-19 — backtest=live parity harness (enforces I-05).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.5
BT-19, ADR-2026-09-06-G §8, docs/design/INVARIANTS.md I-05 ("backtest and
live share the same compiled artifact and the same domain logic").

Neither DSL-11 (old-vs-new signal engine comparison) nor BT-9 (same-seed
reproducibility) diffs "fills that actually happened in PAPER" against
"fills the backtest replayed from the same artifact and window" — no leaf
actually verified I-05. This module owns that comparison: given two
sequences the caller has already obtained (the `FillEvent`s from the PAPER
execution trace, and the `SimulatedFill`s `run_backtest` replayed from the
same artifact/window), it diffs them item-by-item in order on all fields
except timestamp and reports the first point of divergence.

Pure comparison logic — no I/O. Where the PAPER trace comes from (e.g. a
`fills` table lookup, `src/services/oms/adapters/fills_repository.py`) and
how the replay itself is run (`run_backtest`) are not this module's
responsibility.

Values are compared by `Decimal` equality (equal values are treated as
identical even if their exponent representation differs) — the compared
fields (symbol/side/quantity/price/fee) are already structured types, so
differences in raw byte representation carry no meaning. Only the
timestamps (PAPER's `venue_ts`, the backtest's `timestamp`) are excluded
from comparison.

Fail-closed: if the lengths differ, the shorter length is immediately
reported as the divergence point (the tail is never silently ignored).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.models import SimulatedFill
from src.services.oms.contracts.v1_events import FillEvent

_COMPARED_FIELDS: Final = ("symbol", "side", "quantity", "price", "fee")
_LENGTH_MISMATCH_FIELD: Final = "__length__"


@dataclass(frozen=True)
class ComparableFill:
    """Canonical fill representation used for parity comparison — excludes
    timestamp."""

    symbol: str
    side: OrderSide
    quantity: Decimal
    price: Decimal
    fee: Decimal


def paper_fill_to_comparable(fill: FillEvent) -> ComparableFill:
    """One `FillEvent` from the PAPER execution trace -> canonical form for
    comparison."""
    return ComparableFill(
        symbol=fill.symbol,
        side=fill.side,
        quantity=fill.quantity,
        price=fill.price,
        fee=fill.fee,
    )


def backtest_fill_to_comparable(fill: SimulatedFill) -> ComparableFill:
    """One `SimulatedFill` from the backtest replay -> canonical form for
    comparison."""
    return ComparableFill(
        symbol=fill.symbol,
        side=fill.side,
        quantity=fill.quantity,
        price=fill.price,
        fee=fill.fee,
    )


@dataclass(frozen=True)
class Divergence:
    """First divergence point — always carried in the report to disclose
    where things went wrong."""

    index: int
    field: str
    paper_value: str
    backtest_value: str


@dataclass(frozen=True)
class ParityReport:
    is_match: bool
    paper_fill_count: int
    backtest_fill_count: int
    first_divergence: Divergence | None

    def raise_if_mismatch(self) -> None:
        """For using the harness as a gate (CI, etc.) — fails immediately
        with an exception on mismatch."""
        if not self.is_match:
            raise ParityMismatchError(self)


class ParityMismatchError(AssertionError):
    def __init__(self, report: ParityReport) -> None:
        self.report = report
        super().__init__(_format_mismatch(report))


def _format_mismatch(report: ParityReport) -> str:
    d = report.first_divergence
    if d is None:  # pragma: no cover — first_divergence always set when is_match=False
        return "parity mismatch: 발산 지점 없이 불일치로 보고됨(호출자 오류)"
    if d.field == _LENGTH_MISMATCH_FIELD:
        return (
            f"parity mismatch at index {d.index}: 체결 개수 불일치 "
            f"(paper={report.paper_fill_count}건, backtest={report.backtest_fill_count}건)"
        )
    return (
        f"parity mismatch at index {d.index}: field={d.field!r} "
        f"paper={d.paper_value!r} backtest={d.backtest_value!r}"
    )


def check_parity(
    paper_trace: Sequence[FillEvent],
    backtest_fills: Sequence[SimulatedFill],
) -> ParityReport:
    """Diffs the PAPER execution trace and the backtest replay fills item-
    by-item in order.

    Assumes both sequences target the same artifact and window (aligning
    them is the caller's responsibility — this function trusts the order
    and compares only by index). If any field differs within the common-
    length range, that index is the first divergence point; if the common
    range matches entirely but lengths differ, the shorter length is the
    divergence point.
    """
    paper = [paper_fill_to_comparable(f) for f in paper_trace]
    backtest = [backtest_fill_to_comparable(f) for f in backtest_fills]

    common_len = min(len(paper), len(backtest))
    for index in range(common_len):
        divergence = _first_field_divergence(index, paper[index], backtest[index])
        if divergence is not None:
            return ParityReport(
                is_match=False,
                paper_fill_count=len(paper),
                backtest_fill_count=len(backtest),
                first_divergence=divergence,
            )

    if len(paper) != len(backtest):
        return ParityReport(
            is_match=False,
            paper_fill_count=len(paper),
            backtest_fill_count=len(backtest),
            first_divergence=Divergence(
                index=common_len,
                field=_LENGTH_MISMATCH_FIELD,
                paper_value=str(len(paper)),
                backtest_value=str(len(backtest)),
            ),
        )

    return ParityReport(
        is_match=True,
        paper_fill_count=len(paper),
        backtest_fill_count=len(backtest),
        first_divergence=None,
    )


def _first_field_divergence(
    index: int, paper: ComparableFill, backtest: ComparableFill
) -> Divergence | None:
    for field in _COMPARED_FIELDS:
        paper_value = getattr(paper, field)
        backtest_value = getattr(backtest, field)
        if paper_value != backtest_value:
            return Divergence(
                index=index,
                field=field,
                paper_value=str(paper_value),
                backtest_value=str(backtest_value),
            )
    return None


__all__ = [
    "ComparableFill",
    "Divergence",
    "ParityMismatchError",
    "ParityReport",
    "backtest_fill_to_comparable",
    "check_parity",
    "paper_fill_to_comparable",
]
