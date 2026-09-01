"""14번 — 전략 편집기 API 라우터 (FD-14.1~14.4).

Spec: 기능설계문서_v1.20.md#FD-14.1~FD-14.4, 16_backend_signatures.md §16.4

편차 1: §16.4 Draft는 조건 1개짜리 단순 스키마를 가정했지만, 실제
StrategyCreateRequest는 이미 완성된 ConditionCompiler/PreviewCalculator
서비스 계약(리스트+AND/OR 결합)을 그대로 따른다(schemas/strategy_builder.py
참조).

편차 2: FD-14.4 본문이 "입력: strategy_id 없음(저장 전 임시 계산)"이라고
명시하는데 §16.4 Draft는 `GET /strategies/{strategy_id}/preview`로
스케치해 서로 모순된다 — FD-14.4 본문(더 구체적인 처리단계 서술)을
따라 `POST /preview`로 구현하고 strategy_id를 받지 않는다.

편차 3(의도적 축소): 이미 구현된 StrategyBuilderService.transition_lifecycle()을
이 라우터에 노출하지 않는다 — 백테스트/검증/스트레스테스트/Paper
Trading 파이프라인(FD-9.3 등, 아직 미구현)이 자동으로 호출해야 할
전이를 사용자가 HTTP로 직접 호출하면 본인 전략을 셀프 승인해 9.1
생애주기 강제를 무력화하는 구멍이 생긴다. 그 파이프라인들이 생기면
그때 내부 호출 경로로 연결한다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from src.api.deps import get_current_user
from src.api.schemas.strategy_builder import (
    IndicatorComputeResponse,
    IndicatorListResponse,
    PreviewRequest,
    PreviewResponse,
    PromptGenerateRequest,
    StrategyCreateRequest,
    StrategyDetailResponse,
    StrategyResponse,
    WizardGenerateRequest,
    to_strategy_detail_response,
    to_strategy_response,
)
from src.api.service_deps import get_credential_resolver
from src.api.strategy_builder_deps import get_indicator_service, get_strategy_builder_service
from src.core.indicators.talib_adapter import (
    SUPPORTED_INDICATORS,
    IndicatorError,
    IndicatorService,
    period_param_name,
)
from src.services.auth_service import User
from src.services.condition_compiler import ConditionCompileError, ConditionCompiler
from src.services.credential_resolver import CredentialNotFoundError, CredentialResolver
from src.services.preview_service import PreviewCalculator
from src.services.strategy_builder_service import StrategyBuilderService, StrategyLifecycleError
from src.services.strategy_prompt_service import (
    PromptGenerationUnavailableError,
    StrategyPromptService,
)
from src.services.strategy_wizard_service import (
    GeneratedConditions,
    StrategyWizardService,
    WizardError,
)

router = APIRouter()


@router.get("/indicators")
async def list_indicators() -> IndicatorListResponse:
    return IndicatorListResponse(indicators=list(SUPPORTED_INDICATORS))


@router.get("/indicators/{name}/compute")
async def compute_indicator(
    name: str,
    exchange: str,
    symbol: str,
    timeframe: str = "1h",
    period: int | None = None,
    limit: int = 200,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
    indicator_service: IndicatorService = Depends(get_indicator_service),
) -> IndicatorComputeResponse:
    try:
        param_name = period_param_name(name)
    except IndicatorError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    try:
        adapter = await resolver.get_adapter(user.user_id, exchange)
    except CredentialNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    candles = await adapter.get_ohlcv(symbol, timeframe, limit=limit)
    kwargs = {param_name: period} if param_name is not None and period is not None else {}
    result = indicator_service.calculate(name, candles, **kwargs)
    return IndicatorComputeResponse(**result.model_dump())


@router.post("/strategies", status_code=status.HTTP_201_CREATED)
async def create_strategy(
    body: StrategyCreateRequest,
    user: User = Depends(get_current_user),
    service: StrategyBuilderService = Depends(get_strategy_builder_service),
) -> StrategyResponse:
    try:
        compiled = ConditionCompiler().compile(
            strategy_id=body.strategy_id,
            version=body.version,
            target_asset=body.target_asset,
            market=body.market,
            exchange=body.exchange,
            author_agent=str(user.user_id),
            entry_conditions=body.entry_conditions,
            exit_conditions=body.exit_conditions,
            stop_loss_conditions=body.stop_loss_conditions,
            entry_combine=body.entry_combine,
            exit_combine=body.exit_combine,
            stop_loss_combine=body.stop_loss_combine,
        )
        fsm_definition = compiled.model_dump(mode="json")
        saved = await service.save_strategy(
            user.user_id,
            body.strategy_id,
            body.version,
            target_asset=body.target_asset,
            market=body.market,
            exchange=body.exchange,
            fsm_definition=fsm_definition,
            author_agent=str(user.user_id),
        )
    except (ConditionCompileError, StrategyLifecycleError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    return to_strategy_response(saved, fsm_definition)


@router.get("/strategies/{strategy_id}/{version}")
async def get_strategy(
    strategy_id: str,
    version: str,
    user: User = Depends(get_current_user),
    service: StrategyBuilderService = Depends(get_strategy_builder_service),
) -> StrategyDetailResponse:
    try:
        detail = await service.get_strategy(user.user_id, strategy_id, version)
    except StrategyLifecycleError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return to_strategy_detail_response(detail)


@router.post("/preview")
async def preview(
    body: PreviewRequest,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> PreviewResponse:
    try:
        adapter = await resolver.get_adapter(user.user_id, body.exchange)
    except CredentialNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc

    candles = await adapter.get_ohlcv(body.symbol, body.timeframe, limit=body.limit)
    result = PreviewCalculator().preview(candles, body.conditions, combine=body.combine)
    return PreviewResponse(
        signal_indices=result.signal_indices,
        signal_times=result.signal_times,
        disclaimer=result.disclaimer,
        message=result.message,
    )


@router.post("/wizard")
async def generate_wizard_strategy(
    body: WizardGenerateRequest,
    user: User = Depends(get_current_user),
) -> GeneratedConditions:
    try:
        return StrategyWizardService().generate(body.goal, body.risk_tolerance)
    except WizardError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.post("/generate-from-prompt")
async def generate_from_prompt(
    body: PromptGenerateRequest,
    user: User = Depends(get_current_user),
) -> GeneratedConditions:
    try:
        return await StrategyPromptService().generate(body.prompt)
    except PromptGenerationUnavailableError as exc:
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, str(exc)) from exc
