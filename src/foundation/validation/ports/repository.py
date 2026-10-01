"""Strategy Validation repository port. The domain knows only this Protocol;
actual implementations (adapters/) remain hidden(§4 of leaf 71)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID

from src.foundation.validation.domain.models import (
    Outcome,
    ValidationBundle,
    ValidationResult,
    ValidationRun,
)


class ValidationRepository(Protocol):
    async def get_run_by_snapshot(
        self, strategy_id: str, strategy_version: str, check_type: str, input_snapshot_hash: str
    ) -> ValidationRun | None:
        """STR-001/STR-007 — Returns an existing run with the same exact input
        combination (avoids re-execution). The UNIQUE constraint (migration
        3b244535b311) guarantees at the schema level that this lookup and the
        `create_run` below never race(§2.2 of leaf 105: "when a single owner
        is guaranteed by schema UNIQUE")."""
        ...

    async def create_run(
        self,
        *,
        strategy_id: str,
        strategy_version: str,
        check_type: str,
        input_snapshot_hash: str,
        cost_model: dict[str, Any],
        warmup_bars: int,
        periods_per_year: int,
        initial_equity: Decimal,
        artifact_hash: str | None = None,
        policy_version: str = "vp-v1",
        seed: int = 0,
        data_snapshot_hash: str | None = None,
        trace_id: str | None = None,
    ) -> ValidationRun:
        """Creates a new run in QUEUED state. Unless the caller already confirmed
        via `get_run_by_snapshot` that none exists, a UNIQUE violation raises
        `ConcurrencyConflictError` (implementation's responsibility — §2.2 of
        leaf 105).

        L37 (§9 L37) -- `artifact_hash`/`policy_version`/`seed`/
        `data_snapshot_hash`/`trace_id` all have defaults, so a pre-L37
        caller (`start_validation.py`) that never passes them keeps working
        unchanged (107 §3.3 MINOR rule, same defaults as `domain/models.py`)."""
        ...

    async def mark_running(self, run_id: UUID) -> ValidationRun:
        """QUEUED -> RUNNING via the standard-105 conditional UPDATE."""
        ...

    async def mark_failed(self, run_id: UUID) -> ValidationRun:
        """RUNNING -> FAILED (standard-105 pattern)."""
        ...

    async def complete_with_result(
        self, run_id: UUID, result: ValidationResult
    ) -> tuple[ValidationRun, ValidationResult]:
        """RUNNING -> SUCCEEDED transition + result insert within a single
        transaction(§1 of leaf 76: "append result revision; no overwrite" —
        the result table itself is WORM)."""
        ...

    async def get_result_for_run(self, run_id: UUID) -> ValidationResult | None: ...


class ValidationBundleRepository(Protocol):
    """L37 (§9 L37) -- persistence for `domain/models.ValidationBundle`
    (migration M3's `strategy_validation_bundle` table). Separate Protocol
    from `ValidationRepository` because no caller in this codebase composes
    both yet (`application/build_bundle.py`, the writer, is a later leaf per
    §9 L43) -- keeping them apart means that leaf can depend on just this
    narrower port instead of the whole run/result surface."""

    async def get_bundle(
        self, artifact_hash: str, policy_version: str, data_snapshot_hash: str
    ) -> ValidationBundle | None:
        """76 §1 reproducibility/idempotency -- returns the existing bundle
        for this exact (artifact, policy, snapshot) combination instead of
        re-evaluating it. The UNIQUE constraint (migration 627bd92ec750)
        guarantees this lookup and `create_bundle` below never race at the
        schema level (105 §2.2)."""
        ...

    async def create_bundle(
        self,
        *,
        artifact_hash: str,
        policy_version: str,
        data_snapshot_hash: str,
        outcome: Outcome,
        check_run_ids: tuple[UUID, ...],
        bundle_hash: str,
        hard_fail_reasons: tuple[str, ...] = (),
        obligations: tuple[str, ...] = (),
    ) -> ValidationBundle:
        """Creates a new bundle. Unless the caller already confirmed via
        `get_bundle` that none exists, a UNIQUE violation raises
        `ConcurrencyConflictError` (implementation's responsibility -- same
        pattern as `ValidationRepository.create_run`). I6 (both directions)
        is enforced at construction time by `ValidationBundle.__post_init__`,
        not by this method."""
        ...
