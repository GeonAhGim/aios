"""LA-4 — market_data/domain/quality/dedupe.py 순수 규칙 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-4, §8.1, §9.2 LA-4.

핵심 케이스(§8.1): 동일 내용 중복 → 1건 유지(DUPLICATE_IDENTICAL, info),
상이 내용 중복 → CONFLICT(양쪽 격리, DUPLICATE_CONFLICT, reject).
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import cast
from uuid import uuid4

import pytest

from src.foundation.market_data.contracts.v1 import (
    CandleRecord,
    QualityIssueType,
    SeriesKey,
    Severity,
    Timeframe,
    Venue,
)
from src.foundation.market_data.domain.quality.dedupe import DedupeResult, dedupe

UTC = timezone.utc
_KEY = SeriesKey(venue=Venue.BITGET, instrument_id=uuid4(), timeframe=Timeframe.M1)
_OPEN = datetime(2026, 9, 3, 10, 5, tzinfo=UTC)


def _candle(open_time: datetime = _OPEN, **overrides: object) -> CandleRecord:
    fields: dict[str, object] = dict(
        key=_KEY,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=1),
        open=Decimal("100"),
        high=Decimal("110"),
        low=Decimal("90"),
        close=Decimal("105"),
        volume=Decimal("10"),
    )
    fields.update(overrides)
    return CandleRecord(**fields)  # type: ignore[arg-type]


def test_no_duplicates_keeps_everything() -> None:
    a = _candle()
    b = _candle(open_time=_OPEN + timedelta(minutes=1))
    result = dedupe([a, b])
    assert result.kept == (a, b)
    assert result.conflicts == ()
    assert result.issues == ()


def test_identical_duplicate_keeps_one() -> None:
    a = _candle()
    b = _candle()
    result = dedupe([a, b])
    assert result.kept == (a,)
    assert result.conflicts == ()
    assert len(result.issues) == 1
    assert result.issues[0].type is QualityIssueType.DUPLICATE_IDENTICAL
    assert result.issues[0].severity is Severity.INFO


def test_conflicting_duplicate_isolates_both() -> None:
    a = _candle()
    b = _candle(close=Decimal("106"))
    result = dedupe([a, b])
    assert result.kept == ()
    assert result.conflicts == (a, b)
    assert len(result.issues) == 1
    assert result.issues[0].type is QualityIssueType.DUPLICATE_CONFLICT
    assert result.issues[0].severity is Severity.REJECT


def test_mixed_series_dedupes_only_matching_keys() -> None:
    unique = _candle(open_time=_OPEN + timedelta(minutes=5))
    dup_a = _candle()
    dup_b = _candle()
    result = dedupe([unique, dup_a, dup_b])
    assert result.kept == (unique, dup_a)
    assert result.conflicts == ()
    assert len(result.issues) == 1


class _FaultyRecord:
    """비교 시 예외를 던지는 더미 레코드. dedupe는 정적으로 CandleRecord를
    받게 선언돼 있지만 런타임에는 duck-typing으로 동작하므로, `__eq__`
    결함을 주입해 fail-closed(예외 전파, 오분류 없음)를 검증한다."""

    def __init__(self, key: SeriesKey, open_time: datetime) -> None:
        self.key = key
        self.open_time = open_time

    def __eq__(self, other: object) -> bool:
        raise RuntimeError("boom - comparison fault")


def test_dedupe_propagates_equality_fault_without_misclassifying() -> None:
    """실패 주입: 그룹 내 비교가 깨지면 dedupe는 조용히 identical/conflict로
    잘못 분류하지 않고 예외를 그대로 전파해야 한다(fail-closed)."""
    a = _FaultyRecord(_KEY, _OPEN)
    b = _FaultyRecord(_KEY, _OPEN)
    faulty_candles = cast("list[CandleRecord]", [a, b])
    with pytest.raises(RuntimeError):
        dedupe(faulty_candles)


def test_dedupe_handles_large_batch_within_latency_budget() -> None:
    """수치 성능 단언: 20,000개 고유 캔들 + 중복 그룹 하나가 O(n) 유지 하에
    3초 예산 안에 끝난다."""
    unique = [_candle(open_time=_OPEN + timedelta(minutes=i)) for i in range(20_000)]
    dup_group = [_candle(open_time=_OPEN + timedelta(days=365)) for _ in range(50)]
    start = time.perf_counter()
    result = dedupe(unique + dup_group)
    elapsed = time.perf_counter() - start
    assert elapsed < 3.0
    assert len(result.kept) == 20_000 + 1


def test_three_member_group_two_identical_one_different_isolates_all_three() -> None:
    """게이트 적색 재현: 3건 그룹에서 2건만 동일하고 1건이 다르면, 다른 1건만
    격리하는 게 아니라 그룹 전체(3건)를 conflicts로 격리해야 한다 —
    `all(m == members[0] ...)`이 members[0] 기준 비교이므로 이를 우회해
    부분만 격리하는 회귀를 잡는다."""
    a = _candle()
    b = _candle()
    c = _candle(close=Decimal("999"))
    result = dedupe([a, b, c])
    assert result.kept == ()
    assert result.conflicts == (a, b, c)
    assert len(result.issues) == 1
    assert result.issues[0].type is QualityIssueType.DUPLICATE_CONFLICT


def test_kept_order_matches_first_seen_group_order_when_interleaved() -> None:
    """게이트 적색 재현: 그룹이 인터리빙되어 들어와도 kept 순서는 그룹이
    처음 등장한 순서를 따라야 한다 — key로 정렬해버리는 회귀를 잡는다."""
    t1a = _candle(open_time=_OPEN + timedelta(minutes=1))
    t0 = _candle(open_time=_OPEN)
    t1b = _candle(open_time=_OPEN + timedelta(minutes=1))
    t2 = _candle(open_time=_OPEN + timedelta(minutes=2))
    result = dedupe([t1a, t0, t1b, t2])
    assert result.kept == (t1a, t0, t2)


def test_concurrent_dedupe_calls_are_deterministic() -> None:
    """동시성/replay 증명: 순수 함수이므로 20개 스레드가 동일 입력에 동시
    접근해도 전부 동일한 결과를 내야 한다."""
    candles = [_candle(), _candle(), _candle(open_time=_OPEN + timedelta(minutes=1))]
    results: list[DedupeResult] = []

    def _run() -> None:
        results.append(dedupe(candles))

    threads = [threading.Thread(target=_run) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 20
    first = results[0]
    for r in results[1:]:
        assert r == first
