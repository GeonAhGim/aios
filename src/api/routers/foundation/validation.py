"""Strategy Validation API — 71번 §6 규칙: router는 auth/주입/transport
validation/command invocation만 담당한다.

76번 §4 라우트 이름(`POST /v1/foundation/validation-runs`)을 그대로 쓰되,
strategy_id/version은 path에 둔다(어느 전략의 검증인지 URL만 보고 알 수
있게).

도메인 예외는 여기서 잡지 않는다 — `src/api/contracts/exception_mapping.py`의
`EXCEPTION_MAP`이 전역 핸들러에서 봉투로 번역한다(§9 PLT-21b decision,
task-1218)."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_current_user
from src.api.foundation_deps import get_validation_repository
from src.api.schemas.foundation.validation import StartValidationRequest, ValidationResultView
from src.api.service_deps import get_credential_resolver
from src.api.strategy_builder_deps import get_strategy_builder_service
from src.foundation.validation.application.start_validation import start_validation
from src.foundation.validation.contracts.v1 import StartValidationCommand
from src.foundation.validation.ports.repository import ValidationRepository
from src.services.auth_service import User
from src.services.credential_resolver import CredentialResolver
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
) -> ApiResponse[ValidationResultView]:
    adapter = await resolver.get_adapter(user.user_id, body.exchange)
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

    result = await start_validation(
        validation_repo,
        strategy_service,
        owner_user_id=user.user_id,
        command=command,
        bars=bars,
    )
    return ok(result)
