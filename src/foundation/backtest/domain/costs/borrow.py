"""BT-8 — 차입(대차) 비용 모델(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-8, §3.4(`costs.borrow_apr: Decimal | None`), §9.5 BT-8(DoD: "일할 계산 정확").

day-count 관례는 ACT/365(실제 경과일수 / 365)로 고정한다 — 분모를 실제
연도 길이(윤년 366)로 바꾸는 ACT/365.25나 채권시장의 ACT/360과 달리,
크립토·주식 공매도 대차 이자는 연 365일 고정을 관행으로 쓰는 경우가
많다는 점을 채택 근거로 삼았다(미검증: 거래소·프라임브로커별 실제 대차
계약서 대조는 하지 않았다). 분자(실제 경과일수)는 윤년이어도 달력 그대로
셈한다 — 분모만 365로 고정한다(ACT/365 fixed).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from src.foundation.backtest.domain.costs import round_cost
from src.foundation.backtest.domain.models_v2 import CostsConfig

_DAYS_PER_YEAR = Decimal(365)
_SECONDS_PER_DAY = Decimal(86400)


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}는 tz-aware UTC datetime이어야 한다: {value}")


def _reject_negative_or_nan(value: Decimal, name: str) -> None:
    if value.is_nan() or value < 0:
        raise ValueError(f"{name}는 음수·NaN을 허용하지 않는다: {value}")


def compute_borrow_cost(
    config: CostsConfig, *, notional: Decimal, entry_time: datetime, exit_time: datetime
) -> Decimal:
    """실제 보유 일수(ACT/365) 기준 차입 비용.

    `config.borrow_apr=None`이면 다른 인자를 검증하지 않고 즉시
    `Decimal('0')`을 반환한다(무차입 전략은 예외가 아니라 무비용).
    """

    if config.borrow_apr is None:
        return Decimal("0")

    _require_utc(entry_time, "entry_time")
    _require_utc(exit_time, "exit_time")
    _reject_negative_or_nan(notional, "notional")
    if exit_time < entry_time:
        raise ValueError(
            f"exit_time은 entry_time보다 앞일 수 없다: entry={entry_time}, exit={exit_time}"
        )

    holding_days = Decimal((exit_time - entry_time).total_seconds()) / _SECONDS_PER_DAY
    cost = notional * config.borrow_apr * holding_days / _DAYS_PER_YEAR
    return round_cost(cost)
