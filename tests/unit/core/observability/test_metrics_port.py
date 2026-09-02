"""MetricsPort/NullMetrics/PrometheusMetrics + 싱글턴 단위테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-04.
기본 싱글턴은 `NullMetrics`여야 하고, `set_metrics`로 교체 가능해야 한다.
"""
from __future__ import annotations

import pytest

from src.core.observability.metric_names import API_REQUEST_COUNT_TOTAL
from src.core.observability.metrics import NullMetrics, PrometheusMetrics, metrics, set_metrics


@pytest.fixture(autouse=True)
def _restore_singleton():
    original = metrics()
    yield
    set_metrics(original)


def test_default_singleton_is_null_metrics() -> None:
    assert isinstance(metrics(), NullMetrics)


def test_null_metrics_is_noop_and_never_raises() -> None:
    sink = NullMetrics()
    assert sink.counter("aios.test.thing.count_total", {"outcome": "ok"}) is None
    assert sink.observe("aios.test.thing.duration_seconds", 0.5, {"route": "/x"}) is None
    assert sink.gauge("aios.test.thing.gauge", 3.0) is None


def test_set_metrics_replaces_singleton() -> None:
    replacement = NullMetrics()
    set_metrics(replacement)
    assert metrics() is replacement


def test_prometheus_metrics_counter_records_labeled_value() -> None:
    adapter = PrometheusMetrics()
    adapter.counter(API_REQUEST_COUNT_TOTAL, {"route": "/x", "method": "GET"})
    adapter.counter(API_REQUEST_COUNT_TOTAL, {"route": "/x", "method": "GET"})

    families = list(adapter._registry.collect())
    sample = next(
        s
        for family in families
        for s in family.samples
        if s.name == "aios_api_request_count_total"
    )
    assert sample.value == 2.0
    assert sample.labels == {"route": "/x", "method": "GET"}


def test_prometheus_metrics_observe_records_histogram_sample() -> None:
    adapter = PrometheusMetrics()
    adapter.observe("aios.test.thing.duration_seconds", 0.2, {"route": "/y"})

    families = list(adapter._registry.collect())
    count_sample = next(
        s
        for family in families
        for s in family.samples
        if s.name == "aios_test_thing_duration_seconds_count"
    )
    assert count_sample.value == 1.0


def test_prometheus_metrics_gauge_records_set_value() -> None:
    adapter = PrometheusMetrics()
    adapter.gauge("aios.test.thing.gauge", 7.0, {"check": "db"})

    families = list(adapter._registry.collect())
    sample = next(
        s for family in families for s in family.samples if s.name == "aios_test_thing_gauge"
    )
    assert sample.value == 7.0


def test_prometheus_metrics_rejects_relabeling_same_name() -> None:
    """같은 이름을 다른 라벨 키 집합으로 다시 쓰면 실패한다(계측 지점 실수 방지)."""
    adapter = PrometheusMetrics()
    adapter.counter("aios.test.two.count_total", {"a": "1"})
    with pytest.raises(ValueError):
        adapter.counter("aios.test.two.count_total", {"b": "2"})
