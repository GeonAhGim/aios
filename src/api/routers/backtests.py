"""BT-10c — `POST /v1/backtests/quick`: 즉시 백테스트 HTTP API.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md#§9.5 BT-10
(호출자=라우터, `quick_backtest.py` 모듈 docstring "호출자(BT-13 라우터·BT-11
잡)가 `read_candles_columnar` 한 번(왕복 1회)으로 읽어 넘긴다"), §3.4.

71번 §6 규칙: 라우터는 auth/TenantContext 주입·transport validation·
application 호출만 한다. 이 라우터는 `compile_source`(DSL-12)로 컴파일 →
`build_script_signal_source`(BT-10b)로 전략 접점 조립 → 캔들 컬럼 1회 조회
(`CandleStore.read_candles_columnar`, LA-23b) → `run_quick_backtest`(BT-10)
호출까지만 하고, 체결·비용·전략 로직을 다시 계산하지 않는다(REJECT 대상).
`quick_backtest.py`(task-1504 9a1ae87)는 이미 머지된 소비 대상이라 수정하지
않는다.

도메인 예외는 잡지 않는다 — `exception_registry_foundation.py`(EXCEPTION_MAP)
가 400/404로 번역한다(raw HTTPException 0건, PLT-21 가드 대상). 예외:
`TooManyBarsError`는 BT-11 안내를 위해 `details.bars/max`가 필요한데 그
클래스 자체(`quick_backtest.py`, 수정 금지)는 details를 채우지 않으므로,
여기서 우리가 이미 아는 값(읽은 봉 수·상한)을 인스턴스에 얹어 그대로
재전파한다(새 예외 클래스·새 error_code 없음 — task-1218 `ConsentNotFoundError`
번역 패턴과 같은 "애플리케이션 파일 수정 불가, 라우터가 보강" 근거).

동기 실행·무저장(decision) — 백테스트 결과를 어디에도 쓰지 않는다. LIVE
경로와 무관하며 주문을 실제로 내지 않는다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Depends

from src.api.contracts.envelope import ApiResponse, ok
from src.api.deps import get_pool
from src.api.foundation_deps import (
    get_candle_store,
    get_market_reference_reader,
    get_market_reference_repository,
    get_tenant_context,
)
from src.api.schemas.backtests import QuickBacktestRequest, QuickBacktestResultView
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorRegistry
from src.core.script.artifact.compile import compile_source
from src.foundation.backtest.application.quick_backtest import (
    MAX_QUICK_BARS,
    TooManyBarsError,
    run_quick_backtest,
)
from src.foundation.backtest.application.script_signal_source import build_script_signal_source
from src.foundation.market_data.adapters.postgres_source_contract import (
    PostgresSourceContractRepository,
)
from src.foundation.market_data.application.read_api import (
    authorize_redistribution,
    resolve_instrument,
)
from src.foundation.market_data.contracts.v1 import SeriesKey
from src.foundation.market_data.domain.entitlement.source_contract import DataUse
from src.foundation.market_data.ports.candle_store import CandleStore
from src.foundation.market_data.ports.reference_repository import (
    ReferenceReadRepository,
    ReferenceRepository,
)
from src.foundation.market_data.ports.source_contract_repository import SourceContractRepository
from src.foundation.trust.contracts.v1 import TenantContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/backtests", tags=["backtests"])


def get_indicator_registry() -> IndicatorRegistry:
    """IND-1 기본 레지스트리 — DSL-12 라우터(`scripts.py`)와 같은 프로세스
    단일 인스턴스를 공유한다(테스트가 덮어쓸 수 있는 의존성)."""
    return DEFAULT_REGISTRY


def get_source_contract_repository() -> SourceContractRepository:
    """DC-28 — `market_data.py` 라우터와 같은 정의(상태 없음, pool 불필요).
    같은 이름의 함수를 두 라우터가 각자 갖는 것은 `foundation_deps.py`가
    이미 300줄 상한에 닿아 있어(P6.line_cap) 공용 모듈로 옮기지 않은
    의도적 선택이다."""
    return PostgresSourceContractRepository()


@router.post("/quick", response_model=ApiResponse[QuickBacktestResultView])
async def quick_backtest_endpoint(
    body: QuickBacktestRequest,
    tenant: TenantContext = Depends(get_tenant_context),
    pool: asyncpg.Pool = Depends(get_pool),
    store: CandleStore = Depends(get_candle_store),
    refs: ReferenceRepository = Depends(get_market_reference_repository),
    reader: ReferenceReadRepository = Depends(get_market_reference_reader),
    registry: IndicatorRegistry = Depends(get_indicator_registry),
    source_contracts: SourceContractRepository = Depends(get_source_contract_repository),
) -> ApiResponse[QuickBacktestResultView]:
    async with pool.acquire() as conn:
        now = datetime.now(timezone.utc)
        inst = await resolve_instrument(
            conn, refs=refs, reader=reader, venue=body.venue, symbol=body.symbol,
            instrument_id=body.instrument_id, now=now,
        )
        # DC-28(ADR-2026-09-06-H D2) — 백테스트는 원시 캔들을 화면에 그대로
        # 띄우지 않고 내부 계산(체결·손익)에만 쓴다 — `INTERNAL_CALC`는
        # `INTERNAL` 스코프까지도 허용하는 가장 낮은 문턱이다.
        await authorize_redistribution(
            conn, inst.venue.value, repo=source_contracts, clock=lambda: now,
            use=DataUse.INTERNAL_CALC,
        )
        key = SeriesKey(
            venue=inst.venue, instrument_id=inst.instrument_id, timeframe=body.timeframe
        )
        columns = await store.read_candles_columnar(conn, key, body.start, body.end, body.as_of)

    compiled = compile_source(body.script_source, registry_version=registry.registry_hash())
    strategy = build_script_signal_source(compiled.ir, bar_count=len(columns), columns=columns)

    try:
        result = run_quick_backtest(
            body.config, columns, timeframe=body.timeframe, strategy=strategy,
            initial_cash=body.initial_cash, funding_rate=body.funding_rate, max_bars=MAX_QUICK_BARS,
        )
    except TooManyBarsError as exc:
        exc.details = {"bars": len(columns), "max": MAX_QUICK_BARS}  # type: ignore[attr-defined]
        raise

    logger.info(
        "backtest.quick",
        extra={
            "event_type": "backtest.quick",
            "tenant_id": str(tenant.tenant_id),
            "script_hash": compiled.script_hash,
            "bars": result.bars,
        },
    )
    return ok(QuickBacktestResultView.from_result(result))


__all__ = ["get_indicator_registry", "router"]
