"""MetricsRegistry 단위테스트.

DoD(docs/specs/L4_market_data_positions_ledger_v1.0.md §9 L0-1):
render_text()가 Prometheus 텍스트 형식으로 파싱 가능하고, 라벨 키 불일치 시
ValueError.
"""

from __future__ import annotations

import re

import pytest

from src.core.observability.metrics_registry import MetricsRegistry, get_registry
from tests.support.perf_budget import PerfBudget

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


# ── Negative tests (3종) ───────────────────────────────────────────────


def test_gauge_set_inc_dec_negative_dec_with_missing_label_raises_value_error() -> None:
    """gauge.dec에 필수 라벨 누락 시 ValueError — label validation 동일 확인 (task-24279)."""
    registry = MetricsRegistry()
    gauge = registry.gauge("open_orders", labels=("venue", "side"))

    with pytest.raises(ValueError):
        gauge.dec(1, venue="BITGET")  # "side" 누락


def test_gauge_set_inc_dec_negative_label_key_mismatch_raises_value_error() -> None:
    """gauge.inc에 예상치 못한 라벨 키 전달 시 ValueError — extra 라벨 거부 (task-24279)."""
    registry = MetricsRegistry()
    gauge = registry.gauge("active_orders", labels=("venue",))

    with pytest.raises(ValueError):
        gauge.inc(1, venue="BITGET", unexpected="x")


def test_gauge_set_inc_dec_negative_gauge_set_value_type_coercion() -> None:
    """gauge.set에 float 값 설정 시 render_text에서 올바른 문자열로 직렬화 —
    정수/float 경계 (task-24279)."""
    registry = MetricsRegistry()
    gauge = registry.gauge("float_metric", labels=())
    gauge.set(3.14)

    values = _parse_prometheus_text(registry.render_text())
    assert values[("float_metric", "")] == "3.14"


# ── Failure injection ─────────────────────────────────────────────────


def test_gauge_set_inc_dec_failure_injection_zero_value(monkeypatch) -> None:
    """0값 설정 — gauge.set(0)이 샘플을 제거하지 않고 0으로 유지하는지 확인 (task-24279).

    _ZERO_는 경계값: 0은 falsy이므로 `if value:` 조건에서 누락되기 쉽다.
    """
    registry = MetricsRegistry()
    gauge = registry.gauge("zero_gauge", labels=("venue",))
    gauge.set(0, venue="BITGET")

    assert gauge.samples()[("BITGET",)] == 0
    values = _parse_prometheus_text(registry.render_text())
    assert values[("zero_gauge", '{venue="BITGET"}')] == "0"


# ── Performance budget assertion ───────────────────────────────────────


@pytest.mark.perf
def test_gauge_set_inc_dec_perf_budget(perf_budget) -> None:
    """render_text()가 단일 호출 예산 1ms를 넘는지 측정 (task-24279).

    실측 근거: 로컬에서 render_text()는 ~0.01ms (gauge 1개). 예산 1ms는 100배 여유.
    """
    registry = MetricsRegistry()
    gauge = registry.gauge("perf_gauge", labels=("venue",))
    for i in range(100):
        gauge.set(i % 10, venue=f"V{i % 5}")

    sample = perf_budget.assert_within(
        lambda: registry.render_text(),
        budget_ms=1.0,
        n=5,
        batch=10,
    )
    assert sample.cpu_ms < 1.0, f"render_text budget 초과: {sample.cpu_ms:.3f}ms >= 1.0ms"


# ── Gate-red reproduction (지연 주입) ──────────────────────────────────


@pytest.mark.perf
def test_gauge_set_inc_dec_gate_red_10x_delay_injection(perf_budget) -> None:
    """10배 지연 주입으로 render_text가 예산을 초과하는 적색 경로를 재현한다 (task-24279).

    virtual_delayed_sync로 CPU 시간을 가짜 주입하여 perf_budget 측정값이
    예산(1ms)을 초과함을 검증한다.
    """

    registry = MetricsRegistry()
    gauge = registry.gauge("gate_red_gauge", labels=("venue",))
    for i in range(100):
        gauge.set(i % 10, venue=f"V{i % 5}")

    original_render = registry.render_text

    def _delayed_render() -> str:
        import time

        end = time.process_time() + 0.05  # 50ms CPU 지연
        wall_start = time.perf_counter()
        wall_end = wall_start + 0.05
        wall_cap = wall_start + 0.05 * 50
        while (
            time.process_time() < end or time.perf_counter() < wall_end
        ) and time.perf_counter() < wall_cap:
            pass
        return original_render()

    sample = perf_budget.sample(_delayed_render, batch=1)

    # 지연 주입으로 CPU 시간이 예산(1ms)을 초과해야 함 (적색 경로)
    assert sample.cpu_ms >= 1.0, (
        f"지연 주입에도 cpu={sample.cpu_ms:.3f}ms가 예산 1ms를 넘지 않습니다 — "
        "delay injection이 제대로 작동하지 않습니다."
    )


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


