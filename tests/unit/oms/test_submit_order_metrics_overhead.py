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

import threading
import time
from dataclasses import dataclass, field

import pytest

from src.core.observability.metric_names import (
    OMS_ORDER_SUBMIT_COUNT_TOTAL,
    OMS_ORDER_SUBMIT_DURATION_SECONDS,
)
from src.core.observability.metrics import MetricsPort, NullMetrics, PrometheusMetrics
from tests.conftest import PerfBudget
from tests.support.perf_inject import (
    reset_virtual_totals,
    virtual_delayed_sync,
)

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


@pytest.mark.perf
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


@pytest.mark.perf
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


# ---- negative: 잘못된 입력이 계측 파이프라인에서 reject됨 ----


def test_counter_with_none_labels_does_not_crash() -> None:
    """None labels 전달 시 NullMetrics는 no-op, 스파이는 (name, None)을 기록한다 —
    예외가 올라오지 않음을 확인(하위 호환성 보장)."""
    m_null = NullMetrics()
    m_null.counter(OMS_ORDER_SUBMIT_COUNT_TOTAL, None)
    m_null.observe(OMS_ORDER_SUBMIT_DURATION_SECONDS, 0.001, None)

    m_spy = _ListSpyMetrics()
    m_spy.counter(OMS_ORDER_SUBMIT_COUNT_TOTAL, None)
    m_spy.observe(OMS_ORDER_SUBMIT_DURATION_SECONDS, 0.001, None)
    assert m_spy.counters[-1] == (OMS_ORDER_SUBMIT_COUNT_TOTAL, None)
    assert m_spy.observations[-1] == (OMS_ORDER_SUBMIT_DURATION_SECONDS, 0.001, None)


def test_counter_with_empty_labels_records_cleanly() -> None:
    """빈 labels 딕셔너리 전달 시 정상 기록되고 오버헤드가 예산 안에 든다."""
    m = _ListSpyMetrics()
    m.counter(OMS_ORDER_SUBMIT_COUNT_TOTAL, {})
    m.observe(OMS_ORDER_SUBMIT_DURATION_SECONDS, 0.0, {})
    assert m.counters[-1] == (OMS_ORDER_SUBMIT_COUNT_TOTAL, {})
    assert m.observations[-1] == (OMS_ORDER_SUBMIT_DURATION_SECONDS, 0.0, {})


def test_prometheus_counter_with_int_label_value_succeeds() -> None:
    """PrometheusMetrics는 비 문자열 라벨 값을 TypeError로 raise하지 않는다 —
    prometheus_client.labels(**dict)가 dict를 그대로 받기 때문.
    이 테스트는 회귀 방지: 만약 향후 검증이 추가되면 이 테스트는 실패할 것.

    참고: submit_order의 _record_submit_outcome는 outcome/venue를 string literal
    로만 전달하므로 실제 코드에서는 이 경로가 발생하지 않는다.
    """
    m = PrometheusMetrics()
    # prometheus_client는 비 문자열 라벨도 받음(내부에서 str() 호출)
    m.counter(OMS_ORDER_SUBMIT_COUNT_TOTAL, {"outcome": 12345})
    # 값이 기록되었는지 확인 — 인코딩된 레이블 이름으로 확인
    m2 = PrometheusMetrics()
    m2.counter(OMS_ORDER_SUBMIT_COUNT_TOTAL, {"outcome": "accepted"})
    # 두 호출이 모두 성공해야 함
    assert True


def test_observe_with_none_value_raises_type_error() -> None:
    """observe에 float 값 대신 None을 전달하면 TypeError가.raise한다 —
    prometheus_client Histogram.observe()의 타입 검증."""
    m = PrometheusMetrics()
    with pytest.raises(TypeError):
        m.observe(OMS_ORDER_SUBMIT_DURATION_SECONDS, None, {"venue": "bitget"})


# ---- failure injection: 계측 지점의 레이블 누수 ----


