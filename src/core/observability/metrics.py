"""메트릭 포트 + Prometheus 어댑터.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §7.2, §9 PLT-04.
`MetricsPort`는 counter/observe/gauge 세 연산만 노출하는 순수 인터페이스다. 계측 지점은
`metric_names.py`의 상수만 이름으로 쓰고, 이 모듈이 `metrics_registry.py`(인메모리 레지스트리,
task-129)의 재구현이 아니라 그 위에 얹히는 얇은 어댑터라는 점에 유의 — 레지스트리 로직은
여기서 다시 만들지 않는다.

`prometheus-client`는 `PrometheusMetrics.__init__` 안에서만 지연 import한다: 패키지가
설치되지 않은 환경에서도 기본값인 `NullMetrics` 경로는 그대로 동작해야 하기 때문이다
(§9 PLT-04 decision).
"""
from __future__ import annotations

import threading
from typing import Any, Protocol

from src.core.observability.metric_names import to_prom


class MetricsPort(Protocol):
    """메트릭 기록 포트. 구현체는 counter/histogram/gauge 세 연산만 제공한다."""

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None: ...

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None: ...

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None: ...


class NullMetrics:
    """기본값 — 아무것도 기록하지 않는다(테스트·미설정 환경의 안전한 기본값)."""

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        return None

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None


class PrometheusMetrics:
    """`prometheus_client` 위임 어댑터. 이름당 최초 호출 시 라벨 키 집합으로 등록한다.

    같은 이름을 다른 라벨 키 집합으로 다시 호출하면 `prometheus_client`가 던지는
    `ValueError`가 그대로 전파된다(레지스트리 재정의 방지는 어댑터가 아니라 계측 지점의
    일관성 책임).
    """

    def __init__(self) -> None:
        from prometheus_client import CollectorRegistry

        self._registry = CollectorRegistry()
        self._lock = threading.Lock()
        self._counters: dict[str, Any] = {}
        self._histograms: dict[str, Any] = {}
        self._gauges: dict[str, Any] = {}

    def _get_or_create(
        self, store: dict[str, Any], name: str, label_names: tuple[str, ...], factory: Any
    ) -> Any:
        with self._lock:
            metric = store.get(name)
            if metric is None:
                metric = factory(
                    to_prom(name), name, list(label_names), registry=self._registry
                )
                store[name] = metric
            return metric

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        from prometheus_client import Counter

        labels = labels or {}
        metric = self._get_or_create(self._counters, name, tuple(labels), Counter)
        (metric.labels(**labels) if labels else metric).inc()

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        from prometheus_client import Histogram

        labels = labels or {}
        metric = self._get_or_create(self._histograms, name, tuple(labels), Histogram)
        (metric.labels(**labels) if labels else metric).observe(value)

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        from prometheus_client import Gauge

        labels = labels or {}
        metric = self._get_or_create(self._gauges, name, tuple(labels), Gauge)
        (metric.labels(**labels) if labels else metric).set(value)


_current_metrics: MetricsPort = NullMetrics()


def metrics() -> MetricsPort:
    """프로세스 싱글턴 메트릭 포트. 기본값은 `NullMetrics`."""
    return _current_metrics


def set_metrics(port: MetricsPort) -> None:
    """싱글턴을 교체한다(테스트는 `set_metrics(NullMetrics())`로 격리)."""
    global _current_metrics
    _current_metrics = port
