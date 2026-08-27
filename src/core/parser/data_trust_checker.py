"""5.11 — 데이터 신뢰도 검증기 (FD-2.6, 8.1-A 히스테리시스).

Spec: 기능설계문서_v1.20.md#FD-2.6, 07_logging_config_v1.3.md#§7.2
(risk_policy.yaml data_distrust 섹션)

Primary 피드와 Reference 피드 간 괴리를 감시해 오라클 왜곡을 방어한다.
히스테리시스: 진입 1.5% / 해제 0.75%+60초(risk_policy.yaml Draft 수치,
07번 §7.2) — 괴리가 임계치 부근에서 오르내려도 상태가 매번 뒤집히지
않도록 진입/해제 기준을 비대칭으로 둔다.

Reference 소스가 아직 없으면(Phase 1 초기) 검증 자체를 skip하고 WARNING —
"판단 불가"를 "정상"으로 조용히 단정하지 않는다(직전 상태를 그대로 유지).

인스턴스를 심볼별로 재사용해야 히스테리시스가 의미 있다 — 매 호출마다 새
인스턴스를 만들면 상태가 소실된다. 9.5(작업트리 9번, core/safety/
data_distrust.py)에서 2소스 비교를 3소스 쿼럼으로 확장할 때 이 클래스를
대체/흡수한다.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any

from src.data.models.market_data import Ticker

logger = logging.getLogger(__name__)

PublishFn = Callable[[str, dict[str, Any]], Awaitable[None]]

DEFAULT_ENTER_THRESHOLD_PCT = Decimal("1.5")
DEFAULT_EXIT_THRESHOLD_PCT = Decimal("0.75")
DEFAULT_EXIT_SUSTAIN_SECONDS = 60.0


class DataTrustChecker:
    def __init__(
        self,
        *,
        enter_threshold_pct: Decimal = DEFAULT_ENTER_THRESHOLD_PCT,
        exit_threshold_pct: Decimal = DEFAULT_EXIT_THRESHOLD_PCT,
        exit_sustain_seconds: float = DEFAULT_EXIT_SUSTAIN_SECONDS,
        publish: PublishFn | None = None,
    ) -> None:
        self._enter_threshold_pct = enter_threshold_pct
        self._exit_threshold_pct = exit_threshold_pct
        self._exit_sustain_seconds = exit_sustain_seconds
        self._publish = publish
        self._distrusted: dict[str, bool] = {}
        self._below_exit_since: dict[str, float] = {}

    async def check(self, primary: Ticker, reference: Ticker | None) -> bool:
        """반환값: 이 호출 이후 해당 심볼이 불신 상태인지(True=불신)."""
        symbol = primary.symbol

        if reference is None:
            logger.warning(
                "Reference 피드 없음 — 데이터 신뢰도 검증 skip(symbol=%s), 직전 상태 유지",
                symbol,
            )
            return self._distrusted.get(symbol, False)

        if reference.price == 0:
            logger.warning("Reference 가격이 0 — 괴리율 계산 불가(symbol=%s)", symbol)
            return self._distrusted.get(symbol, False)

        deviation_pct = abs(primary.price - reference.price) / reference.price * 100
        is_distrusted = self._distrusted.get(symbol, False)
        now = time.monotonic()

        if not is_distrusted:
            if deviation_pct >= self._enter_threshold_pct:
                is_distrusted = True
                self._below_exit_since.pop(symbol, None)
                await self._publish_event("market.distrust.entered", symbol, deviation_pct)
        else:
            if deviation_pct < self._exit_threshold_pct:
                since = self._below_exit_since.setdefault(symbol, now)
                if now - since >= self._exit_sustain_seconds:
                    is_distrusted = False
                    self._below_exit_since.pop(symbol, None)
                    await self._publish_event("market.distrust.exited", symbol, deviation_pct)
            else:
                # 해제 임계치 아래로 내려갔다가 다시 올라오면 지속시간 카운터 리셋
                self._below_exit_since.pop(symbol, None)

        self._distrusted[symbol] = is_distrusted
        return is_distrusted

    async def _publish_event(self, topic: str, symbol: str, deviation_pct: Decimal) -> None:
        if self._publish is not None:
            await self._publish(topic, {"symbol": symbol, "deviation_pct": str(deviation_pct)})
