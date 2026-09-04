"""ExchangeAdapter 호출을 ApiCallTracker에 기록하는 계측 프록시.

Spec: PM 배정 ⑤ 2단계(agent-platform-12, 2026-09-03) — CircuitBreaker의
api_error_rate_pct/api_disconnect_sec 지표가 필요로 하는 "실제 어댑터
호출 성공/실패"를 여기 한 곳에서만 계측한다. `CredentialResolver`가
이미 갖고 있던 `adapter_factory` 주입 지점(생성자 DI)에 이 클래스를
감싸는 팩토리를 넣으면, 시세/잔고/주문 호출이 전부 자동으로
계측된다 — tick.py/submit.py 등 실제 호출부는 전혀 손대지 않는다.

`ExchangeAdapter`를 상속하지 않고 `__getattr__` 위임으로 감싼다 —
코드베이스 어디서도 `isinstance(adapter, ExchangeAdapter)` 검사를 하지
않음을 확인했다(2026-09-03). 코루틴 메서드(get_ticker/get_ohlcv/
get_balance/place_order 등)만 성공/실패를 기록하고, `is_paper_trading`/
`is_sandboxed` 같은 프로퍼티나 동기 메서드는 그대로 통과시킨다 —
`require_paper_sandbox` 데코레이터(src/exchanges/common/live_guard.py)가
이 값들을 그대로 읽어야 하므로 값 자체를 바꾸면 안 된다.

세션30의 ab86e22(sync_server_time() opt-in 서버시간 동기화) 반영 —
CredentialResolver.get_adapter()는 동기 `adapter_factory(...)` 호출이라
여기서 await할 수 없다(credential_resolver.py는 이 배선을 위해 고치지
않기로 함). 그래서 어댑터가 sync_server_time을 갖고 있으면 생성 직후
`asyncio.ensure_future`로 백그라운드 실행만 예약하고, 실패는
done-callback에서 경고 로그만 남긴다(어댑터 자체 sync_server_time()은
이미 내부에서 예외를 삼키므로 대부분 이 콜백까지 오지 않음 — 향후
다른 거래소 구현이 다르게 동작할 경우의 안전망).

R-42(2026-09-04) — `freshness` 옵션 인자(기본 None, 하위호환). 값이
주어지면 `get_ohlcv` 호출이 성공할 때마다 반환된 캔들 목록의 마지막
원소에서 (exchange, symbol, close_time)을 읽어 `DataFreshnessTracker`에
기록한다. `Candle`(src/data/models/market_data.py)이 이미 두 필드를
갖고 있어 별도로 어댑터의 거래소 이름을 조회할 필요가 없다. 빈 목록은
아무것도 기록하지 않는다(관측 0건은 트래커가 이미 `None`으로 구분).
"""
from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any

from src.core.safety.data_freshness import DataFreshnessTracker
from src.core.safety.metrics_collector import ApiCallTracker
from src.exchanges.common.adapter import ExchangeAdapter

logger = logging.getLogger(__name__)


class InstrumentedAdapter:
    def __init__(
        self,
        wrapped: ExchangeAdapter,
        tracker: ApiCallTracker,
        *,
        freshness: DataFreshnessTracker | None = None,
    ) -> None:
        self._wrapped = wrapped
        self._tracker = tracker
        self._freshness = freshness

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._wrapped, name)
        if not inspect.iscoroutinefunction(attr):
            return attr

        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = await attr(*args, **kwargs)
            except Exception:
                self._tracker.record_failure()
                raise
            self._tracker.record_success()
            if name == "get_ohlcv" and self._freshness is not None and result:
                last_candle = result[-1]
                self._freshness.record(
                    last_candle.exchange, last_candle.symbol, last_candle.close_time
                )
            return result

        return wrapper


def _warn_on_sync_server_time_failure(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.warning("sync_server_time() 백그라운드 호출 실패 — 오프셋 0 유지: %s", exc)


def instrumented_adapter_factory(
    tracker: ApiCallTracker,
    base_factory: Any,
) -> Any:
    """`CredentialResolver(adapter_factory=...)`에 그대로 넣을 수 있는
    래퍼 — `base_factory`(보통 `src.exchanges.factory.build_adapter`)가
    만든 실제 adapter를 `InstrumentedAdapter`로 한 번 더 감싼다."""

    def factory(
        exchange: str,
        api_key: str,
        api_secret: str,
        extra: dict[str, str] | None,
        *,
        demo_mode: bool = True,
    ) -> InstrumentedAdapter:
        real_adapter = base_factory(exchange, api_key, api_secret, extra, demo_mode=demo_mode)
        wrapped = InstrumentedAdapter(real_adapter, tracker)
        sync_time = getattr(wrapped, "sync_server_time", None)
        if sync_time is not None:
            task = asyncio.ensure_future(sync_time())
            task.add_done_callback(_warn_on_sync_server_time_failure)
        return wrapped

    return factory
