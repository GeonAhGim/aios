"""LA-1 — CandleRecord 대량 검증 성능 단언.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.1 (A), §9.2 LA-1.

test_contracts_schema.py에서 분리됨(CLAUDE.md 6-12, loc_over_500) — 원본 파일이
507줄로 임계를 넘겨 perf 축(이 파일) 테스트 2건을 책임별로 분리했다.
"""

import pytest

from tests.conftest import PerfBudget
from tests.unit.market_data.test_contracts_schema import _sample_candle


@pytest.mark.perf
def test_candle_record_bulk_validation_throughput(perf_budget: PerfBudget) -> None:
    """1,000건 CandleRecord 검증이 예산(200ms) 내에 끝나야 한다 — O(n) 이상 회귀 감지.

    `perf_budget.assert_within`은 내부적으로 `process_time()`을 쓰므로
    `perf_counter()` wall-clock 노이즈(다른 워커/llama.cpp 코어 경합)가 제거된다.
    batch=100 → 1,000÷100=10 sample; process_time 15.6ms 틱 오차를 1.56ms로 줄임.
    """
    # batch=100: 1,000회 / 100 = 10회 측정, process_time 15.6ms 틱 오차 1.56ms로 감소
    perf_budget.assert_within(
        lambda: [_sample_candle() for _ in range(100)],
        budget_ms=200.0,
        n=10,
        batch=1,
        label="1,000건 CandleRecord 검증",
    )


@pytest.mark.perf
def test_candle_record_bulk_validation_budget_exceeded_with_injected_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """예산 주입 지연 — perf_budget 내부의 process_time()을 monkeypatch해
    1회 호출당 50ms CPU를 주입하면 100회 호출당 5초가 돼 200ms 예산을
    크게 초과한다.

    이 테스트는 '성능 단언이 실제로 예산 초과를 포착하는지' 검증한다(검출력 확인).
    주입 없이 통과하는 단언은 허울 단언일 수 있다 — 이 테스트가 통과해야
    단언이 의미 있다.
    """
    import time

    cumulative: list[float] = [0.0]

    def _fake_process_time() -> float:
        cumulative[0] += 0.250  # 250ms CPU per call → delta=500ms/sample > 200ms 예산
        return cumulative[0]

    monkeypatch.setattr(time, "process_time", _fake_process_time)

    with pytest.raises(AssertionError):
        perf = PerfBudget()
        perf.assert_within(
            lambda: [_sample_candle() for _ in range(100)],
            budget_ms=200.0,
            n=5,
            batch=1,
            label="1,000건 CandleRecord 검증(주입 지연)",
        )
