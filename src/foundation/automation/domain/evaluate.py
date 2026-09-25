"""Pure condition evaluation — no I/O (mirrors `backtest/application`'s
TID251 "no asyncpg" discipline, even though this isn't backtest code).

Reuses `compare_value` (src.services.condition_evaluation) for the
">"/"<"/"crosses_above"/... semantics instead of redefining them — the same
function AlertService and PreviewCalculator already use.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from src.data.models.market_data import Candle
from src.foundation.automation.contracts.v1 import (
    Condition,
    DisclosureCondition,
    IndicatorCondition,
    PriceCondition,
    TimeCondition,
)
from src.services.condition_evaluation import compare_value

if TYPE_CHECKING:
    from src.foundation.automation.contracts.v1 import AutomationRule

__all__ = ["MarketSnapshot", "evaluate_condition", "evaluate_conditions", "evaluate_rule"]


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    """One symbol's snapshot at evaluation time — bar + precomputed indicators
    + the disclosure set at that moment.

    Indicators/disclosures are already-computed/collected values (keeps this
    a pure function) — computing/collecting them is the caller's
    responsibility (IndicatorService, a disclosure adapter)."""

    candle: Candle
    indicators: Mapping[str, Decimal] = field(default_factory=dict)
    disclosures: frozenset[str] = frozenset()


def _matches_time(condition: TimeCondition, at: datetime) -> bool:
    if at.tzinfo is None:
        raise ValueError("evaluate_condition: naive datetime은 허용하지 않는다")
    if condition.days_of_week is not None and at.weekday() not in condition.days_of_week:
        return False
    return at.time() == condition.at


def evaluate_condition(
    condition: Condition,
    snapshots: Mapping[str, MarketSnapshot],
    prev_snapshots: Mapping[str, MarketSnapshot],
) -> bool:
    """Evaluate a single condition. Missing data is treated as fail-closed
    (not triggered) — `None` is never read as "no condition" (same principle
    as I2)."""
    if isinstance(condition, TimeCondition):
        # TIME has no symbol — use whichever evaluated snapshot's timestamp.
        snapshot = next(iter(snapshots.values()), None)
        if snapshot is None:
            return False
        return _matches_time(condition, snapshot.candle.open_time)

    snapshot = snapshots.get(condition.symbol)
    if snapshot is None:
        return False
    prev = prev_snapshots.get(condition.symbol)

    if isinstance(condition, PriceCondition):
        value = getattr(snapshot.candle, condition.field.value)
        prev_value = getattr(prev.candle, condition.field.value) if prev is not None else None
        return compare_value(
            float(value),
            condition.operator,
            float(condition.threshold),
            float(prev_value) if prev_value is not None else None,
        )

    if isinstance(condition, IndicatorCondition):
        value = snapshot.indicators.get(condition.indicator)
        if value is None:
            return False
        prev_value = prev.indicators.get(condition.indicator) if prev is not None else None
        return compare_value(
            float(value),
            condition.operator,
            float(condition.threshold),
            float(prev_value) if prev_value is not None else None,
        )

    if isinstance(condition, DisclosureCondition):
        return condition.filing_type in snapshot.disclosures

    raise AssertionError(f"unhandled condition kind: {condition!r}")  # pragma: no cover


def evaluate_conditions(
    conditions: tuple[Condition, ...],
    snapshots: Mapping[str, MarketSnapshot],
    prev_snapshots: Mapping[str, MarketSnapshot],
) -> bool:
    """AND-compose all of a rule's conditions (OR/nested boolean is out of scope for this leaf)."""
    return all(evaluate_condition(c, snapshots, prev_snapshots) for c in conditions)


def evaluate_rule(
    rule: AutomationRule,
    snapshots: Mapping[str, MarketSnapshot],
    prev_snapshots: Mapping[str, MarketSnapshot],
) -> bool:
    return evaluate_conditions(rule.conditions, snapshots, prev_snapshots)
