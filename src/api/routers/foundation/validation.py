"""Strategy Validation API — 71번 §6 규칙: router는 auth/주입/transport
validation/command invocation만 담당한다.

76번 §4 라우트 이름(`POST /v1/foundation/validation-runs`)을 그대로 쓰되,
strategy_id/version은 path에 둔다(어느 전략의 검증인지 URL만 보고 알 수
있게)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_current_user
from src.api.foundation_deps import get_validation_repository
from src.api.schemas.foundation.validation import StartValidationRequest, ValidationResultView
from src.api.service_deps import get_credential_resolver
from src.api.strategy_builder_deps import get_strategy_builder_service
from src.foundation.backtest.application.run_backtest import BacktestRunError
from src.foundation.validation.application.start_validation import (
    StrategyNotEligibleForValidationError,
    ValidationAlreadyInProgressError,
    start_validation,
)
from src.foundation.validation.contracts.v1 import StartValidationCommand
from src.foundation.validation.ports.repository import ValidationRepository
from src.services.auth_service import User
from src.services.credential_resolver import CredentialNotFoundError, CredentialResolver
from src.services.strategy_builder_service import StrategyBuilderService

router = APIRouter(prefix="/v1/foundation/validation-runs", tags=["foundation:validation"])


@router.post("/{strategy_id}/{strategy_version}")
async def post_start_validation(
    strategy_id: str,
    strategy_version: str,
    body: StartValidationRequest,
    user: User = Depends(get_current_user),
    validation_repo: ValidationRepository = Depends(get_validation_repository),
    strategy_service: StrategyBuilderService = Depends(get_strategy_builder_service),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> ValidationResultView:
    try:
        adapter = await resolver.get_adapter(user.user_id, body.exchange)
    except CredentialNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    bars = await adapter.get_ohlcv(body.symbol, body.timeframe, limit=body.limit)

    command = StartValidationCommand(
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        cost_model_fee_bps=body.cost_model_fee_bps,
        cost_model_slippage_bps=body.cost_model_slippage_bps,
        warmup_bars=body.warmup_bars,
        periods_per_year=body.periods_per_year,
        initial_equity=body.initial_equity,
    )

    try:
        return await start_validation(
            validation_repo,
            strategy_service,
            owner_user_id=user.user_id,
            command=command,
            bars=bars,
        )
    except StrategyNotEligibleForValidationError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except ValidationAlreadyInProgressError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except BacktestRunError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
