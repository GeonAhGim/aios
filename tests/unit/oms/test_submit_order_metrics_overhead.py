"""L4-27b — `submit_order.py` 계측 오버헤드 수치 단언(task-3506 DoD).

`tests/performance/oms/test_submit_internal_latency.py`(L4-28)가 이미 지적한
decision(§ 모듈 docstring)을 그대로 따른다 — DB 왕복이 섞인 절대 ms 임계는
이 저장소 공유 CI에서 최대 20배 변동해 상시 적색을 낳은 전례가 있다. 이
파일이 재는 대상은 그 문제와 무관하다: `submit_order`의 계측 지점이 추가하는
비용은 `time.monotonic()` 호출 2회 + `MetricsPort.counter/observe` 호출
1회씩뿐이고 DB/네트워크 I/O가 전혀 없는 순수 CPU 경로라 이 환경 안에서도
분산이 극히 작다 — 그래서 여기서는(DB 없는 순수 CPU 벤치) 절대 ms 상수를
직접 단언해도 다른 파일들이 겪은 편차 문제가 재현되지 않는다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from src.core.observability.metric_names import (
    OMS_ORDER_SUBMIT_COUNT_TOTAL,
    OMS_ORDER_SUBMIT_DURATION_SECONDS,
)
from src.core.observability.metrics import MetricsPort, NullMetrics

_SAMPLE_COUNT = 5000
_P99_TARGET_MS = 0.2  # DoD "계측 오버헤드 p99 < 0.2ms"


@dataclass
class _ListSpyMetrics:
    """실 `PrometheusMetrics`보다 싸지 않게(리스트 append + dict 생성) 맞춘
    참조 구현 — `NullMetrics`만 재면 "아무것도 안 한 비용"이 되어 계측
    오버헤드 자체를 재지 못한다."""

    counters: list[tuple[str, dict[str, str] | None]] = field(default_factory=list)
    observations: list[tuple[str, float, dict[str, str] | None]] = field(default_factory=list)

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.counters.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        self.observations.append((name, value, labels))

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None


def _record_submit_order_style(m: MetricsPort, venue: str, outcome: str) -> None:
    """`submit_order`의 `finally` 블록이 실제로 하는 작업을 그대로 재현한다
    (모듈을 통째로 부르지 않고 계측 부분만 분리해 DB 변동을 제거한다)."""
    start = time.monotonic()
    elapsed = time.monotonic() - start
    m.counter(OMS_ORDER_SUBMIT_COUNT_TOTAL, {"outcome": outcome, "venue": venue})
    m.observe(OMS_ORDER_SUBMIT_DURATION_SECONDS, elapsed, {"venue": venue})


def test_null_metrics_instrumentation_overhead_p99_under_200us() -> None:
    m = NullMetrics()
    samples_ms: list[float] = []
    for _ in range(_SAMPLE_COUNT):
        started = time.perf_counter()
        _record_submit_order_style(m, "bitget", "accepted")
        samples_ms.append((time.perf_counter() - started) * 1000.0)

    samples_ms.sort()
    p99 = samples_ms[int(len(samples_ms) * 0.99)]
    msg = f"NullMetrics 오버헤드 p99({p99:.4f}ms) > 목표({_P99_TARGET_MS}ms)"
    assert p99 < _P99_TARGET_MS, msg


def test_spy_metrics_instrumentation_overhead_p99_under_200us() -> None:
    """리스트에 실제로 append하는(참조용 스파이 포트) 구현도 같은 예산 안에 든다
    — `NullMetrics`가 no-op이라 지나치게 낙관적인 수치를 낼 위험을 상쇄한다."""
    m = _ListSpyMetrics()
    samples_ms: list[float] = []
    for _ in range(_SAMPLE_COUNT):
        started = time.perf_counter()
        _record_submit_order_style(m, "bitget", "accepted")
        samples_ms.append((time.perf_counter() - started) * 1000.0)

    assert len(m.counters) == _SAMPLE_COUNT
    samples_ms.sort()
    p99 = samples_ms[int(len(samples_ms) * 0.99)]
    msg = f"spy metrics 오버헤드 p99({p99:.4f}ms) > 목표({_P99_TARGET_MS}ms)"
    assert p99 < _P99_TARGET_MS, msg
