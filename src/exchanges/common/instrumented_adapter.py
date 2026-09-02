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
"""
from __future__ import annotations

import inspect
from typing import Any

from src.core.safety.metrics_collector import ApiCallTracker
from src.exchanges.common.adapter import ExchangeAdapter


class InstrumentedAdapter:
    def __init__(self, wrapped: ExchangeAdapter, tracker: ApiCallTracker) -> None:
        self._wrapped = wrapped
        self._tracker = tracker

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
            return result

        return wrapper


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
        return InstrumentedAdapter(real_adapter, tracker)

    return factory
