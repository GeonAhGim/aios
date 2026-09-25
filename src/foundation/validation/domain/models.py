"""Strategy Validation 도메인 모델 — pure value object.

Spec: AIOSproject 76_strategy_package_validation_l3_build_and_operational_
specification_v1.0.md §1/§3;
docs/specs/L4_strategy_portfolio_backtest_v1.0.md §2 "existing" domain/
models.py row / §9 L37 for the artifact-linkage fields and `ValidationBundle`
added below -- migration M3 (`627bd92ec750`) and the matching
`ports/repository.py`/`contracts/v1.py`/`adapters/postgres_repository.py`
wiring persist and expose these value objects.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID


class RunState(str, Enum):
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class Outcome(str, Enum):
    """76번 §3 "PASS, FAIL, PASS_WITH_OBLIGATIONS만 반환한다."""

    PASS = "PASS"
    FAIL = "FAIL"
    PASS_WITH_OBLIGATIONS = "PASS_WITH_OBLIGATIONS"


@dataclass(frozen=True)
class ValidationRun:
    id: UUID
    strategy_id: str
    strategy_version: str
    check_type: str
    input_snapshot_hash: str
    cost_model: dict[str, Any]
    warmup_bars: int
    periods_per_year: int
    initial_equity: Decimal
    state: RunState
    created_at: datetime | None = None
    completed_at: datetime | None = None
    # L37 artifact-linkage fields (§9 L37) -- all optional/defaulted so the
    # pre-L37 single-check callers (`start_validation.py`,
    # `postgres_repository.py`'s `_row_to_run`) that never pass these keep
    # constructing a `ValidationRun` unchanged until the M3 migration and
    # repository update land.
    artifact_hash: str | None = None
    policy_version: str = "vp-v1"
    seed: int = 0
    data_snapshot_hash: str | None = None
    trace_id: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    id: UUID
    run_id: UUID
    outcome: Outcome
    metrics: dict[str, Any]
    warnings: tuple[str, ...] = field(default_factory=tuple)
    hard_fail_reasons: tuple[str, ...] = field(default_factory=tuple)
    obligations: tuple[str, ...] = field(default_factory=tuple)
    result_hash: str = ""
    created_at: datetime | None = None
    # L37 (§9 L37) -- provenance pointers a `run_check` persists alongside a
    # result (e.g. "snapshot:<hash>", "artifact:<hash>", "audit:<event_id>"),
    # mirrored from `CheckResult.evidence_refs` (domain/check_result.py).
    evidence_refs: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ValidationBundle:
    """L4_strategy_portfolio_backtest_v1.0.md §9 L37 / L43 -- the outcome of
    evaluating one artifact's required checks together (`rules.
    evaluate_bundle`), persisted once per (artifact_hash, policy_version,
    data_snapshot_hash) (migration M3's `strategy_validation_bundle` UNIQUE
    constraint).

    I6 ("a non-empty hard_fail_reasons means outcome must be FAIL", both
    directions per §4.1 I6 -- migration M3's DB `CHECK` only enforces the
    forward direction, the reverse is code-only) is enforced
    here at construction so a bundle built by hand (a test fixture, a row
    reconstructed from a future repository) can never silently mislabel a
    hard-failing bundle as PASS, or a FAIL bundle with no recorded reason.
    """

    id: UUID
    artifact_hash: str
    policy_version: str
    data_snapshot_hash: str
    outcome: Outcome
    check_run_ids: tuple[UUID, ...]
    bundle_hash: str
    hard_fail_reasons: tuple[str, ...] = field(default_factory=tuple)
    obligations: tuple[str, ...] = field(default_factory=tuple)
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.hard_fail_reasons and self.outcome is not Outcome.FAIL:
            raise ValueError(
                "I6 violation: hard_fail_reasons is non-empty but outcome is "
                f"{self.outcome!r}, not FAIL"
            )
        if self.outcome is Outcome.FAIL and not self.hard_fail_reasons:
            raise ValueError("I6 violation: outcome is FAIL but hard_fail_reasons is empty")
