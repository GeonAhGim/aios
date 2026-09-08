"""DC-15 — `adapters/storage/tiering.py` 단위테스트(로컬 파일 + 인메모리 fake hot, 실 DB 불필요).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.2 DC-15.
DoD (a)~(d)를 각각 하나 이상의 테스트로 반증한다.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from src.foundation.market_data.adapters.storage.tiering import (
    VerificationFailedError,
    promote_year,
    read_lineage,
)
from src.foundation.market_data.adapters.storage.warm_parquet import WarmParquetStorage
from src.foundation.market_data.contracts.v1 import SeriesKey, Timeframe, Venue
from src.foundation.market_data.contracts.v2.candle_lineage import SourceKind
from src.foundation.market_data.domain.candle_columns import CandleColumns

_KEY = SeriesKey(venue=Venue.BINANCE, instrument_id=uuid4(), timeframe=Timeframe.D1)


def _empty_columns() -> CandleColumns:
    return CandleColumns(ts=[], open=[], high=[], low=[], close=[], volume=[], quote_volume=[])


def _daily_columns(year: int, days: list[int]) -> CandleColumns:
    ts = [datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=d) for d in days]
    n = len(ts)
    return CandleColumns(
        ts=ts,
        open=[Decimal("100.1000000000") + Decimal(i) for i in range(n)],
        high=[Decimal("101.5000000000") + Decimal(i) for i in range(n)],
        low=[Decimal("99.0000000000") + Decimal(i) for i in range(n)],
        close=[Decimal("100.9000000000") + Decimal(i) for i in range(n)],
        volume=[Decimal("12.3400000000") + Decimal(i) for i in range(n)],
        quote_volume=[None if i % 2 else Decimal("1234.5600000000") for i in range(n)],
    )


@dataclass
class _FakeHot:
    """인메모리 hot 계층 fake — `HotYearSource` Protocol 구현. `read_year`
    가 반환하는 스냅샷은 실제 DB 조회처럼 매 호출 재계산되지 않고 그대로
    보관된 `CandleColumns`이다(deterministic 테스트)."""

    by_year: dict[int, CandleColumns] = field(default_factory=dict)
    delete_calls: list[int] = field(default_factory=list)

    async def read_year(self, key: SeriesKey, year: int) -> CandleColumns:
        return self.by_year.get(year, _empty_columns())

    async def delete_year(self, key: SeriesKey, year: int) -> int:
        removed = self.by_year.pop(year, _empty_columns())
        self.delete_calls.append(year)
        return len(removed)


class _TamperingWarm:
    """`WarmParquetStorage`를 감싸 쓰기는 그대로 위임하되, 읽기 결과를
    일부러 훼손해 검증 실패를 주입한다(DoD(b))."""

    def __init__(self, inner: WarmParquetStorage) -> None:
        self._inner = inner

    def write_year(self, key: SeriesKey, year: int, columns: CandleColumns) -> Path:
        return self._inner.write_year(key, year, columns)

    def read_columns(self, key: SeriesKey, start: datetime, end: datetime):
        columns, missing = self._inner.read_columns(key, start, end)
        tampered = CandleColumns(
            ts=columns.ts,
            open=[v + Decimal("1") for v in columns.open],
            high=columns.high,
            low=columns.low,
            close=columns.close,
            volume=columns.volume,
            quote_volume=columns.quote_volume,
        )
        return tampered, missing


class _CrashOnceWarm:
    """`WarmParquetStorage`를 감싸 첫 `write_year` 호출만 파일을 반쯤 쓴
    채로 예외를 던져 승격 도중 중단을 흉내낸다(DoD(c)). 이후 호출은
    정상 위임한다."""

    def __init__(self, inner: WarmParquetStorage) -> None:
        self._inner = inner
        self._crashed_once = False

    def write_year(self, key: SeriesKey, year: int, columns: CandleColumns) -> Path:
        path = self._inner.write_year(key, year, columns)
        if not self._crashed_once:
            self._crashed_once = True
            raw = path.read_bytes()
            path.write_bytes(raw[: len(raw) // 2])
            raise RuntimeError("simulated crash mid-promotion")
        return path

    def read_columns(self, key: SeriesKey, start: datetime, end: datetime):
        return self._inner.read_columns(key, start, end)


def test_successful_promotion_moves_rows_and_records_lineage(tmp_path: Path):
    warm = WarmParquetStorage(tmp_path)
    hot = _FakeHot(by_year={2026: _daily_columns(2026, [0, 1, 2])})

    outcome = asyncio.run(promote_year(hot, warm, tmp_path, _KEY, 2026))

    assert outcome.promoted is True
    assert outcome.row_count == 3
    assert hot.by_year == {}
    assert hot.delete_calls == [2026]

    loaded, missing = warm.read_columns(
        _KEY, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc)
    )
    assert missing == ()
    assert len(loaded) == 3

    lineage = read_lineage(tmp_path, _KEY)
    assert len(lineage) == 1
    assert lineage[0]["year"] == 2026
    assert lineage[0]["row_count"] == 3
    assert lineage[0]["digest"] == outcome.digest
    assert lineage[0]["source_kind"] == SourceKind.VENDOR.value


def test_idempotent_rerun_does_not_duplicate_file_or_lineage(tmp_path: Path):
    """DoD(a): 같은 승격 잡을 두 번 실행해도 결과 parquet digest가 동일
    하고 계보 행이 1건만 늘어난다(2건이면 실패)."""
    warm = WarmParquetStorage(tmp_path)
    hot = _FakeHot(by_year={2026: _daily_columns(2026, [0, 1])})

    first = asyncio.run(promote_year(hot, warm, tmp_path, _KEY, 2026))
    assert first.promoted is True

    loaded_once, _ = warm.read_columns(
        _KEY, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc)
    )
    series_dir = tmp_path / _KEY.venue.value / _KEY.timeframe.value / str(_KEY.instrument_id)
    year_path = series_dir / "2026.parquet"
    digest_before = year_path.read_bytes()

    second = asyncio.run(promote_year(hot, warm, tmp_path, _KEY, 2026))

    assert second.promoted is False
    assert second.row_count == 0
    assert hot.delete_calls == [2026]  # 두 번째 실행은 delete를 호출하지 않았다

    digest_after = year_path.read_bytes()
    assert digest_after == digest_before

    loaded_twice, _ = warm.read_columns(
        _KEY, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2027, 1, 1, tzinfo=timezone.utc)
    )
    assert loaded_twice.ts == loaded_once.ts

    lineage = read_lineage(tmp_path, _KEY)
    assert len(lineage) == 1


def test_verification_failure_keeps_hot_rows_intact(tmp_path: Path):
    """DoD(b): warm 왕복 검증 실패를 주입하면 hot 삭제가 일어나지 않고
    원본 행 수가 그대로 유지된다."""
    inner_warm = WarmParquetStorage(tmp_path)
    tampering_warm = _TamperingWarm(inner_warm)
    original = _daily_columns(2026, [0, 1, 2])
    hot = _FakeHot(by_year={2026: original})

    with pytest.raises(VerificationFailedError):
        asyncio.run(promote_year(hot, tampering_warm, tmp_path, _KEY, 2026))

    assert hot.delete_calls == []
    assert 2026 in hot.by_year
    assert len(hot.by_year[2026]) == 3

    assert read_lineage(tmp_path, _KEY) == []


def test_crash_during_write_leaves_no_partial_completion_and_retry_rebuilds(tmp_path: Path):
    """DoD(c): 승격 도중 예외를 주입해 죽이면 부분 파일이 '승격 완료'로
    표시되지 않고, 다음 실행이 hot의 전체 데이터로 처음부터 다시 만든다."""
    inner_warm = WarmParquetStorage(tmp_path)
    crash_warm = _CrashOnceWarm(inner_warm)
    original = _daily_columns(2026, [0, 1, 2, 3])
    hot = _FakeHot(by_year={2026: original})

    with pytest.raises(RuntimeError, match="simulated crash"):
        asyncio.run(promote_year(hot, crash_warm, tmp_path, _KEY, 2026))

    assert hot.delete_calls == []
    assert len(hot.by_year[2026]) == 4
    assert read_lineage(tmp_path, _KEY) == []

    outcome = asyncio.run(promote_year(hot, crash_warm, tmp_path, _KEY, 2026))

    assert outcome.promoted is True
    assert outcome.row_count == 4
    assert hot.by_year == {}
    lineage = read_lineage(tmp_path, _KEY)
    assert len(lineage) == 1

    loaded, missing = inner_warm.read_columns(
        _KEY, datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 2, 1, tzinfo=timezone.utc)
    )
    assert missing == ()
    assert len(loaded) == 4
    assert loaded.ts == original.ts


def test_promoting_empty_hot_year_is_a_noop(tmp_path: Path):
    warm = WarmParquetStorage(tmp_path)
    hot = _FakeHot(by_year={})

    outcome = asyncio.run(promote_year(hot, warm, tmp_path, _KEY, 2026))

    assert outcome.promoted is False
    assert outcome.row_count == 0
    assert hot.delete_calls == []
    assert read_lineage(tmp_path, _KEY) == []
