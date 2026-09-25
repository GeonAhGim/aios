"""Strategy Validation repository port. domain은 이 Protocol만 알고, 실제 구현
(adapters/)은 모른다(71번 §4)."""

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
        """STR-001/STR-007 — 같은 정확한 입력 조합의 기존 run이 있으면 그것을
        반환한다(재실행하지 않음). UNIQUE 제약(마이그레이션 3b244535b311)이
        이 조회와 아래 `create_run`의 경합을 스키마 레벨에서 보장한다(105번
        §2.2 "단일 소유자가 스키마 UNIQUE로 보장되는 경우")."""
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
        """QUEUED 상태로 새 run을 만든다. `get_run_by_snapshot`이 이미 있다고
        확인한 뒤에만 호출하는 게 아니라면, UNIQUE 위반 시
        ConcurrencyConflictError를 던진다(구현체 책임 — 105번 §2.2).

        L37 (§9 L37) -- `artifact_hash`/`policy_version`/`seed`/
        `data_snapshot_hash`/`trace_id` all have defaults, so a pre-L37
        caller (`start_validation.py`) that never passes them keeps working
        unchanged (107 §3.3 MINOR rule, same defaults as `domain/models.py`)."""
        ...

    async def mark_running(self, run_id: UUID) -> ValidationRun:
        """105번 표준 conditional_update로 QUEUED -> RUNNING."""
        ...

    async def mark_failed(self, run_id: UUID) -> ValidationRun:
        """RUNNING -> FAILED(105번 표준)."""
        ...

    async def complete_with_result(
        self, run_id: UUID, result: ValidationResult
    ) -> tuple[ValidationRun, ValidationResult]:
        """한 트랜잭션 안에서 RUNNING -> SUCCEEDED 전이 + result insert(76번 §1
        "append result revision; no overwrite" — result 테이블 자체가 WORM)."""
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
