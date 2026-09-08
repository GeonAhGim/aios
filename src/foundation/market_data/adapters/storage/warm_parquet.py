"""DC-14 — warm 계층(Parquet, 종목×연도) 캔들 저장 — `CandleColumns` 직접 왕복.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.1 DC-14(선행 DC-13), §9.2 DC-14.

DC-13 `hot_postgres.HotPostgresStorage`가 위임하는 `ports/candle_store.
CandleStore` 포트(`read_candles_columnar`)를 그대로 구현한다(decision: 새
포트·새 DTO 신설 금지 — §C 중복 컨텍스트). warm 계층은 hot(`md_candle`)에서
승격된 오래된 파티션의 아카이브라 append-only 배치 쓰기(DC-15 `tiering.py`
소관)만 있고, hot의 `created_at` 스냅샷 격리(`as_of`)에 대응하는 메타데이터가
없다 — `as_of`를 조용히 무시하면 hot과 다른(더 넓은) 결과를 반환할 위험이
있어 fail-closed로 거부한다(`AsOfNotSupportedError`).

파일 레이아웃: `<root>/<venue>/<timeframe>/<instrument_id>/<year>.parquet`
(스펙 §9.2 DC-14 "종목×연도"). 컬럼은 전부 문자열로 저장한다
(`Decimal`→`str`, `AwareDatetime`→`isoformat()`) — pyarrow의
`decimal128`/`timestamp` 타입이 강제하는 precision/scale·tz 정규화를 피해
DoD(a) 왕복 바이트 동일성을 보장하기 위한 선택이다(파일 크기는 이진
인코딩보다 커진다 — 정직하게 남겨두는 트레이드오프).

pyarrow만 사용한다(Apache-2.0) — ArcticDB(BSL 1.1)·Timescale 컬럼스토어
(Timescale License) 반입 금지(decision, note (e)).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import SeriesKey
from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)

__all__ = [
    "AsOfNotSupportedError",
    "YearMismatchError",
    "WarmParquetStorage",
]

_ARROW_SCHEMA = pa.schema(
    [
        ("ts", pa.string()),
        ("open", pa.string()),
        ("high", pa.string()),
        ("low", pa.string()),
        ("close", pa.string()),
        ("volume", pa.string()),
        ("quote_volume", pa.string()),
    ]
)

_Row = tuple[datetime, Decimal, Decimal, Decimal, Decimal, Decimal, Decimal | None]


class AsOfNotSupportedError(NotImplementedError):
    """warm parquet 파일은 스냅샷 격리 메타데이터(`created_at`)를 갖지 않는다
    — `as_of != None`은 지원 불가로 fail-closed 거부한다(§ 포트 계약을
    조용히 어기지 않기 위함)."""


class YearMismatchError(ValueError):
    """`write_year(year=...)`에 다른 연도의 `open_time`이 섞여 들어오면
    파일명이 실제 내용과 거짓말을 하게 된다 — 조용히 잘라내지 않고 거부한다."""


def _series_dir(root: Path, key: SeriesKey) -> Path:
    return root / key.venue.value / key.timeframe.value / str(key.instrument_id)


def _year_path(root: Path, key: SeriesKey, year: int) -> Path:
    return _series_dir(root, key) / f"{year}.parquet"


def _add_months(dt: datetime, n: int) -> datetime:
    total = dt.month - 1 + n
    year = dt.year + total // 12
    month = total % 12 + 1
    return dt.replace(year=year, month=month, day=1, hour=0, minute=0, second=0, microsecond=0)


def _months_between(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """`[start, end)`와 겹치는 달력 월 구간을 월 경계로 클립해 반환한다."""
    cursor = start.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    windows: list[tuple[datetime, datetime]] = []
    while cursor < end:
        nxt = _add_months(cursor, 1)
        windows.append((max(cursor, start), min(nxt, end)))
        cursor = nxt
    return windows


def _coalesce_missing(
    ranges: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """인접·중첩한 결측 구간을 하나로 합친다(월 단위로 쌓인 원시 구간을
    사람이 읽기 좋은 최소 구간 목록으로 압축)."""
    if not ranges:
        return []
    ordered = sorted(ranges)
    merged = [ordered[0]]
    for seg_start, seg_end in ordered[1:]:
        last_start, last_end = merged[-1]
        if seg_start <= last_end:
            merged[-1] = (last_start, max(last_end, seg_end))
        else:
            merged.append((seg_start, seg_end))
    return merged


def _read_year_file(path: Path) -> list[_Row]:
    """손상된(잘린) parquet은 pyarrow가 예외를 던지도록 그대로 전파한다
    (DoD(d) — 빈 결과로 조용히 뭉개는 것은 실패)."""
    table = pq.read_table(path, schema=_ARROW_SCHEMA)
    rows: list[_Row] = []
    for record in table.to_pylist():
        quote_volume = record["quote_volume"]
        rows.append(
            (
                datetime.fromisoformat(record["ts"]),
                Decimal(record["open"]),
                Decimal(record["high"]),
                Decimal(record["low"]),
                Decimal(record["close"]),
                Decimal(record["volume"]),
                None if quote_volume is None else Decimal(quote_volume),
            )
        )
    return rows


class WarmParquetStorage:
    """`ports/candle_store.CandleStore`가 요구하는 컬럼지향 읽기 표면을
    구현하는 파일 기반(Parquet) 어댑터. 쓰기는 이 리프의 왕복 DoD 검증용
    표면(`write_year`)만 제공한다 — 실제 hot→warm 승격 오케스트레이션은
    DC-15 `tiering.py` 소관(이 클래스를 호출부로 사용)."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def write_year(self, key: SeriesKey, year: int, columns: CandleColumns) -> Path:
        n = len(columns)
        lengths = {
            "open": len(columns.open),
            "high": len(columns.high),
            "low": len(columns.low),
            "close": len(columns.close),
            "volume": len(columns.volume),
            "quote_volume": len(columns.quote_volume),
        }
        if any(length != n for length in lengths.values()):
            raise MismatchedColumnLengthError({"ts": n, **lengths})
        if any(ts.year != year for ts in columns.ts):
            raise YearMismatchError(
                f"write_year(year={year})에 다른 연도의 open_time이 섞여 있다(fail-closed)."
            )

        table = pa.table(
            {
                "ts": pa.array([ts.isoformat() for ts in columns.ts], type=pa.string()),
                "open": pa.array([str(v) for v in columns.open], type=pa.string()),
                "high": pa.array([str(v) for v in columns.high], type=pa.string()),
                "low": pa.array([str(v) for v in columns.low], type=pa.string()),
                "close": pa.array([str(v) for v in columns.close], type=pa.string()),
                "volume": pa.array([str(v) for v in columns.volume], type=pa.string()),
                "quote_volume": pa.array(
                    [None if v is None else str(v) for v in columns.quote_volume],
                    type=pa.string(),
                ),
            },
            schema=_ARROW_SCHEMA,
        )
        path = _year_path(self._root, key, year)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)
        return path

    def read_columns(
        self, key: SeriesKey, start: AwareDatetime, end: AwareDatetime
    ) -> tuple[CandleColumns, tuple[tuple[datetime, datetime], ...]]:
        """`[start, end)`를 월 단위로 훑어, 연도 파일이 커버하는 부분만
        슬라이싱해 돌려주고 나머지는 두 번째 반환값(결측 구간, 오름차순
        병합됨)으로 명시한다(DoD(b) — 빈 결과로 뭉개지 않는다)."""
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("start/end는 tz-aware datetime이어야 한다(fail-closed).")
        if end <= start:
            raise ValueError(f"start >= end: {start!r} >= {end!r}(fail-closed 구간 역전).")

        year_cache: dict[int, list[_Row]] = {}

        def rows_for_year(year: int) -> list[_Row]:
            if year not in year_cache:
                path = _year_path(self._root, key, year)
                year_cache[year] = _read_year_file(path) if path.exists() else []
            return year_cache[year]

        matched: list[_Row] = []
        missing: list[tuple[datetime, datetime]] = []
        for month_start, month_end in _months_between(start, end):
            in_month = [
                row for row in rows_for_year(month_start.year) if month_start <= row[0] < month_end
            ]
            if not in_month:
                missing.append((month_start, month_end))
                continue
            matched.extend(in_month)

        matched.sort(key=lambda row: row[0])
        columns = CandleColumns(
            ts=[row[0] for row in matched],
            open=[row[1] for row in matched],
            high=[row[2] for row in matched],
            low=[row[3] for row in matched],
            close=[row[4] for row in matched],
            volume=[row[5] for row in matched],
            quote_volume=[row[6] for row in matched],
        )
        return columns, tuple(_coalesce_missing(missing))

    async def read_candles_columnar(
        self,
        _conn: object,
        key: SeriesKey,
        start: AwareDatetime,
        end: AwareDatetime,
        as_of: AwareDatetime | None,
    ) -> CandleColumns:
        """`CandleStore.read_candles_columnar`와 같은 이름·형태(DC-13이 쓰는
        포트를 재사용) — `_conn`은 hot 계층과 달리 트랜잭션이 필요 없어
        받아서 무시한다. 결측 구간 정보가 필요한 호출자는 `read_columns`를
        직접 써야 한다(포트 반환형은 `CandleColumns` 고정이라 여기서 더
        얹을 수 없다)."""
        if as_of is not None:
            raise AsOfNotSupportedError(
                "warm parquet은 스냅샷 메타데이터가 없어 as_of를 지원하지 않는다(fail-closed)."
            )
        columns, _missing = self.read_columns(key, start, end)
        return columns
