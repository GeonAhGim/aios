"""9.5 — Data Distrust Mode 판정 (쿼럼 확장).

Spec: 기능설계문서_v1.20.md#FD-9.5, 정책문서 8.1-A

FD-2.6(5.11, core/parser/data_trust_checker.py)의 2소스 히스테리시스를
시스템 상태로 승격 — 다중 오라클 동시 스푸핑 방어(쿼럼)까지 포함해
완성한다. 이 모듈이 5.11을 대체한다(작업트리 10번 문서 9.5 각주: "5.11
기존 리프 확장").

판정 로직: primary + Reference 최소 2개(총 3소스 쿼럼)의 중앙값을 계산해
primary가 그 중앙값에서 얼마나 벗어났는지를 히스테리시스로 감시하고,
피드 비교와 무관한 통계적 타당성 검사(최근 캔들 실현변동성 대비 현재
괴리가 5배 초과하는지)를 병행한다 — 둘 다 "이상없음"이어야 정상 유지.

편차/해석: 원문 수치(5배, 1.5%/0.75%+60초)는 Draft이며 여기서는 5.11과
동일한 기본값을 재사용한다. "3소스 중 2개 이상 동시 조회 실패"는 primary
포함 전체 가용 소스가 2개 미만인 경우로 해석했다(대부분의 실무 구현에서
primary 자체는 항상 응답 가능하다고 가정하지 않는 것이 안전측).
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from enum import Enum
from statistics import median
from typing import Any

from src.data.models.market_data import Candle, Ticker

logger = logging.getLogger(__name__)

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]

DEFAULT_DEVIATION_ENTER_PCT = Decimal("1.5")
DEFAULT_DEVIATION_EXIT_PCT = Decimal("0.75")
DEFAULT_EXIT_SUSTAIN_SECONDS = 60.0
DEFAULT_VOLATILITY_MULTIPLIER = Decimal("5")
MIN_QUORUM_SOURCES = 2  # 미만이면 "판정 불가"(SUSPICIOUS)


class DataDistrustLevel(str, Enum):
    NORMAL = "NORMAL"
    SUSPICIOUS = "SUSPICIOUS"  # 쿼럼 불성립("판정 불가") — 정상도 불신도 아님
    DISTRUSTED = "DISTRUSTED"


def _realized_volatility(candles: list[Candle]) -> Decimal:
    if len(candles) < 2:
        return Decimal("0")
    closes = [c.close for c in candles]
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    mean_return: Decimal = sum(returns, Decimal("0")) / len(returns)
    variance: Decimal = sum(((r - mean_return) ** 2 for r in returns), Decimal("0")) / len(
        returns
    )
    result: Decimal = variance.sqrt()
    return result


class DataDistrustMonitor:
    def __init__(
        self,
        *,
        deviation_enter_pct: Decimal = DEFAULT_DEVIATION_ENTER_PCT,
        deviation_exit_pct: Decimal = DEFAULT_DEVIATION_EXIT_PCT,
        exit_sustain_seconds: float = DEFAULT_EXIT_SUSTAIN_SECONDS,
        volatility_multiplier: Decimal = DEFAULT_VOLATILITY_MULTIPLIER,
        publish: PublishFn | None = None,
    ) -> None:
        self._deviation_enter_pct = deviation_enter_pct
        self._deviation_exit_pct = deviation_exit_pct
        self._exit_sustain_seconds = exit_sustain_seconds
        self._volatility_multiplier = volatility_multiplier
        self._publish = publish
        self._level: dict[str, DataDistrustLevel] = {}
        self._below_exit_since: dict[str, float] = {}

    def current_level(self, symbol: str) -> DataDistrustLevel:
        return self._level.get(symbol, DataDistrustLevel.NORMAL)

    async def check(
        self,
        symbol: str,
        primary: Ticker,
        references: list[Ticker | None],
        recent_candles: list[Candle],
    ) -> DataDistrustLevel:
        available = [t for t in [primary, *references] if t is not None]

        if len(available) < MIN_QUORUM_SOURCES:
            return await self._transition(
                symbol, DataDistrustLevel.SUSPICIOUS, reason="quorum_not_met"
            )

        median_price = median(t.price for t in available)
        if median_price == 0:
            return await self._transition(
                symbol, DataDistrustLevel.SUSPICIOUS, reason="median_zero"
            )

        deviation_pct = abs(primary.price - median_price) / median_price * 100
        feed_ok = self._apply_hysteresis(symbol, deviation_pct)
        stat_ok = self._statistical_check(primary, recent_candles)

        new_level = (
            DataDistrustLevel.NORMAL if (feed_ok and stat_ok) else DataDistrustLevel.DISTRUSTED
        )
        return await self._transition(symbol, new_level, reason="feed_and_stat_check")

    def _apply_hysteresis(self, symbol: str, deviation_pct: Decimal) -> bool:
        """True면 "정상 범위"(불신 아님). 5.11과 동일한 진입/해제 비대칭 로직."""
        is_distrusted = self._level.get(symbol) == DataDistrustLevel.DISTRUSTED
        now = time.monotonic()

        if not is_distrusted:
            if deviation_pct >= self._deviation_enter_pct:
                return False
            return True

        if deviation_pct < self._deviation_exit_pct:
            since = self._below_exit_since.setdefault(symbol, now)
            if now - since >= self._exit_sustain_seconds:
                self._below_exit_since.pop(symbol, None)
                return True
            return False
        self._below_exit_since.pop(symbol, None)
        return False

    def _statistical_check(self, primary: Ticker, recent_candles: list[Candle]) -> bool:
        """피드 비교와 무관한 타당성 검사 — 실현변동성 대비 표준편차 5배
        초과 여부. 캔들이 부족하면(신뢰 기준 없음) 검사를 skip하고 통과
        처리(조용히 항상 실패시키지 않는다 — feed_ok 쪽 검사가 여전히 살아있음)."""
        if len(recent_candles) < 2:
            return True
        last_close = recent_candles[-1].close
        if last_close == 0:
            return True
        volatility = _realized_volatility(recent_candles)
        move_pct = abs(primary.price - last_close) / last_close
        if volatility == 0:
            # 과거 변동이 전혀 없던 시장에서 어떤 움직임이든 통계적으로 이례적이다.
            return move_pct == 0
        return move_pct <= volatility * self._volatility_multiplier

    async def _transition(
        self, symbol: str, new_level: DataDistrustLevel, *, reason: str
    ) -> DataDistrustLevel:
        old_level = self._level.get(symbol, DataDistrustLevel.NORMAL)
        self._level[symbol] = new_level
        if new_level != old_level:
            logger.warning(
                "DataDistrustLevel 전이: symbol=%s %s -> %s (%s)",
                symbol,
                old_level.value,
                new_level.value,
                reason,
            )
            if self._publish is not None:
                await self._publish(
                    "market.distrust.level_changed",
                    {"symbol": symbol, "level": new_level.value, "reason": reason},
                )
        return new_level
