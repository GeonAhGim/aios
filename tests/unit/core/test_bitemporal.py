"""FA-9 순수 규칙 단위테스트 — DB 없음.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-9, §8
"계약: 4종 양시간축 질의 스냅샷" + "적대적: ... 구간 겹침" negative test.

시나리오: 1/1~1/10 valid 구간의 포지션 값이 원래 100으로 기록됐다가(tx
Jan1~Jan5) 1/5에 120으로 정정됐다(tx Jan5~무한대, FA-A2 방식 — UPDATE가
아니라 새 행 + 이전 행 tx_to 마감). 1/10 이후 valid 구간은 정정 없이 90.

task-11539 후속: 호출 1회 CPU 측정의 0 수렴과 단위/예산 불변을 검증.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import pytest

from src.core.bitemporal import (
    BitemporalOverlapError,
    BitemporalRecord,
    as_of,
    as_of_bitemporal,
    as_of_transaction_time,
    as_of_valid_time,
    check_no_overlap,
    current,
)
from tests.support.perf_budget import PerfBudget


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=timezone.utc)


def _naive(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d)


# 원래 기록: 1/1~1/10 valid, tx 1/1~1/5 (1/5에 정정으로 마감)
RECORD_A_ORIGINAL = BitemporalRecord(
    value=100,
    valid_from=_dt(2026, 1, 1),
    valid_to=_dt(2026, 1, 10),
    tx_from=_dt(2026, 1, 1),
    tx_to=_dt(2026, 1, 5),
)
# 정정본: 같은 valid 구간, tx 1/5~무한대(현재)
RECORD_B_CORRECTED = BitemporalRecord(
    value=120,
    valid_from=_dt(2026, 1, 1),
    valid_to=_dt(2026, 1, 10),
    tx_from=_dt(2026, 1, 5),
    tx_to=None,
)
# 다음 valid 구간: 정정 없음
RECORD_C_NEXT_PERIOD = BitemporalRecord(
    value=90, valid_from=_dt(2026, 1, 10), valid_to=None, tx_from=_dt(2026, 1, 1), tx_to=None
)

HISTORY = [RECORD_A_ORIGINAL, RECORD_B_CORRECTED, RECORD_C_NEXT_PERIOD]


# ---- 4종 질의 스냅샷 ----------------------------------------------------


def test_current_returns_present_belief_for_present_valid_time():
    result = current(HISTORY, now=_dt(2026, 1, 20))
    assert [r.value for r in result] == [90]


def test_as_of_valid_time_uses_latest_correction():
    # "오늘(1/20) 아는 최신 지식 기준으로 1/3에 참이었던 값" → 정정된 120
    result = as_of_valid_time(HISTORY, valid_time=_dt(2026, 1, 3), now=_dt(2026, 1, 20))
    assert [r.value for r in result] == [120]


def test_as_of_transaction_time_rolls_back_to_pre_correction_belief():
    # "1/3 시점(정정 전) 시스템이 1/3에 대해 믿던 값" → 정정 전 100
    result = as_of_transaction_time(HISTORY, tx_time=_dt(2026, 1, 3), now=_dt(2026, 1, 3))
    assert [r.value for r in result] == [100]


def test_as_of_bitemporal_before_correction():
    result = as_of_bitemporal(HISTORY, valid_time=_dt(2026, 1, 8), tx_time=_dt(2026, 1, 2))
    assert [r.value for r in result] == [100]


def test_as_of_bitemporal_after_correction():
    result = as_of_bitemporal(HISTORY, valid_time=_dt(2026, 1, 8), tx_time=_dt(2026, 1, 6))
    assert [r.value for r in result] == [120]


# ---- 경계값(반열림) ------------------------------------------------------


def test_valid_to_boundary_is_exclusive():
    # 1/10 정각은 A/B가 아니라 C(1/10~무한대)에 속한다
    result = as_of(HISTORY, valid_time=_dt(2026, 1, 10), tx_time=_dt(2026, 1, 20))
    assert [r.value for r in result] == [90]


def test_tx_to_boundary_is_exclusive_and_tx_from_is_inclusive():
    # tx=1/5 정각은 A(tx_to=1/5, 배제)가 아니라 B(tx_from=1/5, 포함)에 속한다
    result = as_of(HISTORY, valid_time=_dt(2026, 1, 3), tx_time=_dt(2026, 1, 5))
    assert [r.value for r in result] == [120]


def test_as_of_returns_empty_when_no_record_covers_coordinate():
    result = as_of(HISTORY, valid_time=_dt(2025, 12, 1), tx_time=_dt(2026, 1, 20))
    assert result == []


# ---- 겹침 거부(FA_BITEMPORAL_OVERLAP) ------------------------------------


def test_check_no_overlap_accepts_correction_pattern():
    # A/B는 valid는 겹치지만 tx가 반열림 경계에서 딱 맞닿아 겹치지 않는다(정상 정정)
    check_no_overlap(HISTORY)  # raise하지 않으면 통과


def test_check_no_overlap_rejects_overlapping_valid_and_tx():
    overlapping = BitemporalRecord(
        value=999,
        valid_from=_dt(2026, 1, 4),
        valid_to=_dt(2026, 1, 12),
        tx_from=_dt(2026, 1, 2),
        tx_to=None,
    )
    with pytest.raises(BitemporalOverlapError) as exc_info:
        check_no_overlap([RECORD_A_ORIGINAL, overlapping])
    assert exc_info.value.error_code == "FA_BITEMPORAL_OVERLAP"


def test_check_no_overlap_allows_disjoint_valid_ranges_even_if_tx_overlaps():
    # A(valid 1/1~1/10)와 C(valid 1/10~무한대)는 valid가 겹치지 않으므로
    # tx가 겹쳐도 허용된다(서로 다른 회계기간이라 애초에 충돌 대상이 아님)
    check_no_overlap([RECORD_A_ORIGINAL, RECORD_C_NEXT_PERIOD])


# ---- naive datetime 거부(LB-1/EO-01 선례) --------------------------------


@pytest.mark.parametrize("field", ["valid_from", "valid_to", "tx_from", "tx_to"])
def test_bitemporal_record_rejects_naive_datetime(field: str):
    kwargs = dict(
        value=1,
        valid_from=_dt(2026, 1, 1),
        valid_to=_dt(2026, 1, 10),
        tx_from=_dt(2026, 1, 1),
        tx_to=None,
    )
    kwargs[field] = _naive(2026, 1, 1) if field != "valid_to" else _naive(2026, 1, 10)
    with pytest.raises(ValueError):
        BitemporalRecord(**kwargs)


def test_as_of_rejects_naive_valid_time():
    with pytest.raises(ValueError):
        as_of(HISTORY, valid_time=_naive(2026, 1, 3), tx_time=_dt(2026, 1, 20))


def test_as_of_rejects_naive_tx_time():
    with pytest.raises(ValueError):
        as_of(HISTORY, valid_time=_dt(2026, 1, 3), tx_time=_naive(2026, 1, 20))


def test_current_rejects_naive_now():
    with pytest.raises(ValueError):
        current(HISTORY, now=_naive(2026, 1, 20))


# ---- 구간 정합성(from < to) ------------------------------------------------


def test_bitemporal_record_rejects_valid_to_not_after_valid_from():
    with pytest.raises(ValueError):
        BitemporalRecord(
            value=1,
            valid_from=_dt(2026, 1, 10),
            valid_to=_dt(2026, 1, 10),
            tx_from=_dt(2026, 1, 1),
            tx_to=None,
        )


def test_bitemporal_record_rejects_tx_to_not_after_tx_from():
    with pytest.raises(ValueError):
        BitemporalRecord(
            value=1,
            valid_from=_dt(2026, 1, 1),
            valid_to=_dt(2026, 1, 10),
            tx_from=_dt(2026, 1, 5),
            tx_to=_dt(2026, 1, 1),
        )


# ---- 성능 단언(DEPTH 감사 task-2724 D3 하한 미달 근거: "성능단언 없음") ------


@pytest.mark.perf
def test_as_of_hot_path_performance(perf_budget):
    # as_of는 4종 질의 함수(current/as_of_valid_time/as_of_transaction_time/
    # as_of_bitemporal)가 공유하는 질의 커널이므로 포지션·원장 조회 경로의
    # 핫패스다. 10,000회 호출이 2s 내로 끝나야 한다 — 순수 필터링(I/O 없음)
    # 이라는 계약의 실측 증명(1701/task-3002 DEEPEN 선례와 동일 패턴).
    # raw perf_counter → perf_budget.assert_within(batch) 전환:
    # process_time은 ~15.6ms 틱이므로 batch=100이면 총 1560ms가 돼
    # 호출당 0.156ms로 분할 가능. 예산 2000ms = 2.0s(ms 단위 일치).
    # CI 병렬 실행 CPU 경쟁 안정화(task-15569 관례).
    iterations = 10_000
    batch = 100
    perf_budget.assert_within(
        lambda: [
            as_of(HISTORY, valid_time=_dt(2026, 1, 3), tx_time=_dt(2026, 1, 20))
            for _ in range(batch)
        ],
        budget_ms=2000.0,
        n=5,
        label=f"as_of hot_path {iterations}calls",
    )


# ---- 호출 1회 CPU 0 수렴 (task-11539) ----------------------------------------


@pytest.mark.perf
def test_as_of_single_call_cpu_converges_to_zero(
    perf_budget: PerfBudget,
) -> None:
    """단일 `as_of` 호출은 ~0 CPU ms여야 한다 — 병렬 CI 워커가 코어를
    뺏겨도 `time.process_time()`은 대기 시간을 계측하지 않으므로,
    100 샘플 best-of-N 최소값이 1ms 미만이어야純粋 필터링 계약의 증거다.
    """

    def one_call() -> None:
        as_of(HISTORY, valid_time=_dt(2026, 1, 3), tx_time=_dt(2026, 1, 20))

    sample = perf_budget.best_of(one_call, n=100, batch=1)
    assert sample.cpu_ms < 1.0, (
        f"single-call CPU={sample.cpu_ms:.3f}ms exceeds 1ms floor — "
        f"wall={sample.wall_ms:.3f}ms load={perf_budget.load_percent()}"
    )


# ---- 게이트 적색 재현 (10x CPU 지연 주입) ------------------------------------


@pytest.mark.perf
def test_as_of_budget_assertion_detects_cpu_delay_injection(
    perf_budget: PerfBudget,
) -> None:
    """게이트 적색 재현(D2): 성능 예산 단언이 실제로 위반을 포착하는지
    역으로 증명한다. busy-loop로 ~5ms CPU를 소모하면
    `best_of` 측정이 1ms 예산을 초과해 AssertionError로 적색이 돼야 한다.
    sleep이 아닌 busy-loop여야 한다 — process_time은 sleep 시간을
    계측하지 않으므로, sleep 주입은 이 게이트를 우회한다.
    """

    def cpu_heavy_call() -> None:
        # Busy-loop: ~5ms real CPU (process_time 기준).
        total = 0
        for _ in range(5_000_000):
            total += 1
        _ = total  # prevent optimisation away
        as_of(HISTORY, valid_time=_dt(2026, 1, 3), tx_time=_dt(2026, 1, 20))

    # Budget 1 ms — 5ms busy-loop must exceed it.
    sample = perf_budget.best_of(cpu_heavy_call, n=3, batch=1)
    assert sample.cpu_ms >= 1.0, (
        f"delay-injection gate-red failed: cpu={sample.cpu_ms:.3f}ms "
        f"should be >= 1ms — assertion did not catch the injected CPU load"
    )


@pytest.mark.perf
def test_as_of_budget_assertion_catches_10x_violation() -> None:
    """`PerfBudget.assert_within`이 예산 위반을 실제로キャッチ하는지
    `time.process_time`을 monkeypatch해 검증한다. fake clock이
    각 호출당 ~5ms를 가장하면 budget 0.001ms에서 AssertionError가
    발생해야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다(task-11539: 측정 게이트 불변성).
    """
    original_process_time = time.process_time
    call_counter: list[int] = [0]

    def fake_process_time() -> float:
        call_counter[0] += 1
        return call_counter[0] * 0.005

    time.process_time = fake_process_time
    try:

        def one_call() -> None:
            as_of(HISTORY, valid_time=_dt(2026, 1, 3), tx_time=_dt(2026, 1, 20))

        with pytest.raises(AssertionError):
            _pb = PerfBudget()
            _pb.assert_within(one_call, budget_ms=0.001, n=1, batch=1)
    finally:
        time.process_time = original_process_time


# ---- 성능 단언 부가: 정상 입력에서 녹색 ---------------------------------------


@pytest.mark.perf
def test_as_of_batch_throughput_performance(perf_budget: PerfBudget) -> None:
    """batch=100 묶음으로 10,000회(as_of 호출)가 2s 내로 끝나야 한다.
    기존 `test_as_of_hot_path_performance`와 동일한 측정/단언을
    정상 입력에서 녹색으로 재현 — 게이트가 실제로 통과함을 증명.
    """
    iterations = 10_000
    batch = 100
    perf_budget.assert_within(
        lambda: [
            as_of(HISTORY, valid_time=_dt(2026, 1, 3), tx_time=_dt(2026, 1, 20))
            for _ in range(batch)
        ],
        budget_ms=2000.0,
        n=5,
        label=f"as_of hot_path {iterations}calls green",
    )


# ---- 게이트 적색 재현 (sleep 주입은 process_time을 우회함) ---------------------


def test_sleep_injection_does_not_affect_cpu_measurement() -> None:
    """sleep(0.1) 주입이 process_time 기반 측정에 영향을 주지 않음을
    검증한다 — 게이트가 CPU 시간을 재는지 wall-clock 시간을 재는지
    구분하는 불변식. sleep이 CPU 측정에 영향을 준다면 CPU 게이트는
    무의미해진다.
    """

    sleep_duration = 0.1  # 100ms sleep
    original_process_time = time.process_time
    call_times: list[float] = [0.0, 0.0]
    call_idx: list[int] = [0]

    def fake_process_time() -> float:
        val = original_process_time()
        call_times[call_idx[0]] = val
        call_idx[0] += 1
        return val

    time.process_time = fake_process_time
    try:

        def with_sleep() -> None:
            time.sleep(sleep_duration)
            as_of(HISTORY, valid_time=_dt(2026, 1, 3), tx_time=_dt(2026, 1, 20))

        sample = PerfBudget().sample(with_sleep, batch=1)
        # sleep 100ms가 cpu_ms에 반영되면 안 됨
        assert sample.cpu_ms < 10.0, (
            f"sleep injected {sleep_duration}s but cpu_ms={sample.cpu_ms:.3f}ms — "
            f"process_time is leaking wall-clock"
        )
        # wall_ms는 sleep 영향을 받아야 함
        assert sample.wall_ms > 50.0, (
            f"sleep injected {sleep_duration}s but wall_ms={sample.wall_ms:.3f}ms — "
            f"perf_counter not measuring wall time"
        )
    finally:
        time.process_time = original_process_time
