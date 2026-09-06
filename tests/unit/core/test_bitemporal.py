"""FA-9 순수 규칙 단위테스트 — DB 없음.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#FA-9, §8
"계약: 4종 양시간축 질의 스냅샷" + "적대적: ... 구간 겹침" negative test.

시나리오: 1/1~1/10 valid 구간의 포지션 값이 원래 100으로 기록됐다가(tx
Jan1~Jan5) 1/5에 120으로 정정됐다(tx Jan5~무한대, FA-A2 방식 — UPDATE가
아니라 새 행 + 이전 행 tx_to 마감). 1/10 이후 valid 구간은 정정 없이 90.
"""
from __future__ import annotations

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
