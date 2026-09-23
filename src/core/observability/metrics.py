"""Metrics port + Prometheus adapter.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §7.2, §9 PLT-04.
`MetricsPort` is a pure interface exposing only three operations: counter/observe/gauge.
Instrumentation sites reference only the constants in `metric_names.py` by name, and this
module is a thin adapter layered on top of `metrics_registry.py` (in-memory registry,
task-129) — do not mistake it for a reimplementation; registry logic is not rebuilt here.

`prometheus-client` is lazy-imported only inside `PrometheusMetrics.__init__`: the default
`NullMetrics` path must remain functional even when the package is not installed
(§9 PLT-04 decision).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Protocol

from src.core.observability.metric_names import to_prom

logger = logging.getLogger(__name__)


class MetricsPort(Protocol):
    """Metrics recording port. Implementations provide only three operations: counter/histogram/gauge."""

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None: ...

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None: ...

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None: ...


class NullMetrics:
    """Default — records nothing (safe no-op for tests and unconfigured environments)."""

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        return None

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        return None


class PrometheusMetrics:
    """`prometheus_client` delegation adapter. Registers on first call per name with a label-key set.

    Re-registering the same name with a different label-key set causes `prometheus_client` to
    raise `ValueError`, which propagates as-is (preventing registry override is the
    responsibility of instrumentation-site consistency, not the adapter).
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
                metric = factory(to_prom(name), name, list(label_names), registry=self._registry)
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
    """Process-singleton metrics port. Default is `NullMetrics`."""
    return _current_metrics


def set_metrics(port: MetricsPort) -> None:
    """Replaces the singleton (tests isolate with `set_metrics(NullMetrics())`)."""
    global _current_metrics
    _current_metrics = port


def safe_counter(
    metrics_port: MetricsPort, name: str, labels: dict[str, str] | None = None
) -> None:
    """Isolates a metrics-hook failure from the caller's order path (L4-27 D3).

    `PrometheusMetrics` raises `ValueError` when the same name is re-registered
    with a different label-key set (see that class's docstring) -- one mistake
    at an instrumentation site must not roll back an order/fill/resolution
    transaction or propagate as an exception to the caller."""
    try:
        metrics_port.counter(name, labels)
    except Exception:
        logger.warning(
            "metrics.counter failed name=%s -- order path continues", name, exc_info=True
        )


def safe_observe(
    metrics_port: MetricsPort, name: str, value: float, labels: dict[str, str] | None = None
) -> None:
    """Same rationale as `safe_counter`, for the `observe` hook."""
    try:
        metrics_port.observe(name, value, labels)
    except Exception:
        logger.warning(
            "metrics.observe failed name=%s -- order path continues", name, exc_info=True
        )


def safe_gauge(
    metrics_port: MetricsPort, name: str, value: float, labels: dict[str, str] | None = None
) -> None:
    """Same rationale as `safe_counter`, for the `gauge` hook."""
    try:
        metrics_port.gauge(name, value, labels)
    except Exception:
        logger.warning("metrics.gauge failed name=%s -- order path continues", name, exc_info=True)
