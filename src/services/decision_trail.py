"""Decision trail entry model (J8 trail).

Each row in the trail represents a single risk / execution decision
recorded during an execution's lifetime.  This dataclass is a
lightweight, serialisable view — the authoritative schema lives in
``src/core/risk/decision.py`` (``RiskDecision``).
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


@dataclass(frozen=True)
class RiskTrailEntry:
    """One decision recorded in the trail.

    Fields mirror ``RiskDecision`` but ``kind`` is a plain ``str``
    (``GateKind`` value) so the API response schema stays simple.
    """

    decision_id: UUID
    kind: str  # GateKind value string
    outcome: str  # RiskOutcome value string
    reason_codes: tuple[str, ...]
    rule_version: str
    evaluated_at: datetime
    expires_at: datetime
    trace_id: UUID
    # Human-readable summary derived from rule_results / obligations.
    summary: str


class TrailKind(StrEnum):
    CONVERSION = "CONVERSION"
    EXPANSION = "EXPANSION"
    RISK_DECISION = "RISK_DECISION"


class TrailEntry(BaseModel, frozen=True):
    """A chronological view of recorded decisions, never an authorization."""

    kind: TrailKind
    occurred_at: datetime
    actor: str | None
    summary: dict[str, Any]
    evidence: dict[str, Any] | None
    unavailable: dict[str, str] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def require_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        return value.astimezone(UTC)


_CONVERSION_KEYS = (
    "strategy_id",
    "strategy_version",
    "new_execution_id",
    "target_capital",
    "allocated_capital",
    "max_loss_amount",
    "initial_exposure_pct",
    "max_drawdown_pct",
)
_EXPANSION_KEYS = (
    "allocated_capital_before",
    "allocated_capital_after",
    "exposure_pct_after",
    "max_drawdown_pct_after",
)
_RISK_KEYS = (
    "decision_id",
    "gate_kind",
    "outcome",
    "reason_codes",
    "rule_version",
    "evaluated_at",
    "expires_at",
    "trace_id",
    "summary",
)
_ACTIONS = {
    "execution.converted_to_live": (TrailKind.CONVERSION, _CONVERSION_KEYS),
    "execution.exposure_expanded": (TrailKind.EXPANSION, _EXPANSION_KEYS),
}
_KIND_ORDER = {kind: index for index, kind in enumerate(TrailKind)}


def _summary(
    data: Mapping[str, Any],
    keys: tuple[str, ...],
    unavailable: dict[str, str],
) -> dict[str, Any]:
    for key in keys:
        if data.get(key) is None:
            unavailable[key] = "기록 없음"
    return {key: deepcopy(data.get(key)) for key in keys}


def _risk_summary(
    row: Mapping[str, Any], unavailable: dict[str, str],
) -> dict[str, Any]:
    """Preserve recorded risk summaries or derive them from reason codes."""
    summary = _summary(row, _RISK_KEYS, unavailable)
    # Derive summary string from reason_codes when not provided.
    if summary.get("summary") is None:
        codes = row.get("reason_codes")
        if isinstance(codes, (list, tuple)) and len(codes) > 0:
            summary["summary"] = ", ".join(str(c) for c in codes)
        else:
            summary["summary"] = ""
        unavailable.pop("summary", None)
    return summary


def build_decision_trail(
    audit_rows: Sequence[Mapping[str, Any]],
    risk_rows: Sequence[Mapping[str, Any]],
) -> list[TrailEntry]:
    """Merge audit and risk snapshots without I/O or modifying input rows."""
    entries: list[TrailEntry] = []
    for row in audit_rows:
        action = _ACTIONS.get(row.get("action_type", ""))
        if action is None:
            continue
        kind, keys = action
        unavailable: dict[str, str] = {}
        data = row.get("decision_data")
        if isinstance(data, str):
            try:
                data = json.loads(data, parse_float=Decimal)
            except (ValueError, RecursionError):
                data = None
                unavailable["decision_data"] = "파싱 실패"
        if not isinstance(data, Mapping):
            unavailable.setdefault("decision_data", "기록 없음" if data is None else "파싱 실패")
            data = {}
        evidence = data.get("readiness_evidence")
        if not isinstance(evidence, dict):
            evidence = None
            if kind == TrailKind.CONVERSION:
                unavailable["readiness_evidence"] = "기록 없음"
        entries.append(
            TrailEntry(
                kind=kind,
                occurred_at=row["created_at"],
                actor=row.get("actor_agent"),
                summary=_summary(data, keys, unavailable),
                evidence=deepcopy(evidence),
                unavailable=unavailable,
            )
        )
    for row in risk_rows:
        unavailable = {}
        summary = _risk_summary(row, unavailable)
        entries.append(
            TrailEntry(
                kind=TrailKind.RISK_DECISION,
                occurred_at=row["evaluated_at"],
                actor=None,
                summary=summary,
                evidence=None,
                unavailable=unavailable,
            )
        )
    return sorted(entries, key=lambda entry: (entry.occurred_at, _KIND_ORDER[entry.kind]))
