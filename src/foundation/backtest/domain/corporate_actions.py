"""BT-20 — 백테스트 기업행위(분할·현금배당) 조정(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§3.4(`BacktestConfigV2.adjustments{splits, dividends}`),
docs/design/ADR-2026-09-06-G-second-audit-corrections.md 135행(BT-20
"백테스트에 기업행위 미반영 — TradingView·Bloomberg 기본 제공").

`BacktestConfigV2.adjustments`는 계약에 선언돼 있었지만 이를 실제로 적용하는
리프가 없었다. 분할은 가격·수량을, 배당은 가격만(총수익 기준) 조정한다 —
과거 가격을 현재 주식 수 기준으로 맞춰 분할 시점의 인위적 가격 단절을
없애고, 배당락으로 인한 가격 하락을 실제 손실이 아닌 것으로 정정한다.

`costs` 패키지(BT-8)의 "꺼짐=조용히 0" 관례와 달리, 여기서는 조정 비활성
(`config.splits=False`/`config.dividends=False`)이 조용한 통과가 아니다 —
`AdjustedQuote.applied`/`AdjustedFill.{splits,dividends}_applied`로 반환값에
그 사실이 그대로 남는다(applied=False). 호출자는 이 플래그를 결과 지표에
노출해야 하며, `adjusted == raw`만 보고 "조정할 게 없었다"고 오판해서는
안 된다(46번 "한계·가정 노출" 원칙, BT-20 DoD "미조정 모드는 결과 지표에
플래그로 표시" — 조정 미적용을 조용히 통과시키지 않는다).

`StockSplit.ratio`는 "1주가 몇 주가 되는가"(new/old) 관례다 — 2:1 분할은
`ratio=Decimal(2)`(과거 가격을 2로 나누고 수량은 2를 곱한다). 배당 조정은
CRSP 방식(배당락 전일 종가 대비 배당액 비율만큼 과거 가격을 낮추는 누적곱)을
채택했다(미검증: 벤더별 실제 조정 알고리즘 대조는 하지 않았다). 이 모듈은
시세 저장소를 조회하지 않는 순수 함수라 배당 이벤트가 배당락 전일 종가
(`prior_close`)를 직접 들고 다닌다 — 호출자가 그 종가를 함께 넘긴다.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.foundation.backtest.domain.models_v2 import AdjustmentsConfig

__all__ = [
    "StockSplit",
    "CashDividend",
    "AdjustedQuote",
    "AdjustedFill",
    "split_factor",
    "dividend_factor",
    "adjust_price_for_splits",
    "adjust_quantity_for_splits",
    "adjust_price_for_dividends",
    "adjust_fill",
]


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}는 tz-aware UTC datetime이어야 한다: {value}")


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name}는 음수·NaN을 허용하지 않는다: {value}")


def _reject_non_positive_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value <= 0:
        raise ValueError(f"{name}는 0 이하·NaN을 허용하지 않는다: {value}")


@dataclass(frozen=True, slots=True)
class StockSplit:
    """분할(또는 병합) 이벤트 1건. 2:1 분할은 `ratio=Decimal(2)`, 1:2
    병합(reverse split)은 `ratio=Decimal('0.5')`."""

    ex_date: datetime
    ratio: Decimal


@dataclass(frozen=True, slots=True)
class CashDividend:
    """현금배당 이벤트 1건. `prior_close`는 배당락 전일 종가 — 이 모듈은
    시세를 조회하지 않으므로 호출자가 직접 넘긴다."""

    ex_date: datetime
    amount: Decimal
    prior_close: Decimal


@dataclass(frozen=True, slots=True)
class AdjustedQuote:
    """단일 값(가격 또는 수량)의 원시값·조정값·조정 적용 여부.

    `applied=False`면 `adjusted == raw`지만, 이는 우연히 비율이 1이라는
    뜻이 아니라 설정으로 조정을 껐다는 뜻이다 — 호출자는 이 필드를 결과
    지표에 그대로 노출해야 한다."""

    raw: Decimal
    adjusted: Decimal
    applied: bool


@dataclass(frozen=True, slots=True)
class AdjustedFill:
    """체결 1건(가격+수량)에 대한 분할·배당 조정 결과.

    두 플래그를 따로 남기는 이유: `splits=True, dividends=False`처럼 한쪽만
    켜진 조합에서도 어느 쪽이 미조정 상태인지 결과 지표가 구분해 보여줘야
    하기 때문이다."""

    raw_price: Decimal
    raw_quantity: Decimal
    adjusted_price: Decimal
    adjusted_quantity: Decimal
    splits_applied: bool
    dividends_applied: bool


def _require_ordered_window(bar_time: datetime, as_of: datetime) -> None:
    _require_utc(bar_time, "bar_time")
    _require_utc(as_of, "as_of")
    if as_of < bar_time:
        raise ValueError(
            f"as_of는 bar_time보다 앞일 수 없다: bar_time={bar_time}, as_of={as_of}"
        )


def split_factor(
    splits: Sequence[StockSplit], *, bar_time: datetime, as_of: datetime
) -> Decimal:
    """`bar_time` 시점 원시가격을 `as_of` 시점 주식 수 기준으로 맞추는
    누적 배수. `bar_time`(배제) 초과 ~ `as_of`(포함) 이하 구간에 낙일
    (ex_date)이 있는 분할만 반영한다 — 반열린 구간(funding.py 정산 경계와
    같은 관례, `bar_time` 당일 발생한 분할은 그 봉이 이미 분할 이후
    가격이라고 가정해 배제한다)."""

    _require_ordered_window(bar_time, as_of)
    factor = Decimal(1)
    for index, split in enumerate(splits):
        _require_utc(split.ex_date, f"splits[{index}].ex_date")
        _reject_non_positive_or_nan(split.ratio, f"splits[{index}].ratio")
        if bar_time < split.ex_date <= as_of:
            factor *= split.ratio
    return factor


def dividend_factor(
    dividends: Sequence[CashDividend], *, bar_time: datetime, as_of: datetime
) -> Decimal:
    """`bar_time` 시점 원시가격에 곱해 `as_of` 기준 총수익(total return)
    조정가를 만드는 누적 배수(CRSP 방식: 배당락 전일 종가 대비 배당액
    비율만큼 과거 가격을 낮춘다). 구간 규칙은 `split_factor`와 동일한
    반열린 구간."""

    _require_ordered_window(bar_time, as_of)
    factor = Decimal(1)
    for index, dividend in enumerate(dividends):
        _require_utc(dividend.ex_date, f"dividends[{index}].ex_date")
        _reject_negative_or_nan(dividend.amount, f"dividends[{index}].amount")
        _reject_non_positive_or_nan(dividend.prior_close, f"dividends[{index}].prior_close")
        if bar_time < dividend.ex_date <= as_of:
            factor *= Decimal(1) - dividend.amount / dividend.prior_close
    return factor


def adjust_price_for_splits(
    config: AdjustmentsConfig,
    splits: Sequence[StockSplit],
    *,
    raw_price: Decimal,
    bar_time: datetime,
    as_of: datetime,
) -> AdjustedQuote:
    """`config.splits=False`면 다른 인자를 검증한 뒤에도 raw를 그대로
    돌려주되 `applied=False`를 명시한다(꺼짐을 조용히 raw==adjusted로만
    남기지 않는다)."""

    _reject_negative_or_nan(raw_price, "raw_price")
    factor = split_factor(splits, bar_time=bar_time, as_of=as_of)
    if not config.splits:
        return AdjustedQuote(raw=raw_price, adjusted=raw_price, applied=False)
    return AdjustedQuote(raw=raw_price, adjusted=raw_price / factor, applied=True)


def adjust_quantity_for_splits(
    config: AdjustmentsConfig,
    splits: Sequence[StockSplit],
    *,
    raw_quantity: Decimal,
    bar_time: datetime,
    as_of: datetime,
) -> AdjustedQuote:
    _reject_negative_or_nan(raw_quantity, "raw_quantity")
    factor = split_factor(splits, bar_time=bar_time, as_of=as_of)
    if not config.splits:
        return AdjustedQuote(raw=raw_quantity, adjusted=raw_quantity, applied=False)
    return AdjustedQuote(raw=raw_quantity, adjusted=raw_quantity * factor, applied=True)


def adjust_price_for_dividends(
    config: AdjustmentsConfig,
    dividends: Sequence[CashDividend],
    *,
    raw_price: Decimal,
    bar_time: datetime,
    as_of: datetime,
) -> AdjustedQuote:
    _reject_negative_or_nan(raw_price, "raw_price")
    factor = dividend_factor(dividends, bar_time=bar_time, as_of=as_of)
    if not config.dividends:
        return AdjustedQuote(raw=raw_price, adjusted=raw_price, applied=False)
    return AdjustedQuote(raw=raw_price, adjusted=raw_price * factor, applied=True)


def adjust_fill(
    config: AdjustmentsConfig,
    *,
    raw_price: Decimal,
    raw_quantity: Decimal,
    bar_time: datetime,
    as_of: datetime,
    splits: Sequence[StockSplit] = (),
    dividends: Sequence[CashDividend] = (),
) -> AdjustedFill:
    """원시 체결가·수량에 분할→배당 순서로 조정을 적용한다(분할은 가격·
    수량 모두에 영향을 주므로 먼저 적용하고, 배당은 가격에만 총수익
    조정을 더한다 — 조정된 가격 위에 배당 배수를 곱한다)."""

    price_after_splits = adjust_price_for_splits(
        config, splits, raw_price=raw_price, bar_time=bar_time, as_of=as_of
    )
    quantity_after_splits = adjust_quantity_for_splits(
        config, splits, raw_quantity=raw_quantity, bar_time=bar_time, as_of=as_of
    )
    price_after_dividends = adjust_price_for_dividends(
        config,
        dividends,
        raw_price=price_after_splits.adjusted,
        bar_time=bar_time,
        as_of=as_of,
    )
    return AdjustedFill(
        raw_price=raw_price,
        raw_quantity=raw_quantity,
        adjusted_price=price_after_dividends.adjusted,
        adjusted_quantity=quantity_after_splits.adjusted,
        splits_applied=price_after_splits.applied,
        dividends_applied=price_after_dividends.applied,
    )
