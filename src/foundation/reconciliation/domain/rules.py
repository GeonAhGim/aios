"""Reconciliation & Resilience pure rule functions — must be unit-testable without DB/HTTP.

Spec: AIOSproject 80_reconciliation_resilience_l3_build_and_operational_specification_v1.0.md §1/§2.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from src.foundation.reconciliation.domain.models import Classification, MaterialityPolicy

# Apply §2 "Rules have ordered severity" (same principle as risk_gate/78) to aggregation
# as well — if any single item is more severe, the entire run/state gets that grade.
_SEVERITY_ORDER = (
    Classification.MATERIAL_MISMATCH,
    Classification.PROVIDER_UNAVAILABLE,
    Classification.MINOR_DIFFERENCE,
    Classification.HEALTHY,
)


def classify_item(
    internal_value: Decimal,
    provider_value: Decimal | None,
    policy: MaterialityPolicy,
) -> Classification:
    """80번 §1 "Missing/unreadable input yields ... never assumes zero
    balance/fill"(§2) — when provider_value is missing, immediately return
    PROVIDER_UNAVAILABLE; do not treat it as zero."""
    if provider_value is None:
        return Classification.PROVIDER_UNAVAILABLE

    diff = abs(internal_value - provider_value)
    if diff == 0:
        return Classification.HEALTHY

    relative_base = abs(internal_value) if internal_value != 0 else abs(provider_value)
    relative_diff_pct = (diff / relative_base * 100) if relative_base != 0 else Decimal(0)

    if diff <= policy.absolute_tolerance or relative_diff_pct <= policy.relative_tolerance_pct:
        return Classification.MINOR_DIFFERENCE
    return Classification.MATERIAL_MISMATCH


def aggregate_classification(items: tuple[Classification, ...]) -> Classification:
    if not items:
        return Classification.PENDING
    present = set(items)
    for candidate in _SEVERITY_ORDER:
        if candidate in present:
            return candidate
    return Classification.HEALTHY


def compute_input_hash(
    target_ref: str, entities: dict[str, tuple[str, str]]
) -> str:
    """REC-004/006 "concurrent scheduled/manual runs dedupe ... safe retry
    does not duplicate" — same target + same internal/provider value combo produces the
    same hash. `entities` is `{entity_key: (internal_value_str, provider_value_str)}` —
    serializing Decimal as string avoids hash instability from floating-point
    representation differences (caller passes str(Decimal(...)))."""
    payload = json.dumps({"target_ref": target_ref, "entities": entities}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
