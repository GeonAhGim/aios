"""BT-8 — 무기한선물 펀딩비 모델(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-8, §3.4(`costs: {funding: bool, borrow_apr}`), §9.5 BT-8(DoD: "일할 계산 정확").

무기한선물 펀딩비는 고정 인터벌(기본 8시간)마다, UNIX epoch
(1970-01-01T00:00:00Z)를 기준으로 그 배수인 시각에 정산된다고 가정한다
(바이낸스·바이비트 등 주요 거래소의 00:00/08:00/16:00 UTC 관례를
채택했다 — 미검증: 거래소별 실제 스펙 대조는 하지 않았다. 다른 정산
시각을 쓰는 거래소는 `interval_hours`로 교정한다).

부호 관례(무기한선물 표준): `funding_rate > 0`이면 롱이 숏에게 지급한다.
반환값은 포지션 보유자 관점의 순비용이다 — 양수는 지급(비용), 음수는
수취(수익)다. `side=BUY`는 롱, `side=SELL`은 숏 포지션을 뜻한다(주문
방향이 아니라 보유 포지션 방향).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.data.models.trading import OrderSide
from src.foundation.backtest.domain.costs import round_cost
from src.foundation.backtest.domain.models_v2 import CostsConfig

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_DEFAULT_INTERVAL_HOURS = 8


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}는 tz-aware UTC datetime이어야 한다: {value}")


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name}는 음수·NaN을 허용하지 않는다: {value}")


def _ceil_div(numerator: Decimal, denominator: Decimal) -> int:
    quotient, remainder = divmod(numerator, denominator)
    if remainder != 0:
        quotient += 1
    return int(quotient)


def count_funding_settlements(
    *, entry_time: datetime, exit_time: datetime, interval_hours: int = _DEFAULT_INTERVAL_HOURS
) -> int:
    """반열린 구간 `[entry_time, exit_time)`에 포함되는 정산 시각 개수.

    진입이 정확히 정산 시각이면 그 정산을 포함하고(그 순간부터 보유),
    청산이 정확히 정산 시각이면 그 정산은 제외한다(그 순간 보유 종료 —
    다음 트레이드가 이어받는다는 가정, 경계 이중 계산 방지).
    """

    _require_utc(entry_time, "entry_time")
    _require_utc(exit_time, "exit_time")
    if interval_hours <= 0:
        raise ValueError(f"interval_hours는 양수여야 한다: {interval_hours}")
    if exit_time < entry_time:
        raise ValueError(
            f"exit_time은 entry_time보다 앞일 수 없다: entry={entry_time}, exit={exit_time}"
        )

    interval_seconds = Decimal(interval_hours) * Decimal(3600)
    entry_offset = Decimal((entry_time - _EPOCH).total_seconds())
    exit_offset = Decimal((exit_time - _EPOCH).total_seconds())
    first_n = _ceil_div(entry_offset, interval_seconds)
    last_n_exclusive = _ceil_div(exit_offset, interval_seconds)
    return max(0, last_n_exclusive - first_n)


def compute_funding_cost(
    config: CostsConfig,
    *,
    side: OrderSide,
    notional: Decimal,
    funding_rate: Decimal,
    entry_time: datetime,
    exit_time: datetime,
    interval_hours: int = _DEFAULT_INTERVAL_HOURS,
) -> Decimal:
    """보유 구간 동안 정산된 펀딩비 순비용(지급 양수/수취 음수).

    `config.funding=False`면 다른 인자를 검증하지 않고 즉시 `Decimal('0')`
    을 반환한다(꺼진 비용은 예외가 아니라 무비용).
    """

    if not config.funding:
        return Decimal("0")

    _reject_negative_or_nan(notional, "notional")
    if funding_rate.is_nan():
        raise ValueError(f"funding_rate는 NaN을 허용하지 않는다: {funding_rate}")

    settlements = count_funding_settlements(
        entry_time=entry_time, exit_time=exit_time, interval_hours=interval_hours
    )
    direction = Decimal(1) if side == OrderSide.BUY else Decimal(-1)
    cost = notional * funding_rate * direction * settlements
    return round_cost(cost)
