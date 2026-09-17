"""AI-10 -- Experiment Ledger contract v1.

Spec: docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.4 AI-10
(`Experiment{experiment_id, reproducibility_key, kind: backtest|sweep|
walk_forward|paper, inputs_hash, metrics, artifacts, parent_id, created_by}`),
§9 AI-10 DoD ("append-only proof").

`reproducibility_key` is never computed here -- it is BT-9's
`domain/reproducibility.py::reproducibility_key()` output for
backtest/sweep/walk_forward kinds (or the equivalent construction a future
leaf performs for `paper`-kind experiments); this contract only validates
its shape (64-char lowercase sha256 hex, same discipline as
`factory/contracts/v1.py::_validate_sha256_hex` and `gateway/contracts/
v1.py`'s own copy -- duplicated rather than imported, standard 71 §4
layering: contracts modules must not depend on each other). `inputs_hash`
is the same shape but a distinct value -- it digests the raw inputs one
specific run consumed (e.g. a walk-forward fold's train/test split), while
`reproducibility_key` identifies "would re-running this reproduce the same
result"; two experiments can legitimately share one without the other
(`domain/lineage.py` is where that relationship is enforced, not here).

`tenant_id` has no FK (same choice as `gateway/contracts/v1.py::AgentToken`
/ migration `0895391e36f5`'s `agent_token.tenant_id`) -- this leaf keeps that
precedent for consistency within `L4_ai_research_strategy_factory_v1.0`,
rather than the `research_data`/`ems` modules' `tenant(id)` FK convention.
"""

from __future__ import annotations

import enum
import re
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, model_validator

__all__ = ["SCHEMA_VERSION", "ExperimentKind", "Experiment"]

SCHEMA_VERSION: Literal["v1"] = "v1"

_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_sha256_hex(value: str, field_name: str) -> None:
    if not _SHA256_HEX_RE.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase sha256 hex digest (64 hex chars)")


class ExperimentKind(str, enum.Enum):
    BACKTEST = "backtest"
    SWEEP = "sweep"
    WALK_FORWARD = "walk_forward"
    PAPER = "paper"


class Experiment(BaseModel, frozen=True):
    """§2.4 AI-10 row verbatim field list, plus `tenant_id`/`created_at`
    (persistence metadata every foundation aggregate carries -- not spec-
    named, but required by the WORM table's tenant isolation and insertion-
    order columns)."""

    experiment_id: UUID
    tenant_id: UUID
    reproducibility_key: str
    kind: ExperimentKind
    inputs_hash: str
    metrics: dict[str, Any] = {}
    artifacts: tuple[str, ...] = ()
    parent_id: UUID | None = None
    created_by: UUID
    created_at: AwareDatetime
    schema_version: Literal["v1"] = SCHEMA_VERSION

    @model_validator(mode="after")
    def _check_invariants(self) -> Experiment:
        _validate_sha256_hex(self.reproducibility_key, "reproducibility_key")
        _validate_sha256_hex(self.inputs_hash, "inputs_hash")
        if self.parent_id is not None and self.parent_id == self.experiment_id:
            raise ValueError("parent_id must not reference the experiment itself")
        return self