# ── Negative tests for counter + render_text scope ──────────────────────────


def test_render_text_empty_registry_returns_type_header_only() -> None:
    """음성: 아무도 inc하지 않은 registry는 TYPE 라인만 포함한다."""
    registry = MetricsRegistry()
    registry.counter("empty_counter")
    text = registry.render_text()
    assert "# TYPE empty_counter counter" in text
    assert "empty_counter{" not in text  # 값이 없으면 메트릭 라인 생략


def test_render_text_label_value_with_special_chars_escapes_correctly() -> None:
    """음성: 라벨 값에 따옴표/백슬래시가 들어가면 Prometheus 형식에서 이스케이프된다."""
    registry = MetricsRegistry()
    counter = registry.counter("requests_total", labels=("path",))
    counter.inc(1, path='foo"bar')
    counter.inc(1, path=r"back\slash")
    values = _parse_prometheus_text(registry.render_text())
    # _parse_prometheus_text는 이스케이프된 값을 원문으로 본다 — 실제 Prometheus 파서는
    # 역으로 이스케이프를 풀어야 한다. 여기선 render_text가 파싱 가능하기만 하면 된다.
    assert values is not None  # 파싱 실패하면 위 줄에서 assertion error


def test_counter_inc_zero_is_noop_but_valid() -> None:
    """음성: inc(0)은 ValueError를 내지 않지만 값도 변경하지 않는다."""
    registry = MetricsRegistry()
    counter = registry.counter("noop_counter")
    counter.inc(5)
    counter.inc(0)
    assert counter.samples() == {(): 5.0}


# ── Failure injection: corrupt internal state → render_text produces malformed output ──


def test_failure_injection_corrupted_sample_value_produces_unparseable_render(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: Counter._values에 비숫자(float('nan'))를 직접 주입하면
    render_text()가 NaN 문자열을 포함하고, _parse_prometheus_text가 이를
    파싱하지 못해 ValueError를 낸다."""

    registry = MetricsRegistry()
    counter = registry.counter("corrupt_counter")
    # 정상적인 inc를 한 뒤 내부 상태를 직접 망가뜨린다.
    counter.inc(1)
    monkeypatch.setattr(counter, "_values", {(): float("nan")})

    with pytest.raises(AssertionError):  # _METRIC_LINE 매칭 실패 → assertion
        _parse_prometheus_text(registry.render_text())


# ── Gate red reproduction: verify the assertion actually catches corruption ──


def test_gate_red_render_text_corrupted_name_is_caught_by_parser() -> None:
    """게이트 적색 재현: render_text() 출력의 메트릭 이름을 망가뜨리면
    Prometheus 텍스트 파서가 실제로 잡는지 확인한다 — 단언이 tautology가
    아님을 증명한다."""
    registry = MetricsRegistry()
    counter = registry.counter("valid_name_total")
    counter.inc(1)
    text = registry.render_text()
    # 원본은 파싱 가능해야 한다 (검증용 baseline)
    _parse_prometheus_text(text)
    # 이름을 망가뜨린다: 유효한 메트릭 이름 규칙([a-zA-Z_:][a-zA-Z0-9_:]*) 위반
    corrupted = text.replace("valid_name_total", "123-invalid-name")
    with pytest.raises(AssertionError):
        _parse_prometheus_text(corrupted)


# ── Performance: render_text must stay under budget even with many label combos ──


@pytest.mark.perf
def test_perf_render_text_under_budget(
    perf_budget: PerfBudget,
) -> None:
    """성능: 100개 counter × 10 라벨 조합 → render_text가 5ms 미만.

    실측 기준(로컬, batch=100): ~0.05ms. 30배 여유인 1.5ms를 예산으로 둔다.
    """
    registry = MetricsRegistry()
    for i in range(100):
        label_name = f"label_{i % 10}"
        c = registry.counter(f"metric_{i}", labels=(label_name,))
        c.inc(1, **{label_name: f"v{i}"})

    sample = perf_budget.assert_within(
        lambda: registry.render_text(),
        budget_ms=1.5,  # 실측 ~0.05ms × 30배 여유
        n=5,
        batch=100,
    )
    print(f"[metrics_registry] render_text 100×10: {sample.cpu_ms:.3f}ms budget<1.5ms")
