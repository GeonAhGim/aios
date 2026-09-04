"""BT-7 — bar magnifier(상위 TF 봉을 하위 TF로 확대해 체결 순서 결정).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-7, §3.4(`magnifier_tf`), §9.5 BT-7 DoD("하위 TF 체결 순서 결정론").

스탑/리밋처럼 여러 주문이 같은 상위 TF 봉 안에서 동시에 트리거 조건을
만족할 수 있을 때, "그 봉 안에서 가격이 어느 순서로 지나갔는가"가 있어야
어느 주문이 먼저 체결됐는지 정할 수 있다. 이 모듈은 그 가격 방문 순서
(open→...→close, `Decimal` 튜플)를 만든다.

두 경로:
1. 실제 하위 TF 봉(`lower_bars`, `CandleColumns`)이 상위 봉의 시간창 안에
   존재하면 그것을 그대로 쓴다 — 하위 봉을 시간순으로 훑고, 봉마다 아래
   확대 규칙으로 4점을 뽑아 이어붙인다.
2. `magnifier_tf`가 `None`이거나 `lower_bars`가 없거나 비어 있으면(하위
   데이터 갭) 상위 봉 자체를 확대 규칙으로 4점만 뽑는다 — "봉 단위
   체결"로 되돌아간다(예외 아님, §DoD (4)).

확대 규칙(모든 봉·모든 경로에 동일 적용, 결정론):
- `close >= open`(양봉·도지): `open → low → high → close`
  (불리한 쪽(저가)을 먼저 찍고 유리한 쪽(고가)을 나중에 찍는 보수적 가정)
- `close < open`(음봉): `open → high → low → close`
이 규칙은 이 모듈이 정의한 내부 관례다("미검증" — 특정 거래소의 실제 틱
순서를 반영한 것이 아니라, 틱 데이터 없이 체결 현실성을 보수적으로
근사하기 위한 자체 가정이다).

미래 참조 금지: `lower_bars`는 반드시 상위 봉의 시간창
`[open_time, open_time + duration(higher_tf))` 안에 있는 행만 담아야
한다 — 그 창을 벗어난 행이 하나라도 있으면(다음 상위 봉에 속하는 "미래"
하위 봉을 포함해) `LookAheadError`. 호출자가 "그 상위 봉 시점에 이미
아는 하위 봉"만 슬라이스해서 넘길 책임을 지고, 이 모듈은 그 경계를
검증만 한다 — 포트/저장소 접근이 전혀 없으므로 이 모듈 스스로 미래를
조회할 방법이 없다.

Timeframe 길이는 LA-2(`domain.timeframe.duration`)를 그대로 쓰고
재구현하지 않는다. 하위 봉 컨테이너는 DC-10 rollup의 산출 타입
(`CandleColumns`)을 그대로 재사용한다 — rollup 결과를 변환 없이 바로
넘길 수 있다.

순수 도메인 — I/O·DB·시계 접근 없음(모든 시각·데이터는 인자로만 받는다).
dict/set 순회에 기대지 않는다 — 입력·출력 모두 순서가 고정된
list/tuple이라 같은 입력이면 항상 바이트 동일한 출력을 낸다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.domain.timeframe import duration

__all__ = [
    "HigherBar",
    "LookAheadError",
    "IncompatibleMagnifierTimeframeError",
    "validate_magnifier_config",
    "magnify",
]


@dataclass(frozen=True, slots=True)
class HigherBar:
    """확대 대상 상위 TF 봉 1개(OHLC + 그 봉의 open_time)."""

    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


class LookAheadError(ValueError):
    """`BT_MAGNIFIER_LOOK_AHEAD` — 상위 봉의 시간창 밖(다음 상위 봉에 속한
    "미래" 하위 봉 포함)에 있는 하위 봉을 확대 입력으로 받았다."""


class IncompatibleMagnifierTimeframeError(ValueError):
    """`BT_MAGNIFIER_TF_INCOMPATIBLE` — `magnifier_tf`가 상위 TF보다 크거나
    같거나, 상위 TF 길이를 정수배로 나누지 못한다."""


def validate_magnifier_config(*, higher_tf: Timeframe, magnifier_tf: Timeframe | None) -> None:
    """`magnifier_tf`가 `higher_tf`를 확대할 자격이 있는지 검사한다.

    `magnifier_tf=None`은 항상 유효하다(확대 없음 — 예외 아님, §DoD (4))."""

    if magnifier_tf is None:
        return
    higher_step = duration(higher_tf)
    lower_step = duration(magnifier_tf)
    if lower_step >= higher_step:
        raise IncompatibleMagnifierTimeframeError(
            f"magnifier_tf({magnifier_tf!r})는 상위 TF({higher_tf!r})보다 "
            "짧아야 한다"
        )
    if higher_step % lower_step != timedelta(0):
        raise IncompatibleMagnifierTimeframeError(
            f"상위 TF({higher_tf!r})는 magnifier_tf({magnifier_tf!r})의 정수배가 아니다"
        )


def _expand_single_bar(
    open_: Decimal, high: Decimal, low: Decimal, close: Decimal
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    if close >= open_:
        return (open_, low, high, close)
    return (open_, high, low, close)


def magnify(
    higher_bar: HigherBar,
    *,
    higher_tf: Timeframe,
    magnifier_tf: Timeframe | None,
    lower_bars: CandleColumns | None = None,
) -> tuple[Decimal, ...]:
    """`higher_bar` 하나의 가격 방문 순서를 결정론적으로 만든다.

    같은 (`higher_bar`, `higher_tf`, `magnifier_tf`, `lower_bars`) 입력이면
    항상 같은 튜플을 낸다 — 전역 시계·난수·dict/set 순회가 경로에 없다.
    """

    validate_magnifier_config(higher_tf=higher_tf, magnifier_tf=magnifier_tf)

    if magnifier_tf is None or lower_bars is None or len(lower_bars) == 0:
        return _expand_single_bar(
            higher_bar.open, higher_bar.high, higher_bar.low, higher_bar.close
        )

    window_start = higher_bar.open_time
    window_end = higher_bar.open_time + duration(higher_tf)

    visited: list[Decimal] = []
    for i in range(len(lower_bars)):
        ts = lower_bars.ts[i]
        if ts < window_start or ts >= window_end:
            raise LookAheadError(
                f"하위 봉 index {i}(open_time={ts!r})가 상위 봉 시간창 "
                f"[{window_start!r}, {window_end!r}) 밖입니다"
            )
        visited.extend(
            _expand_single_bar(
                lower_bars.open[i], lower_bars.high[i], lower_bars.low[i], lower_bars.close[i]
            )
        )
    return tuple(visited)
