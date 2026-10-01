"""Router 14 — Strategy Builder API (FD-14.1~FD-14.4).

Spec: functional_spec_v1.20.md#FD-14.1~FD-14.4, 16_backend_signatures.md §16.4

Deviation 1: §16.4 Draft assumed a single-condition simple schema, but the actual
StrategyCreateRequest follows the already-implemented ConditionCompiler/PreviewCalculator
service contract (list + AND/OR composition) as-is (see schemas/strategy_builder.py).

Deviation 2: FD-14.4 states "input: no strategy_id (temporary calculation before save)",
yet §16.4 Draft sketches `GET /strategies/{strategy_id}/preview` — a contradiction.
Following FD-14.4's more detailed processing steps, this implements `POST /preview`
and does not accept strategy_id.

Deviation 3 (intentional reduction): The already-implemented
StrategyBuilderService.transition_lifecycle() is not exposed via this router — if users
could call transitions via HTTP directly, they could self-approve their own strategies,
creating a loophole that neutralizes the lifecycle enforcement in 9.1. The pipeline
(backtest/validation/stress-test/Paper Trading, FD-9.3, etc., not yet implemented)
should automatically invoke these transitions when it exists; at that point, wire
to internal call paths.

PLT-18 — Migrated all raw `HTTPException` raises to domain exceptions (§9
PLT-17~21). The "not found" reason from `get_strategy()` is separated into
`StrategyNotFoundError`(strategy_builder_service.py, a `StrategyLifecycleError`
subclass) — using the same `StrategyLifecycleError` for both `create_strategy`
(save rejection, 400) and `get_strategy`(target not found, 404) would leave
exception_mapping.py's type-based EXCEPTION_MAP unable to choose a single status code.
`PromptGenerationUnavailableError`(501) specifies only the status code via
STATUS_OVERRIDE (same rationale as marketplace.py module
docstring — no new ErrorCode for 501).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from src.api.deps import get_current_user
from src.api.schemas.strategy_builder import (
    CandleResponse,
    IndicatorComputeResponse,
    IndicatorListResponse,
    PreviewRequest,
    PreviewResponse,
    PromptGenerateRequest,
    StrategyCreateRequest,
    StrategyDetailResponse,
    StrategyResponse,
    WizardGenerateRequest,
    to_candle_response,
    to_strategy_detail_response,
    to_strategy_response,
)
from src.api.service_deps import get_credential_resolver
from src.api.strategy_builder_deps import get_indicator_service, get_strategy_builder_service
from src.core.indicators.registry import DEFAULT_REGISTRY
from src.core.indicators.talib_adapter import SUPPORTED_INDICATORS, IndicatorService
from src.services.auth_service import User
from src.services.condition_compiler import ConditionCompiler
from src.services.credential_resolver import CredentialResolver
from src.services.preview_service import PreviewCalculator
from src.services.strategy_builder_service import StrategyBuilderService, StrategySummary
from src.services.strategy_prompt_service import StrategyPromptService
from src.services.strategy_wizard_service import GeneratedConditions, StrategyWizardService

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
    spec = DEFAULT_REGISTRY.get(name)
    param_name = spec.params[0].name if spec.params else None
    adapter = await resolver.get_adapter(user.user_id, exchange)

    candles = await adapter.get_ohlcv(symbol, timeframe, limit=limit)
    kwargs = {param_name: period} if param_name is not None and period is not None else {}
    result = indicator_service.calculate(name, candles, **kwargs)
    return IndicatorComputeResponse(**result.model_dump())


@router.get("/strategies")
async def list_strategies(
    user: User = Depends(get_current_user),
    service: StrategyBuilderService = Depends(get_strategy_builder_service),
) -> list[StrategySummary]:
    return await service.list_strategies(user.user_id)


@router.get("/candles")
async def get_candles(
    exchange: str,
    symbol: str,
    timeframe: str = "1h",
    limit: int = 200,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> list[CandleResponse]:
    """Deviation (2026-09-01, gap discovered after app assembly): compute_indicator/
    preview only fetch candles server-side and return indicator values/signals —
    the frontend had no way to render actual candlestick charts (price data itself).
    Reuses the same CredentialResolver pattern to return raw OHLCV as-is
    (no new computation logic)."""
    adapter = await resolver.get_adapter(user.user_id, exchange)
    candles = await adapter.get_ohlcv(symbol, timeframe, limit=limit)
    return [to_candle_response(c) for c in candles]


@router.post("/strategies", status_code=status.HTTP_201_CREATED)
async def create_strategy(
    body: StrategyCreateRequest,
    user: User = Depends(get_current_user),
    service: StrategyBuilderService = Depends(get_strategy_builder_service),
) -> StrategyResponse:
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
    return to_strategy_response(saved, fsm_definition)


@router.get("/strategies/{strategy_id}/{version}")
async def get_strategy(
    strategy_id: str,
    version: str,
    user: User = Depends(get_current_user),
    service: StrategyBuilderService = Depends(get_strategy_builder_service),
) -> StrategyDetailResponse:
    detail = await service.get_strategy(user.user_id, strategy_id, version)
    return to_strategy_detail_response(detail)


@router.post("/preview")
async def preview(
    body: PreviewRequest,
    user: User = Depends(get_current_user),
    resolver: CredentialResolver = Depends(get_credential_resolver),
) -> PreviewResponse:
    adapter = await resolver.get_adapter(user.user_id, body.exchange)
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
    return StrategyWizardService().generate(body.goal, body.risk_tolerance)


@router.post("/generate-from-prompt")
async def generate_from_prompt(
    body: PromptGenerateRequest,
    user: User = Depends(get_current_user),
) -> GeneratedConditions:
    return await StrategyPromptService().generate(body.prompt)
