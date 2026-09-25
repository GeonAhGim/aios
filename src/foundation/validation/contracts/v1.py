"""Strategy Validation Contract v1.

Spec: AIOSproject 46_strategy_package_and_validation_specification_v1.0.md,
76_strategy_package_validation_l3_build_and_operational_specification_v1.0.md §4,
107_contract_versioning_and_compatibility_standard_v1.0.md.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel

SCHEMA_VERSION = "v1"


class RunState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Outcome(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    PASS_WITH_OBLIGATIONS = "PASS_WITH_OBLIGATIONS"


class StartValidationCommand(BaseModel):
    """76 §4 `StartValidation`. `bars` is the actual content of 76 §1
    "input snapshot ref" — regardless of where the caller (router) fetches
    it (currently CredentialResolver + exchange adapter's get_ohlcv),
    this contract takes no dependency on that detail."""

    strategy_id: str
    strategy_version: str
    cost_model_fee_bps: Decimal
    cost_model_slippage_bps: Decimal
    warmup_bars: int
    periods_per_year: int
    initial_equity: Decimal


class ValidationResultView(BaseModel):
    run_id: UUID
    strategy_id: str
    strategy_version: str
    check_type: str
    state: RunState
    outcome: Outcome | None
    metrics: dict[str, Any] | None
    warnings: list[str]
    hard_fail_reasons: list[str]
    obligations: list[str]
    result_hash: str | None
    created_at: datetime
    # L37 (§9 L37) -- provenance pointers (`ValidationResult.evidence_refs`);
    # defaulted so pre-L37 callers (`start_validation.py`) that never pass it
    # keep constructing this view unchanged (107 §3.3 MINOR rule).
    evidence_refs: list[str] = []
    schema_version: str = SCHEMA_VERSION


class ValidationBundleView(BaseModel):
    """L37 (§9 L37) -- read view for `domain/models.ValidationBundle`, the
    outcome of evaluating one artifact's required checks together. No writer
    exists in this codebase yet (`application/build_bundle.py` is a later
    leaf) -- this view exists so `ports/repository.ValidationBundleRepository`
    has a stable wire shape to return once one does."""

    id: UUID
    artifact_hash: str
    policy_version: str
    data_snapshot_hash: str
    outcome: Outcome
    check_run_ids: list[UUID]
    bundle_hash: str
    hard_fail_reasons: list[str]
    obligations: list[str]
    created_at: datetime
    schema_version: str = SCHEMA_VERSION
