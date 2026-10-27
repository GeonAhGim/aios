"""lifespan — real distrust provider factory wiring.

분리 근거: test_app_lifespan.py의 distrust provider factory 배선 테스트를
spec split 지시에 따라 sibling 파일로 분리함.
"""

from __future__ import annotations

from src.services.safety.reference_quotes import (
    BinancePublicTickerReference,
    BitgetFuturesMarkPriceReference,
    DefaultDistrustProviderFactory,
)


async def test_lifespan_wires_real_distrust_provider_factory_into_execution_scheduler() -> None:
    """게이트 적색 재현(task-2810 배선 결함 회귀) — 예전엔
    `ExecutionLoopScheduler`가 `distrust_provider_factory` 인자 자체를
    받지 않아 production 배선(`background_loops.py`)이 이 값을 채울 방법이
    없었고, 매 틱 `distrust_providers`가 기본값 `()`로 고정돼 R-48의 2소스
    쿼럼 비교가 한 번도 돌지 않았다. `tests/integration/test_execution_scheduler.py`
    의 관련 테스트들은 스케줄러를 직접 만들며 커스텀 팩토리를 주입해 이
    회귀를 못 잡는다 — 여기서는 실제 lifespan(`start_background_loops`)이
    조립한 스케줄러의 팩토리가 `DefaultDistrustProviderFactory`이고
    실제로 참조 provider를 만들어내는지 end-to-end로 증명한다."""
    from fastapi import FastAPI

    from src.main import lifespan as _lifespan

    # Fresh instance: the shared `app` singleton collides with parallel lifespans (task-20036).
    test_app = FastAPI(lifespan=_lifespan)
    async with test_app.router.lifespan_context(test_app):
        scheduler = test_app.state.execution_scheduler
        factory = scheduler._distrust_provider_factory
        assert isinstance(factory, DefaultDistrustProviderFactory)

        bitget_providers = factory(object(), "bitget")
        assert len(bitget_providers) == 2
        assert isinstance(bitget_providers[0], BinancePublicTickerReference)
        assert isinstance(bitget_providers[1], BitgetFuturesMarkPriceReference)

        # bitget이 아닌 거래소는 아직 R-48 쿼럼 대상이 아니다(Binance만 참조).
        other_providers = factory(object(), "okx")
        assert len(other_providers) == 1
        assert isinstance(other_providers[0], BinancePublicTickerReference)
