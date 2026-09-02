"""9.5 — Data Distrust Mode 판정 (쿼럼 확장).

Spec: 기능설계문서_v1.20.md#FD-9.5, 정책문서 8.1-A. FD-2.6(5.11,
core/parser/data_trust_checker.py)의 2소스 히스테리시스를 다중 오라클
쿼럼 방어까지 포함해 시스템 상태로 승격 — 이 모듈이 5.11을 대체한다.

판정 로직: primary + 참조 최소 2개(총 3소스 쿼럼)의 중앙값 대비 편차를
히스테리시스로 감시하고, 피드 비교와 무관한 통계적 타당성 검사(최근
캔들 실현변동성 대비 괴리 5배 초과 여부)를 병행 — 둘 다 "이상없음"이어야
정상 유지. 수치(5배, 1.5%/0.75%+60초)는 Draft, 5.11과 동일 기본값.

R-48(PM 결정, 2026-09-03) — 참조가 *하나도* 없으면(크립토는 독립 참조가
아직 2개뿐, 둘 다 실패 시 바로 이 상태) SUSPICIOUS로 고착시키지 않는다
— 게이트가 신규 진입을 영구히 막게 된다. 대신 `DEGRADED_SINGLE_SOURCE`로
분리해 피드 비교는 건너뛰고 통계 검사만 적용, 실패 시에만(이례적 급변)
DISTRUSTED로 격상한다."""
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
MIN_QUORUM_SOURCES = 2  # primary+참조 1개 이상이면 항상 충족(check() 참조)


class DataDistrustLevel(str, Enum):
    NORMAL = "NORMAL"
    SUSPICIOUS = "SUSPICIOUS"  # 쿼럼 불성립("판정 불가")
    DISTRUSTED = "DISTRUSTED"
    DEGRADED_SINGLE_SOURCE = "DEGRADED_SINGLE_SOURCE"  # R-48, 참조 0개 — 게이트 DENY 없음


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

    def restore(
        self, symbol: str, level: DataDistrustLevel, since: float | None = None
    ) -> None:
        """R-48 — 재시작 후 영속 상태에서 인메모리 상태를 복원(기동 시 1회,
        distrust_wiring.py). `since`는 절대시각이 아니라 EXIT 타이머 경과
        초(모노토닉은 재시작마다 0 리셋 — wall-clock 직접대입 불가, 호출부가
        updated_at과 now() 차이로 계산). None이면 타이머 새로 시작(더 오래
        DISTRUSTED 유지 — 안전한 방향 편향)."""
        self._level[symbol] = level
        if since is not None:
            self._below_exit_since[symbol] = time.monotonic() - since

    async def check(
        self,
        symbol: str,
        primary: Ticker,
        references: list[Ticker | None],
        recent_candles: list[Candle],
    ) -> DataDistrustLevel:
        available_references = [t for t in references if t is not None]

        if not available_references:
            # R-48 — 참조 0개, median 비교 불가 -> 통계 검사만으로 판정.
            degraded = DataDistrustLevel.DEGRADED_SINGLE_SOURCE
            distrusted = DataDistrustLevel.DISTRUSTED
            new_level = degraded if self._statistical_check(primary, recent_candles) else distrusted
            return await self._transition(symbol, new_level, reason="no_reference_sources")

        available = [primary, *available_references]  # 참조 1개 이상 -> quorum 항상 충족
        assert len(available) >= MIN_QUORUM_SOURCES

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