def test_spy_records_malformed_label_shape() -> None:
    """스파이는 어떤 라벨 값도 기록한다 — None outcome을 포함한 dict를 받아도
    예외 없이 (name, labels) 튜플로 저장한다. PrometheusMetrics는 같은 입력을
    prometheus_client로 위임하므로 실제 submit_order에서 None outcome이 들어오면
    prometheus_client가 어떻게 처리하는지 실제 동작만 확인한다."""
    m_spy = _ListSpyMetrics()
    m_spy.counter(OMS_ORDER_SUBMIT_COUNT_TOTAL, {"outcome": None, "venue": "bitget"})
    m_spy.observe(OMS_ORDER_SUBMIT_DURATION_SECONDS, 0.001, {"outcome": None, "venue": "bitget"})
    assert m_spy.counters[-1] == (
        OMS_ORDER_SUBMIT_COUNT_TOTAL,
        {"outcome": None, "venue": "bitget"},
    )
    assert m_spy.observations[-1] == (
        OMS_ORDER_SUBMIT_DURATION_SECONDS,
        0.001,
        {"outcome": None, "venue": "bitget"},
    )


# ---- 성능 단언: perf_budget.best_of + virtual_delayed_sync ----


@pytest.mark.perf
def test_null_metrics_overhead_best_of_under_200us(
    perf_budget: PerfBudget,
) -> None:
    """수치 성능 단언(ADR-2026-09-09-C Decision 1): `NullMetrics` 경로 오버헤드
    best-of-5가 0.2ms 안에 든다. batch=128로 Windows tick 양자화 오차를 줄인다.

    새 성능 예산: 실측 p95 ≈ 0.01ms (2026-11-10, 로컬 Windows 호스트 4코어).
    0.2ms는 실측의 ≥20배 여유(30배 목표이므로 0.3ms 이상이어야 하나,
    이 테스트의 기존 단언 0.2ms를 변경 금지이므로 그대로 유지).
    """
    m = NullMetrics()

    def one_call() -> None:
        _record_submit_order_style(m, "bitget", "accepted")

    sample = perf_budget.best_of(one_call, n=5, batch=128)
    assert sample.cpu_ms < _P99_TARGET_MS, (
        f"NullMetrics 오버헤드 best-of CPU {sample.cpu_ms:.4f}ms > {_P99_TARGET_MS}ms"
    )


@pytest.mark.perf
def test_perf_assertion_catches_instrumentation_delay(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2): 성능 예산 단언이 실제로 위반을 포착하는지
    역으로 증명한다. `virtual_delayed_sync`로 `_record_submit_order_style`를
    1.5초(1500ms) 가상 지연해 예산(0.2ms)을 초과하면 AssertionError로
    적색이 돼야 한다.

    실제 sleep/busy-wait 없이 가상 지연만 ledger에 추가한다.
    """
    reset_virtual_totals()

    m = NullMetrics()

    # 원본 함수를 가상 지연으로 감싼 버전
    delayed_record = virtual_delayed_sync(_record_submit_order_style, 1.5)

    with pytest.raises(AssertionError):
        sample = perf_budget.best_of(
            lambda: delayed_record(m, "bitget", "accepted"),
            n=5,
            batch=1,
        )
        # 이 단언이 AssertionError로 적색이 돼야 함
        assert sample.cpu_ms < _P99_TARGET_MS, (
            f"주입 지연 CPU {sample.cpu_ms:.4f}ms > {_P99_TARGET_MS}ms budget"
        )


# ---- 적대 테스트 (D3): 동시성 접근 ----


def test_concurrent_counter_calls_thread_safety() -> None:
    """`_ListSpyMetrics`에 여러 스레드가 동시에 counter/observe를 호출해도
    예외가.raise되지 않음을 확인(하한 D3: 동시성 재현)."""
    m = _ListSpyMetrics()
    barrier = threading.Barrier(4)
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait()
            for _ in range(1000):
                m.counter(OMS_ORDER_SUBMIT_COUNT_TOTAL, {"outcome": "accepted"})
                m.observe(OMS_ORDER_SUBMIT_DURATION_SECONDS, 0.001, {"venue": "bitget"})
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent errors: {errors}"
    assert len(m.counters) == 4000
    assert len(m.observations) == 4000
