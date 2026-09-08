"""BT-15a (1/2) — `CandleColumns` 컬럼 경로 위 numpy 배열 뷰.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-15(1/2). 선행: BT-14 라이선스 평가(1b53ad92), BT-2~6 체결 모델(fa3afe4),
LA-23b `CandleColumns` 컬럼 경로(be1c88b).

BT-16(그리드·워크포워드·몬테카를로, 1,000조합 ≤60s)·BT-17(다종목 스윕)이
대량 조합을 numpy/numba로 빠르게 돌리려면 캔들을 배열로 들고 있어야 한다.
이 모듈은 그 배열을 새로 설계하지 않는다(§C 중복 컨텍스트 회피) — LA-23b
`CandleColumns`(ts/open/high/low/close/volume/quote_volume, 이 순서)를 그대로
numpy dtype으로 옮긴다. 필드 이름·순서·개수는 `CandleColumns` 정의와 항상
같아야 하므로 `from_candle_columns`가 그 사실 자체를 실행 시점에 단언한다
(리플렉션 대조 — 한쪽만 필드를 추가/삭제/재배열하면 즉시 실패한다).

`Decimal` → `float64` 변환은 정밀도를 버리는 의도적 선택이다: 이 엔진은
BT-16 대량 스윕을 위한 속도 우선 경로이고, 신뢰 가능한 최종 체결 로그는
여전히 이벤트 엔진(`quick_backtest.run_quick_backtest`, `Decimal`)이 낸다.
단일 조합에서 두 엔진이 원소 단위로 얼마나 일치하는지는 BT-15b(fills.py +
동등성 테스트)의 몫이다 — 이 리프는 그 전 단계인 컬럼 로드만 다룬다.

`ts`는 tz-aware `datetime`을 UTC 자정 기준 정수 나노초로 바꾼다(부동소수
경유 없이 `timedelta`의 정수 필드만 사용 — 큰 epoch 값에서 float64 가수부
(52비트, 약 4.5e15) 정밀도 손실을 피한다).

순수 모듈 — I/O 없음.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, fields
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

import numpy as np

from src.foundation.market_data.domain.candle_columns import (
    CandleColumns,
    MismatchedColumnLengthError,
)

__all__ = ["ArrayDtypeError", "CandleArrays", "from_candle_columns"]

FloatArray = np.ndarray[Any, np.dtype[np.float64]]
TimestampArray = np.ndarray[Any, np.dtype[np.int64]]

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_PRICE_FIELDS = ("open", "high", "low", "close", "volume", "quote_volume")


class ArrayDtypeError(TypeError):
    """`BT_VECTOR_ARRAY_DTYPE` — `CandleArrays` 필드 dtype이나 `CandleColumns`와의
    필드 대응이 계약과 다르면 fail-closed로 거부한다(조용한 형변환 금지)."""


@dataclass(frozen=True, slots=True)
class CandleArrays:
    """`CandleColumns`와 같은 필드 이름·순서(ts/open/high/low/close/volume/
    quote_volume)를 갖는 numpy 배열 뷰. 인덱스 `i`가 캔들 하나에 대응하는 것도
    동일하다 — 저장 형식만 파이썬 `list[Decimal]` 대신 numpy 배열이다."""

    ts: TimestampArray  # int64, UTC epoch 나노초
    open: FloatArray
    high: FloatArray
    low: FloatArray
    close: FloatArray
    volume: FloatArray
    quote_volume: FloatArray  # None -> NaN

    def __post_init__(self) -> None:
        n = len(self.ts)
        lengths = {name: len(getattr(self, name)) for name in _PRICE_FIELDS}
        if any(length != n for length in lengths.values()):
            raise MismatchedColumnLengthError({"ts": n, **lengths})
        if self.ts.dtype != np.int64:
            raise ArrayDtypeError(f"CandleArrays.ts dtype이 int64가 아니다: {self.ts.dtype}")
        for name in _PRICE_FIELDS:
            dtype = getattr(self, name).dtype
            if dtype != np.float64:
                raise ArrayDtypeError(f"CandleArrays.{name} dtype이 float64가 아니다: {dtype}")

    def __len__(self) -> int:
        return len(self.ts)


def from_candle_columns(columns: CandleColumns) -> CandleArrays:
    """`columns`를 `CandleArrays`로 옮긴다. `CandleColumns`가 필드를 추가·
    삭제·재배열해 이 모듈이 뒤따라 갱신되지 않으면(§C 중복 컨텍스트가 벌어질
    조짐) 여기서 즉시 실패한다 — 두 dataclass의 필드 이름 목록을 순서까지
    대조한다."""
    columns_fields = [f.name for f in fields(CandleColumns)]
    arrays_fields = [f.name for f in fields(CandleArrays)]
    if columns_fields != arrays_fields:
        raise ArrayDtypeError(
            "CandleArrays 필드가 CandleColumns와 더 이상 같은 순서·이름이 아니다 "
            f"(CandleColumns={columns_fields}, CandleArrays={arrays_fields}) — "
            "새 캔들 표현이 생겼거나 한쪽만 갱신됐다"
        )
    return CandleArrays(
        ts=_to_epoch_ns(columns.ts),
        open=_to_float64(columns.open),
        high=_to_float64(columns.high),
        low=_to_float64(columns.low),
        close=_to_float64(columns.close),
        volume=_to_float64(columns.volume),
        quote_volume=_to_float64(columns.quote_volume),
    )


def _to_epoch_ns(values: Sequence[datetime]) -> TimestampArray:
    out = np.empty(len(values), dtype=np.int64)
    for i, ts in enumerate(values):
        delta = ts.astimezone(timezone.utc) - _EPOCH
        micros = delta.days * 86_400_000_000 + delta.seconds * 1_000_000 + delta.microseconds
        out[i] = micros * 1_000
    return out


def _to_float64(values: Sequence[Decimal | None]) -> FloatArray:
    return np.array([np.nan if v is None else float(v) for v in values], dtype=np.float64)
