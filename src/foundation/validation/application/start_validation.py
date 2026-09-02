"""StartValidation 커맨드 — 76번 §4. `strategy_builder.py` 라우터 편차 3
주석이 예고한 "백테스트/검증 파이프라인이 생기면 내부 호출 경로로
transition_lifecycle()에 연결"을 실제로 구현한다.

이 함수가 유일하게 BACKTESTING -> VALIDATING 전이를 트리거할 수 있는
경로다 — 사용자가 그 전이를 직접 호출할 방법은 여전히 없다(라우터
편차 3 그대로 유지, 이 커맨드가 대신 내부에서 호출).
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

from src.core.db.conditional_write import ConcurrencyConflictError
from src.core.indicators.talib_adapter import IndicatorService
from src.data.models.market_data import Candle
from src.data.models.strategy_fsm import FSMStrategyConfig
from src.foundation.backtest.application.run_backtest import BacktestRunError, run_backtest
from src.foundation.backtest.domain.models import BacktestConfig, CostModel
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

CHECK_TYPE = "backtest"
"""76번 §3의 6개 체크 중 지금 FND-10이 실제로 계산 가능한 것 하나만 —
migration 3b244535b311 docstring 참조."""


class StrategyNotEligibleForValidationError(Exception):
    """전략이 BACKTESTING 상태가 아니거나(9.9 절대원칙 순서 위반), 소유자가
    아니다."""


class ValidationAlreadyInProgressError(Exception):
    """STR-007 "duplicate StartValidation uses one operation" — 동시에 들어온
    같은 정확한 요청 중 하나만 실제로 실행되고, 나머지는 그 실행이 아직
    끝나지 않은 상태에서 이 예외를 받는다(호출부가 409로 안내, 잠시 후
    재시도하면 완료된 결과를 그대로 받는다)."""


def _run_to_view(
    run: ValidationRun,
    result: ValidationResult | None,
) -> ValidationResultView:
    assert run.created_at is not None  # DB에서 온 run은 항상 NOT NULL(마이그레이션 보장)
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
    )


async def start_validation(
    validation_repo: ValidationRepository,
    strategy_service: StrategyBuilderService,
    *,
    owner_user_id: UUID,
    command: StartValidationCommand,
    bars: list[Candle],
    indicator_service: IndicatorService | None = None,
) -> ValidationResultView:
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

    # STR-001/STR-007 — 같은 정확한 입력이면 재실행하지 않고 기존 결과를 그대로
    # 반환한다(멱등성). 이 조회를 아래 BACKTESTING 상태 검사보다 먼저 하는 게
    # 중요하다 — 이미 성공해서 전략이 VALIDATING으로 넘어간 뒤에 같은 요청이
    # 다시 오면(네트워크 재시도 등), 상태 검사를 먼저 하면 "이제 BACKTESTING이
    # 아니다"로 거부돼버려 진짜 멱등성이 깨진다. 캐시를 못 찾았을 때만
    # "새로 만들려는 시도"로 보고 상태를 검사한다.
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
        # 위 get_run_by_snapshot 조회와 이 create_run 사이에 다른 요청이 먼저
        # 같은 입력으로 run을 만들었다(105번 §2.2 "스키마 UNIQUE 제약이 단일
        # 소유자를 보장"). 그 run이 이미 끝났으면 결과를 그대로 돌려주고,
        # 아직 진행 중이면 "잠시 후 다시 시도" 신호를 준다.
        winner = await validation_repo.get_run_by_snapshot(
            command.strategy_id, command.strategy_version, CHECK_TYPE, snapshot_hash
        )
        if winner is None:  # pragma: no cover — UNIQUE 위반이 났다면 반드시 존재해야 함
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
    except BacktestRunError:
        await validation_repo.mark_failed(run.id)
        raise

    outcome, obligations = evaluate_validation_policy(backtest_result.warnings)
    metrics = backtest_result.metrics.model_dump(mode="json")
    result = ValidationResult(
        id=uuid4(),
        run_id=run.id,
        outcome=DomainOutcome(outcome.value),
        metrics=metrics,
        warnings=tuple(backtest_result.warnings),
        hard_fail_reasons=(),
        obligations=tuple(obligations),
        result_hash=compute_result_hash(metrics),
        created_at=datetime.now(timezone.utc),
    )
    completed_run, saved_result = await validation_repo.complete_with_result(run.id, result)

    if outcome in (DomainOutcome.PASS, DomainOutcome.PASS_WITH_OBLIGATIONS):
        # 76번 §2 "Only a successful required validation bundle can create
        # PAPER_ELIGIBLE" — 지금 스콥에선 그 다음 생애주기 단계(VALIDATING)로
        # 전이시키는 것으로 대체한다(마이그레이션 docstring 참조).
        await strategy_service.transition_lifecycle(
            command.strategy_id, command.strategy_version, "VALIDATING"
        )

    return _run_to_view(completed_run, saved_result)
