"""MetricsRegistry 단위테스트.

DoD(docs/specs/L4_market_data_positions_ledger_v1.0.md §9 L0-1):
render_text()가 Prometheus 텍스트 형식으로 파싱 가능하고, 라벨 키 불일치 시
ValueError.
"""
from __future__ import annotations

import re

import pytest

from src.core.observability.metrics_registry import MetricsRegistry, get_registry

_METRIC_LINE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(\{[^}]*\})? (-?[0-9.eE+-]+)$")


def _parse_prometheus_text(text: str) -> dict[tuple[str, str], str]:
    """단순 Prometheus 텍스트 형식 파서 — `# TYPE` 라인 검증 + `name{labels} value` 파싱."""
    values: dict[tuple[str, str], str] = {}
    for line in text.splitlines():
        if not line:
            continue
        if line.startswith("# TYPE "):
            parts = line.split()
            assert len(parts) == 4, f"malformed TYPE line: {line!r}"
            assert parts[3] in ("counter", "gauge", "histogram")
            continue
        match = _METRIC_LINE.match(line)
        assert match is not None, f"unparseable line: {line!r}"
        values[(match.group(1), match.group(2) or "")] = match.group(3)
    return values


def test_counter_inc_and_render_text_is_parseable() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("http_requests_total", labels=("method", "status"))
    counter.inc(1, method="GET", status="200")
    counter.inc(2, method="GET", status="200")
    counter.inc(1, method="POST", status="500")

    values = _parse_prometheus_text(registry.render_text())

    assert values[("http_requests_total", '{method="GET",status="200"}')] == "3"
    assert values[("http_requests_total", '{method="POST",status="500"}')] == "1"


def test_gauge_set_inc_dec() -> None:
    registry = MetricsRegistry()
    gauge = registry.gauge("open_positions", labels=("venue",))
    gauge.set(5, venue="BITGET")
    gauge.inc(2, venue="BITGET")
    gauge.dec(1, venue="BITGET")

    assert gauge.samples()[("BITGET",)] == 6
    values = _parse_prometheus_text(registry.render_text())
    assert values[("open_positions", '{venue="BITGET"}')] == "6"


def test_histogram_observe_cumulative_buckets_and_render() -> None:
    registry = MetricsRegistry()
    hist = registry.histogram("request_latency_seconds", buckets=(0.1, 0.5, 1.0))
    for value in (0.05, 0.2, 0.2, 2.0):
        hist.observe(value)

    values = _parse_prometheus_text(registry.render_text())

    assert values[("request_latency_seconds_bucket", '{le="0.1"}')] == "1"
    assert values[("request_latency_seconds_bucket", '{le="0.5"}')] == "3"
    assert values[("request_latency_seconds_bucket", '{le="1"}')] == "3"
    assert values[("request_latency_seconds_bucket", '{le="+Inf"}')] == "4"
    assert values[("request_latency_seconds_count", "")] == "4"
    assert values[("request_latency_seconds_sum", "")] == repr(0.05 + 0.2 + 0.2 + 2.0)


def test_get_registry_returns_process_wide_singleton() -> None:
    assert get_registry() is get_registry()


def test_counter_inc_label_key_mismatch_raises_value_error() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("orders_total", labels=("venue",))

    with pytest.raises(ValueError):
        counter.inc(1, wrong_label="x")


def test_gauge_set_missing_label_raises_value_error() -> None:
    registry = MetricsRegistry()
    gauge = registry.gauge("open_orders", labels=("venue", "side"))

    with pytest.raises(ValueError):
        gauge.set(1, venue="BITGET")  # "side" 라벨 누락


def test_histogram_observe_extra_label_raises_value_error() -> None:
    registry = MetricsRegistry()
    hist = registry.histogram("latency", buckets=(1.0,), labels=("venue",))

    with pytest.raises(ValueError):
        hist.observe(0.5, venue="BITGET", extra="unexpected")


def test_reregister_counter_with_different_labels_raises_value_error() -> None:
    registry = MetricsRegistry()
    registry.counter("orders_total", labels=("venue",))

    with pytest.raises(ValueError):
        registry.counter("orders_total", labels=("venue", "side"))


def test_reregister_histogram_with_different_buckets_raises_value_error() -> None:
    registry = MetricsRegistry()
    registry.histogram("latency", buckets=(1.0, 2.0))

    with pytest.raises(ValueError):
        registry.histogram("latency", buckets=(1.0, 3.0))


def test_counter_inc_negative_amount_raises_value_error() -> None:
    registry = MetricsRegistry()
    counter = registry.counter("orders_total")

    with pytest.raises(ValueError):
        counter.inc(-1)
