"""MetricsPort/NullMetrics/PrometheusMetrics + 싱글턴 단위테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-04.
기본 싱글턴은 `NullMetrics`여야 하고, `set_metrics`로 교체 가능해야 한다.
"""

from __future__ import annotations

import time

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
        s for family in families for s in family.samples if s.name == "aios_api_request_count_total"
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


def test_prometheus_metrics_observe_rejects_relabeling_same_name() -> None:
    """observe()도 counter()와 동일하게 라벨 키 재정의를 거부해야 한다(히스토그램 경로)."""
    adapter = PrometheusMetrics()
    adapter.observe("aios.test.three.duration_seconds", 0.1, {"a": "1"})
    with pytest.raises(ValueError):
        adapter.observe("aios.test.three.duration_seconds", 0.2, {"b": "2"})


def test_prometheus_metrics_gauge_rejects_relabeling_same_name() -> None:
    """gauge()도 counter()와 동일하게 라벨 키 재정의를 거부해야 한다(게이지 경로)."""
    adapter = PrometheusMetrics()
    adapter.gauge("aios.test.four.gauge", 1.0, {"a": "1"})
    with pytest.raises(ValueError):
        adapter.gauge("aios.test.four.gauge", 2.0, {"b": "2"})


def test_prometheus_metrics_counter_propagates_underlying_empty_name_failure() -> None:
    """실패 주입: 계측 지점이 빈 문자열 이름(예: 상수 조회 실패로 빈 값이 흘러든 경우)을
    넘기면 `prometheus_client`가 던지는 `ValueError`가 조용히 삼켜지지 않고 그대로
    전파되어야 한다(fail-closed — 어댑터가 예외를 흡수해 메트릭 유실을 숨기지 않는다).
    """
    adapter = PrometheusMetrics()
    with pytest.raises(ValueError):
        adapter.counter("")


def test_prometheus_metrics_counter_increments_within_perf_budget() -> None:
    """수치 성능 단언: `_get_or_create`가 이름당 1회만 등록한다면(캐시 적중) 5,000회
    반복이 300ms 안에 끝나야 한다. 캐시가 없다면 두 번째 호출부터 동일 이름의 중복
    등록으로 `CollectorRegistry`가 즉시 `ValueError`를 던져 이 테스트 자체가 실패한다
    — 성능 상한과 캐시 동작을 함께 증명한다.
    """
    adapter = PrometheusMetrics()
    iterations = 5000
    start = time.perf_counter()
    for _ in range(iterations):
        adapter.counter("aios.test.perf.count_total")
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 300, f"{iterations}회 counter() 호출 {elapsed_ms:.1f}ms — 상한 300ms 초과"
