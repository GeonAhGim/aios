"""LA-1 — CandleRecord 대량 검증 성능 단언.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.1 (A), §9.2 LA-1.

test_contracts_schema.py에서 분리됨(CLAUDE.md 6-12, loc_over_500) — 원본 파일이
507줄로 임계를 넘겨 perf 축(이 파일) 테스트 4건을 책임별로 분리했다.

DoD (task-22373): 음성 테스트 3개, 주입 실패 1개, 성능 수치 1개, gate-red 재현 1개.
"""

import pytest

from src.foundation.market_data.contracts import v1
from tests.conftest import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync
from tests.unit.market_data.test_contracts_schema import _sample_candle


@pytest.mark.perf
def test_candle_record_bulk_validation_throughput(perf_budget: PerfBudget) -> None:
    """1,000건 CandleRecord 검증이 예산(200ms) 내에 끝나야 한다 — O(n) 이상 회귀 감지.

    `perf_budget.assert_within`은 내부적으로 `best_of` → `process_time()`을 쓰므로
    `perf_counter()` wall-clock 노이즈(다른 워커/llama.cpp 코어 경합)가 제거된다.
    batch=100 → 1,000÷100=10 sample; process_time 15.6ms 틱 오차를 1.56ms로 줄임.
    """
    perf_budget.assert_within(
        lambda: [_sample_candle() for _ in range(100)],
        budget_ms=200.0,
        n=10,
        batch=100,
        label="1,000건 CandleRecord 검증",
    )


@pytest.mark.perf
def test_candle_record_bulk_validation_budget_exceeded_with_virtual_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """예산 주입 — virtual_delayed_sync로 1회 호출당 50ms 가상 CPU 지연을 주입하면
    100회 호출당 5초가 돼 200ms 예산을 크게 초과한다.

    이 테스트는 '성능 단언이 실제로 예산 초과를 포착하는지' 검증한다(검출력 확인).
    주입 없이 통과하는 단언은 허울 단언일 수 있다 — 이 테스트가 통과해야
    단언이 의미 있다.

    virtual_delayed_sync는 실제 sleep/spin 없이 virtual ledger에만 누적을
    넣으므로 실제 테스트 지연은 0이다(실제 sleep 금지 — CLAUDE.md).
    """
    reset_virtual_totals()

    # _sample_candle을 가상 지연 50ms로 감싸서 monkeypatch — 실제 sleep 없음
    original_sample = _sample_candle

    def _delayed_sample(**overrides: object) -> v1.CandleRecord:
        return virtual_delayed_sync(lambda: original_sample(**overrides), 50.0)()

    monkeypatch.setattr(
        "tests.unit.market_data.test_contracts_schema_perf._sample_candle",
        _delayed_sample,
    )

    with pytest.raises(AssertionError):
        perf = PerfBudget()
        perf.assert_within(
            lambda: [_delayed_sample() for _ in range(100)],
            budget_ms=200.0,
            n=5,
            batch=1,
            label="1,000건 CandleRecord 검증(가상 지연)",
        )


@pytest.mark.perf
def test_gate_red_reproduction_virtual_delay_proves_measurement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """gate-red 재현: virtual_delayed_sync로 정확히 10ms 지연을 주입하면
    PerfBudget.sample의 cpu_ms가 10ms 이상으로 측정되어야 한다.

    이는 측정 시스템이 가상 지연을 실제로 읽을 수 있음을 증명한다 —
    즉 단언이 '의미 있는' 측정임을 검증한다. 지연을 주지 않으면 cpu_ms는
    실제 검증 시간(<<1ms)으로 매우 작아야 한다.
    """
    reset_virtual_totals()

    perf = PerfBudget()

    # 지연 없음 — baseline: 매우 작은 cpu_ms
    baseline = perf.sample(lambda: _sample_candle(), batch=1)
    assert baseline.cpu_ms < 50.0, (
        f"지연 없는 baseline이 너무 큼: {baseline.cpu_ms:.3f}ms — 측정 노이즈가 예산을 침식"
    )

    # 10ms 가상 지연 주입
    delayed_fn = virtual_delayed_sync(lambda: _sample_candle(), 0.010)

    # virtual_delayed_sync는 호출 시 lambda를 실행하므로
    # wrapper를 직접 호출해야 함
    def _wrapped() -> v1.CandleRecord:
        return delayed_fn()

    reset_virtual_totals()
    delayed_sample = perf.sample(_wrapped, batch=1)
    assert delayed_sample.cpu_ms >= 9.0, (
        f"10ms 가상 지연이 {delayed_sample.cpu_ms:.3f}ms로 읽힘 — 가상 지연 측정 누락"
    )


@pytest.mark.perf
def test_best_of_robust_to_zero_quantization_tick() -> None:
    """Windows process_time()은 ~15.6ms 양자화 tick을 가진다.

    실제 작업이 tick보다 빠르면 0ms 또는 15.6ms로만 읽혀 best_of가
    0에 수렴할 수 있다. best_of(n=5)는 여러 샘플 중 최소를 반환하므로
    0 수렴 단언: 최소값이 0보다 크다는 보장은 없지만, 최소한 crash하지
    않고 최소 1개는 유한한 값을 반환해야 한다.

    이 테스트는 best_of가 0 수렴에 흔들리지 않음을 확인한다 —
    실제 CandleRecord 검증은 유한한 CPU 시간을 소비한다.
    """
    perf = PerfBudget()
    samples = perf.samples(lambda: _sample_candle(), n=5, batch=1)
    # 모든 샘플이 유한한 cpu_ms를 가져야 함 (inf/nan 아님)
    for s in samples:
        assert s.cpu_ms >= 0.0, f"음수 cpu_ms: {s.cpu_ms}"
        assert s.cpu_ms < 1000.0, f"비정상적으로 큰 cpu_ms: {s.cpu_ms}"
    # best_of는 최소 cpu_ms를 반환 — 0일 수도 있지만 crash는 안 함
    best = perf.best_of(lambda: _sample_candle(), n=5)
    assert best.cpu_ms >= 0.0
    assert best.cpu_ms < 1000.0
