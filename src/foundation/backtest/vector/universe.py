"""BT-17 — `backtest/vector/universe.py`: 다종목 유니버스 스윕.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9
BT-17. 선행: BT-15(`vector/{arrays,signals,fills}.py`, 1c5e4b5f 계열)·DC-13
(hot/warm 계층, 1212 계열) — 둘 다 origin/main 병합 완료(task-2372 decision).

이 모듈은 BT-15b `fills.run_vector_backtest`(벡터 신호 + BT-2~6 이벤트 체결
엔진)를 종목별로 순회 호출만 한다 — 체결 산식을 재구현하지 않는다(I-05).
캔들 표현도 LA-23b/BT-15a `CandleColumns`를 그대로 재사용한다 — 종목별
dict-of-DataFrame 같은 새 시계열 표현을 신설하지 않는다(§C 중복 컨텍스트
회피). 유니버스 자체는 `symbol -> (CandleColumns, VectorSignal)` 매핑으로만
표현한다.

메모리 상한(`MAX_UNIVERSE_CANDLES`): 유니버스 전 종목의 캔들 수 합이 상한을
넘으면 종목을 하나도 처리하지 않고 즉시 거부한다(fail-closed) — 상한 직전까지
처리하다 중간에 멈추는 "부분 결과"를 절대 반환하지 않는다. 합계 검사가
순회보다 먼저 끝나므로 이 보장은 코드 구조 자체가 준다(사후 검사가 아니다).
100종목×1년 D1(≈36,500봉)이 여유 있게 들어가면서 경계 테스트가 실제로
그만큼의 `CandleColumns`를 만들어도 무리 없는 크기로 40,000을 골랐다 — 이
숫자 자체는 실측 메모리 프로파일링이 아니라 "합이 상한을 넘으면 계산을
시작하지 않는다"는 구조를 증명하기 위한 값이다(미검증: 실제 운영 환경의
가용 메모리와의 관계는 별도로 확인해야 한다).

결측 종목: 어떤 종목의 `CandleColumns`가 비어 있으면(구간에 캔들이 없음)
그 종목은 조용히 건너뛰지 않고 `UniverseSweepResult.skipped`에 종목명이
그대로 남는다 — "0건 스윕 성공"과 "그 종목만 데이터가 없었다"를 호출자가
구분할 수 있어야 한다.

순수 모듈 — I/O 없음.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal

from src.foundation.backtest.application.quick_backtest import QuickBacktestResult
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.backtest.vector.fills import VectorSignal, run_vector_backtest
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = [
    "MAX_UNIVERSE_CANDLES",
    "UniverseMemoryLimitError",
    "UniverseSweepResult",
    "sweep_universe",
]

# 유니버스 전 종목 캔들 수 합계 상한 — 모듈 상수로 노출해 호출자가 사전에
# 유니버스 크기를 검사할 수 있게 한다(모듈 docstring 근거).
MAX_UNIVERSE_CANDLES = 40_000


class UniverseMemoryLimitError(ValueError):
    """`BT_VECTOR_UNIVERSE_MEMORY_LIMIT` — 유니버스 총 캔들 수가
    `MAX_UNIVERSE_CANDLES`를 넘으면 부분 결과 없이 fail-closed로 거부한다."""

    def __init__(self, total_candles: int) -> None:
        super().__init__(
            f"유니버스 총 캔들 수 {total_candles}가 상한 {MAX_UNIVERSE_CANDLES}를 넘는다 — "
            "부분 결과를 만들지 않고 거부한다"
        )


@dataclass(frozen=True, slots=True)
class UniverseSweepResult:
    """`results`는 실제로 스윕한 종목만 담는다(`symbol -> QuickBacktestResult`).
    `skipped`는 구간에 캔들이 없어 건너뛴 종목명이다 — 두 컬렉션은 항상
    유니버스 키 전체를 정확히 분할한다(교집합 없음, 합집합이 입력 키 전체)."""

    results: dict[str, QuickBacktestResult]
    skipped: tuple[str, ...]


def sweep_universe(
    universe: Mapping[str, tuple[CandleColumns, VectorSignal]],
    config: BacktestConfigV2,
    *,
    timeframe: Timeframe,
    initial_cash: Decimal,
    funding_rate: Decimal | None = None,
) -> UniverseSweepResult:
    """`universe`의 각 종목에 같은 `config`로 `run_vector_backtest`를 돌린다.

    상한 검사가 순회 전체보다 먼저 끝나므로, 상한을 넘는 유니버스는 어떤
    종목도 계산하지 않은 채 거부된다(부분 결과 없음)."""
    total_candles = sum(len(columns) for columns, _ in universe.values())
    if total_candles > MAX_UNIVERSE_CANDLES:
        raise UniverseMemoryLimitError(total_candles)

    results: dict[str, QuickBacktestResult] = {}
    skipped: list[str] = []
    for symbol, (columns, signal) in universe.items():
        if len(columns) == 0:
            skipped.append(symbol)
            continue
        results[symbol] = run_vector_backtest(
            config, columns, signal, timeframe=timeframe,
            initial_cash=initial_cash, funding_rate=funding_rate,
        )
    return UniverseSweepResult(results=results, skipped=tuple(skipped))
