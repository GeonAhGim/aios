"""LB-6 — Assemble supplier reconciliation rules (reconciliation_rules).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.4, §9 LB-6,
FND-08 `src/foundation/reconciliation/**`.

Assemble internal ledger values and supplier (exchange) responses into
FND-08 `EntitySnapshot` (shared reconciliation contract). Actual
classification (HEALTHY/MINOR_DIFFERENCE/MATERIAL_MISMATCH, etc.) is the
responsibility of FND-08 `domain.rules.classify_item`; this leaf only
produces the inputs (single responsibility). Pure functions only — no
direct I/O or clock calls.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.foundation.reconciliation.contracts.v1 import EntitySnapshot


@dataclass(frozen=True, slots=True)
class InternalEntityValue:
    """A typed value looked up from the internal ledger. `entity_key` must
    be unique within the `build_entity_snapshots` input list."""

    entity_type: str
    entity_key: str
    value: Decimal


def build_entity_snapshots(
    internal: Sequence[InternalEntityValue],
    provider: Mapping[str, Decimal | None],
) -> list[EntitySnapshot]:
    """Pair internal value list with provider value map by `entity_key`
    to produce a list of `EntitySnapshot`.

    When `entity_key` is entirely absent from `provider` (key missing),
    `provider_value` is `None` — this distinguishes the supplier omitting
    the item from its response versus the value being 0 (FND-08 §2
    "do not interpret as zero"); use `.get()` and never supply a default
    of 0 for missing keys.

    Duplicate `entity_key` in `internal` is a caller bug (ambiguity in
    reconciliation targets) — raises `ValueError`.
    """
    seen: set[str] = set()
    snapshots: list[EntitySnapshot] = []
    for item in internal:
        if item.entity_key in seen:
            raise ValueError(f"duplicate entity_key: {item.entity_key!r}")
        seen.add(item.entity_key)
        snapshots.append(
            EntitySnapshot(
                entity_type=item.entity_type,
                entity_key=item.entity_key,
                internal_value=item.value,
                provider_value=provider.get(item.entity_key),
            )
        )
    return snapshots


def break_age(detected_at: datetime, now: datetime) -> timedelta:
    """Elapsed time since the break was detected (evidence metric for
    §8.4 "surface within minutes of occurrence"). Both timestamps must be
    tz-aware — a naive datetime signals a misinterpretation of exchange or
    storage response, not a valid input."""
    if detected_at.tzinfo is None:
        raise ValueError("detected_at must be timezone-aware")
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now - detected_at
