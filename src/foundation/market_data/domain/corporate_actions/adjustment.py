"""LA-8 — corporate action 조정계수 체인, RAW→ADJUSTED 캔들 변환.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.2 LA-8, §9.2 LA-8.

`ex_date` 이전 캔들에는 그 날짜(및 그 이후) 발생한 모든 조정을 누적 곱한
계수를 적용한다 — 같은 종목에 분할이 연속 2회 이상 있어도 각 캔들은 자기
날짜보다 뒤에 일어난 조정만 반영해야 하므로, `ex_date` 내림차순으로 계수를
누적한다. Decimal 곱셈만 사용한다(부동소수 금지, 사양 §9.2 LA-8).

`CorporateAction.ratio`는 SPLIT/REVERSE_SPLIT/MERGER에 대해 "2:1 분할이면
2"(계약 docstring) 규칙을 따른다. CASH_DIVIDEND는 계약상 `ratio=1`이 강제되지
않으므로, 배당의 가격 조정(종가 대비 배당락)은 이 리프에서 다루지 않는다 —
**미검증**: 배당 조정에는 ex_date 전일 종가가 필요하며 `factor_chain`은 캔들을
받지 않으므로 여기서는 배당을 가격·거래량 계수에 반영하지 않는다(계수=1).
MERGER의 정확한 전환 비율 관례도 **미검증**이며 SPLIT과 동일한 공식을 쓴다.
I/O 없음 — 순수 함수만 담는다.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from src.foundation.market_data.contracts.v1 import CandleRecord, CorporateAction

__all__ = ["AdjustmentFactor", "InvalidRatioError", "adjust", "factor_chain"]

_ONE = Decimal(1)


class InvalidRatioError(ValueError):
    """`ratio <= 0`인 corporate action이 들어왔다 — 계보를 신뢰할 수 없다."""

    def __init__(self, action: CorporateAction) -> None:
        super().__init__(
            f"instrument_id={action.instrument_id} ex_date={action.ex_date}: "
            f"ratio={action.ratio}는 양수여야 합니다."
        )
        self.action = action


@dataclass(frozen=True, slots=True)
class AdjustmentFactor:
    """`effective_date` **미만**(strictly before)의 캔들에 곱해야 하는 누적 계수.

    `effective_date` 및 그 이후에 일어난 모든 조정이 이미 누적되어 있다."""

    instrument_id: UUID
    effective_date: date
    price_factor: Decimal
    volume_factor: Decimal


def _action_factors(action: CorporateAction) -> tuple[Decimal, Decimal]:
    """(가격 계수, 거래량 계수) — 과거 캔들에 곱할 값. 분할 2:1(ratio=2)이면
    과거 가격은 1/2로, 과거 거래량은 2배로 환산한다."""
    if action.ratio <= 0:
        raise InvalidRatioError(action)
    if action.action_type == "REVERSE_SPLIT":
        return action.ratio, _ONE / action.ratio
    # SPLIT, MERGER, CASH_DIVIDEND(ratio=1 관례 — 위 모듈 docstring 참고)
    return _ONE / action.ratio, action.ratio


def factor_chain(actions: list[CorporateAction], as_of: datetime) -> list[AdjustmentFactor]:
    """`as_of` 시점까지 발효된 조정만 모아 종목별 누적 계수 목록을 만든다.

    반환 목록은 `ex_date` 오름차순이며, 각 원소는 해당 `ex_date` 미만 캔들에
    적용할 누적 계수다(자신 및 이후 조정 전부 포함)."""
    as_of_date = as_of.date()
    by_instrument: dict[UUID, list[CorporateAction]] = defaultdict(list)
    for action in actions:
        if action.ratio <= 0:
            raise InvalidRatioError(action)
        if action.ex_date > as_of_date:
            continue
        by_instrument[action.instrument_id].append(action)

    factors: list[AdjustmentFactor] = []
    for instrument_id, instrument_actions in by_instrument.items():
        ordered = sorted(instrument_actions, key=lambda a: a.ex_date, reverse=True)
        cumulative_price = _ONE
        cumulative_volume = _ONE
        instrument_factors: list[AdjustmentFactor] = []
        for action in ordered:
            price_mult, volume_mult = _action_factors(action)
            cumulative_price *= price_mult
            cumulative_volume *= volume_mult
            instrument_factors.append(
                AdjustmentFactor(
                    instrument_id=instrument_id,
                    effective_date=action.ex_date,
                    price_factor=cumulative_price,
                    volume_factor=cumulative_volume,
                )
            )
        factors.extend(reversed(instrument_factors))
    return sorted(factors, key=lambda f: (f.instrument_id, f.effective_date))


def _factor_for(
    instrument_id: UUID,
    open_time: datetime,
    factors_by_instrument: dict[UUID, list[AdjustmentFactor]],
) -> tuple[Decimal, Decimal]:
    candle_date = open_time.date()
    for factor in factors_by_instrument.get(instrument_id, ()):
        if factor.effective_date > candle_date:
            return factor.price_factor, factor.volume_factor
    return _ONE, _ONE


def adjust(candles: list[CandleRecord], factors: list[AdjustmentFactor]) -> list[CandleRecord]:
    """RAW 캔들에 `factor_chain` 결과를 적용해 ADJUSTED 캔들을 만든다.

    캔들 날짜보다 늦게(effective_date 이후) 일어난 조정만 반영하도록, 종목별
    `effective_date` 오름차순에서 처음으로 캔들 날짜를 초과하는 계수를 쓴다."""
    by_instrument: dict[UUID, list[AdjustmentFactor]] = defaultdict(list)
    for f in factors:
        by_instrument[f.instrument_id].append(f)
    for instrument_factors in by_instrument.values():
        instrument_factors.sort(key=lambda f: f.effective_date)

    adjusted: list[CandleRecord] = []
    for candle in candles:
        price_factor, volume_factor = _factor_for(
            candle.key.instrument_id, candle.open_time, by_instrument
        )
        if price_factor == _ONE and volume_factor == _ONE:
            adjusted.append(candle)
            continue
        adjusted.append(
            candle.model_copy(
                update={
                    "open": candle.open * price_factor,
                    "high": candle.high * price_factor,
                    "low": candle.low * price_factor,
                    "close": candle.close * price_factor,
                    "volume": candle.volume * volume_factor,
                }
            )
        )
    return adjusted
