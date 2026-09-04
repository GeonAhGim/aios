"""R-42 — 최근 캔들 close_time 관측점.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#§9 R-42.

`CircuitBreakerMetrics.data_delay_sec`는 metrics_collector.py에서 아직
상수 0을 반환한다("정직한 축소" — 그 모듈 docstring 참조). 실계산에는
"마지막으로 관측된 캔들 close_time"이 필요한데, 그 관측 지점 자체가
없었다. 이 모듈이 그 관측 지점이다 — `InstrumentedAdapter.get_ohlcv`
호출부가 (exchange, symbol)별 마지막 close_time을 여기에 기록하면,
`max_delay_sec(now)`로 "지금 기준 가장 오래된 관측 지연"을 구할 수 있다.

상수 0을 이 실측값으로 교체하는 배선(circuit_breaker.py)은 R-43 몫이며
이 리프는 관측점만 만든다 — 아직 아무 곳도 이 트래커를 읽지 않는다.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal


class DataFreshnessTracker:
    """프로세스 메모리 전용 — `ApiCallTracker`(metrics_collector.py)와 같은
    수명주기로, 단일 main.py 프로세스 안에서 InstrumentedAdapter가 공유한다."""

    def __init__(self) -> None:
        self._last_close_time: dict[tuple[str, str], datetime] = {}

    def record(self, exchange: str, symbol: str, close_time: datetime) -> None:
        if close_time.utcoffset() is None:
            raise ValueError("close_time must be tz-aware (UTC)")
        self._last_close_time[(exchange, symbol)] = close_time

    def max_delay_sec(self, now: datetime) -> Decimal | None:
        """(exchange, symbol) 관측이 하나도 없으면 `None`을 반환한다 —
        "관측 0건"을 "지연 0초"로 되돌리면 호출자가 지연이 없다고
        오판할 수 있으므로(fail-closed), 두 상태를 절대 섞지 않는다."""
        if now.utcoffset() is None:
            raise ValueError("now must be tz-aware (UTC)")
        if not self._last_close_time:
            return None
        max_delay = max(now - close_time for close_time in self._last_close_time.values())
        return Decimal(str(max_delay.total_seconds()))
