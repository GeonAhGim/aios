"""StartValidation command — spec #76 §4. Implements what the `strategy_builder.py`
router-deviation-3 comment foretold: "once a backtest/validation pipeline exists,
wire it to transition_lifecycle() via an internal call path."

This function is the only path that can trigger the BACKTESTING -> VALIDATING
transition — there is still no way for a user to call that transition directly
(router deviation 3 stays as-is; this command invokes it internally instead).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.indicators.talib_adapter import IndicatorService
from src.core.observability.metric_names import (
    VALIDATION_RUN_COUNT_TOTAL,
    VALIDATION_RUN_DURATION_SECONDS,
)
from src.core.observability.metrics import MetricsPort, NullMetrics
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMStrategyConfig
from src.foundation.backtest.api import BacktestConfig, CostModel
from src.foundation.backtest.application.run_backtest import BacktestRunError, run_backtest
from src.foundation.validation.contracts.v1 import Outcome as ContractOutcome
from src.foundation.validation.contracts.v1 import RunState as ContractRunState
from src.foundation.validation.contracts.v1 import StartValidationCommand, ValidationResultView
from src.foundation.validation.domain.models import Outcome as DomainOutcome
from src.foundation.validation.domain.models import ValidationResult, ValidationRun
from src.foundation.validation.domain.rules import (
    compute_input_snapshot_hash,
    compute_result_hash,
    evaluate_validation_policy,
)
from src.foundation.validation.ports.repository import ValidationRepository
from src.services.strategy_builder_service import (
    StrategyBuilderService,
    StrategyLifecycleError,
)

logger = logging.getLogger(__name__)

CHECK_TYPE = "backtest"
"""Of the 6 checks in spec #76 §3, only the one FND-10 can actually compute
right now — see migration 3b244535b311's docstring."""


class StrategyNotEligibleForValidationError(Exception):
    """Raised when the strategy is not in BACKTESTING state (violates the
    9.9 absolute-principle ordering), or the caller is not the owner."""


class ValidationAlreadyInProgressError(Exception):
    """STR-007 "duplicate StartValidation uses one operation" — of concurrent
    requests with the exact same input, only one actually executes; the
    others receive this exception while that execution is still in flight
    (the caller surfaces it as a 409; retrying shortly after returns the
    completed result as-is)."""


def _run_to_view(
    run: ValidationRun,
    result: ValidationResult | None,
) -> ValidationResultView:
    assert run.created_at is not None  # run from DB is always NOT NULL (migration-guaranteed)
    return ValidationResultView(
        run_id=run.id,
        strategy_id=run.strategy_id,
        strategy_version=run.strategy_version,
        check_type=run.check_type,
        state=ContractRunState(run.state.value),
        outcome=None if result is None else ContractOutcome(result.outcome.value),
        metrics=None if result is None else result.metrics,
        warnings=[] if result is None else list(result.warnings),
        hard_fail_reasons=[] if result is None else list(result.hard_fail_reasons),
        obligations=[] if result is None else list(result.obligations),
        result_hash=None if result is None else result.result_hash,
        created_at=run.created_at,
        evidence_refs=[] if result is None else list(result.evidence_refs),
    )


