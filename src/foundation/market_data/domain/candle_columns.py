"""LA-23b — 컬럼지향 읽기 전용 뷰(ADR-2026-09-04-A #1).

Spec: docs/design/ADR-2026-09-04-A-market-data-replay-perf.md #1,
docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.4.

리플레이·백테스트 같은 대량 소비자가 캔들 하나하나를 pydantic
`CandleRecord`로 검증·인스턴스화하지 않고도 순회할 수 있게 ts/o/h/l/c/v를
배열로 담는다. `close_time`은 쓰기 시점 불변식(`domain/quality/ohlc_sanity.
check_candle`이 `close_time == open_time + duration(timeframe)`을 강제하고,
위반 캔들은 REJECT로 격리되어 `md_candle`에 저장되지 않는다)에 따라 항상
유도 가능하므로 배열로 들고 다니지 않는다.

ADR 원문은 배열을 "ts/o/h/l/c/v"로만 적었지만, `quote_volume`은 여기서
빼면 `to_candle_records`가 `CandleRecord`를 무손실 재구성하지 못해
`domain/lineage.batch_hash`가 원래 값과 달라진다 — contracts/v1 불변(P5)과
batch_hash 바이트 동일성(P3 WORM, 같은 ADR #2) 둘 다 지키려면 필요한
확장이다(정직하게 남겨 두는 편차).

I/O 없음 — 순수 데이터 홀더 + 순수 변환 함수만 담는다.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from pydantic import AwareDatetime

from src.foundation.market_data.contracts.v1 import CandleRecord, SeriesKey
from src.foundation.market_data.domain.timeframe import duration

__all__ = ["CandleColumns", "MismatchedColumnLengthError", "to_candle_records"]


class MismatchedColumnLengthError(ValueError):
    """`MD_CANDLE_COLUMNS_LENGTH_MISMATCH` — 배열 길이가 서로 다르면
    인덱스 접근이 조용히 어긋난 행을 짝지을 수 있다(fail-closed로 거부)."""

    def __init__(self, lengths: dict[str, int]) -> None:
        super().__init__(f"CandleColumns 배열 길이가 서로 다릅니다: {lengths}")


@dataclass(frozen=True, slots=True)
class CandleColumns:
    """읽기 전용 컬럼 배열. 인덱스 `i`가 캔들 하나에 대응한다(`ts[i]`가
    `open_time`). 정렬 순서(`open_time ASC`)는 어댑터의 `ORDER BY`가
    보장한다 — 이 타입 자체는 정렬을 검증하지 않는다."""

    ts: list[AwareDatetime]
    open: list[Decimal]
    high: list[Decimal]
    low: list[Decimal]
    close: list[Decimal]
    volume: list[Decimal]
    quote_volume: list[Decimal | None]

    def __len__(self) -> int:
        return len(self.ts)


def to_candle_records(columns: CandleColumns, key: SeriesKey) -> list[CandleRecord]:
    """`columns`가 `key`(venue/instrument_id/timeframe)로 필터링해 조회한
    결과라고 전제한다(호출자 책임 — 포트 계약, `ports/candle_store.
    CandleStore.read_candles_columnar` 참고). 그래서 행마다 `SeriesKey`를 새로
    만들지 않고 `key` 인스턴스를 그대로 공유한다 — WHERE 절이 이미 그 값과
    같은 행만 돌려주므로 값은 항상 같다.

    `CandleRecord.model_construct`로 필드 검증을 건너뛴다 — DB에서 읽은
    Decimal·tz-aware datetime은 asyncpg가 이미 올바른 타입으로 반환하므로
    (NUMERIC→Decimal, TIMESTAMPTZ→aware datetime) 재검증은 순수 비용이다.
    쓰기 시점에 `ohlc_sanity.check_candle`을 통과한 데이터에만 안전하다."""
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

    step = duration(key.timeframe)
    return [
        CandleRecord.model_construct(
            key=key,
            open_time=columns.ts[i],
            close_time=columns.ts[i] + step,
            open=columns.open[i],
            high=columns.high[i],
            low=columns.low[i],
            close=columns.close[i],
            volume=columns.volume[i],
            quote_volume=columns.quote_volume[i],
        )
        for i in range(n)
    ]