async def start_validation(
    validation_repo: ValidationRepository,
    strategy_service: StrategyBuilderService,
    *,
    owner_user_id: UUID,
    command: StartValidationCommand,
    bars: list[Candle],
    indicator_service: IndicatorService | None = None,
    metrics: MetricsPort | None = None,
) -> ValidationResultView:
    # PLT-10 instrumentation point — defaults to NullMetrics, so existing callers
    # that don't pass `metrics` stay unaffected.
    metrics = metrics if metrics is not None else NullMetrics()
    started = time.monotonic()
    try:
        detail = await strategy_service.get_strategy(
            owner_user_id, command.strategy_id, command.strategy_version
        )
    except StrategyLifecycleError as exc:
        raise StrategyNotEligibleForValidationError(str(exc)) from exc

    cost_model_dict = {
        "fee_bps": str(command.cost_model_fee_bps),
        "slippage_bps": str(command.cost_model_slippage_bps),
    }
    snapshot_hash = compute_input_snapshot_hash(
        fsm_definition=detail.fsm_definition,
        cost_model=cost_model_dict,
        warmup_bars=command.warmup_bars,
        periods_per_year=command.periods_per_year,
        initial_equity=command.initial_equity,
        bars=bars,
    )

    # STR-001/STR-007 — for the exact same input, don't re-run; return the existing
    # result instead (idempotency). It matters that this lookup happens before the
    # BACKTESTING state check below — if the same request arrives again after the
    # strategy already succeeded and moved to VALIDATING (e.g. a network retry),
    # checking state first would reject it as "no longer BACKTESTING", breaking
    # true idempotency. Only when the cache lookup misses do we treat this as a
    # genuine new attempt and check state.
    existing_run = await validation_repo.get_run_by_snapshot(
        command.strategy_id, command.strategy_version, CHECK_TYPE, snapshot_hash
    )
    if existing_run is not None:
        existing_result = await validation_repo.get_result_for_run(existing_run.id)
        return _run_to_view(existing_run, existing_result)

    if detail.lifecycle_status != "BACKTESTING":
        raise StrategyNotEligibleForValidationError(
            f"전략이 BACKTESTING 상태가 아닙니다(현재: {detail.lifecycle_status}) — "
            "9.9 절대원칙 순서상 이 단계에서만 backtest 검증을 시작할 수 있습니다."
        )

    try:
        run = await validation_repo.create_run(
            strategy_id=command.strategy_id,
            strategy_version=command.strategy_version,
            check_type=CHECK_TYPE,
            input_snapshot_hash=snapshot_hash,
            cost_model=cost_model_dict,
            warmup_bars=command.warmup_bars,
            periods_per_year=command.periods_per_year,
            initial_equity=command.initial_equity,
        )
    except ConcurrencyConflictError as exc:
        # Between the get_run_by_snapshot lookup above and this create_run, another
        # request already created a run with the same input (spec #105 §2.2 "the
        # schema's UNIQUE constraint guarantees a single owner"). If that run has
        # already finished, return its result as-is; if it's still in progress,
        # signal "retry shortly".
        winner = await validation_repo.get_run_by_snapshot(
            command.strategy_id, command.strategy_version, CHECK_TYPE, snapshot_hash
        )
        if winner is None:  # pragma: no cover — must exist if a UNIQUE violation occurred
            raise
        winner_result = await validation_repo.get_result_for_run(winner.id)
        if winner_result is None:
            raise ValidationAlreadyInProgressError(
                f"{command.strategy_id}/{command.strategy_version}의 이 검증은 다른 "
                "요청이 이미 진행 중입니다 — 잠시 후 다시 시도하세요."
            ) from exc
        return _run_to_view(winner, winner_result)
    run = await validation_repo.mark_running(run.id)

    fsm_config = FSMStrategyConfig.model_validate(detail.fsm_definition)
    backtest_config = BacktestConfig(
        strategy_id=command.strategy_id,
        strategy_version=command.strategy_version,
        initial_equity=command.initial_equity,
        cost_model=CostModel(
            fee_bps=command.cost_model_fee_bps, slippage_bps=command.cost_model_slippage_bps
        ),
        warmup_bars=command.warmup_bars,
        periods_per_year=command.periods_per_year,
    )

    try:
        backtest_result = run_backtest(
            backtest_config, fsm_config, bars, indicator_service=indicator_service
        )
    except BacktestRunError as exc:
        await validation_repo.mark_failed(run.id)
        metrics.counter(VALIDATION_RUN_COUNT_TOTAL, {"outcome": "backtest_error"})
        metrics.observe(
            VALIDATION_RUN_DURATION_SECONDS,
            time.monotonic() - started,
            {"outcome": "backtest_error"},
        )
        logger.error(
            "validation_run_failed",
            extra={
                "event": "validation_run_failed",
                "duration_ms": round((time.monotonic() - started) * 1000),
                "payload": {
                    "run_id": str(run.id),
                    "strategy_id": str(command.strategy_id),
                    "reason": str(exc),
                },
            },
        )
        raise

    outcome, obligations, hard_fail_reasons = evaluate_validation_policy(backtest_result.warnings)
    metrics_payload = backtest_result.metrics.model_dump(mode="json")
    result = ValidationResult(
        id=uuid4(),
        run_id=run.id,
        outcome=DomainOutcome(outcome.value),
        metrics=metrics_payload,
        warnings=tuple(backtest_result.warnings),
        hard_fail_reasons=tuple(hard_fail_reasons),
        obligations=tuple(obligations),
        result_hash=compute_result_hash(metrics_payload),
        created_at=datetime.now(timezone.utc),
    )
    completed_run, saved_result = await validation_repo.complete_with_result(run.id, result)

    if outcome in (DomainOutcome.PASS, DomainOutcome.PASS_WITH_OBLIGATIONS):
        # spec #76 §2 "Only a successful required validation bundle can create
        # PAPER_ELIGIBLE" — within the current scope, this is replaced by
        # transitioning to the next lifecycle stage (VALIDATING) instead (see
        # the migration docstring).
        await strategy_service.transition_lifecycle(
            command.strategy_id, command.strategy_version, "VALIDATING"
        )

    elapsed = time.monotonic() - started
    metrics.counter(VALIDATION_RUN_COUNT_TOTAL, {"outcome": outcome.value})
    metrics.observe(VALIDATION_RUN_DURATION_SECONDS, elapsed, {"outcome": outcome.value})
    logger.info(
        "validation_run_completed",
        extra={
            "event": "validation_run_completed",
            "duration_ms": round(elapsed * 1000),
            "payload": {
                "run_id": str(run.id),
                "strategy_id": str(command.strategy_id),
                "outcome": outcome.value,
                "hard_fail_reason_count": len(hard_fail_reasons),
            },
        },
    )
    return _run_to_view(completed_run, saved_result)
