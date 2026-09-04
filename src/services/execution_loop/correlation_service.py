"""R-29 — execution_loop/correlation_service.py

캐시된 캔들 히스토리(`candle_history.py`, R-28)로 실제 상관행렬을 계산해
상관 노출을 집계한다. 상관 계산 자체는
`src/core/risk_stats/{returns,correlation_matrix}.py`(R-18,20,
task-104 5ec19fb)에 위임한다 — 재구현하지 않는다.

이 모듈이 대체하는 `correlation.py`는 5개 심볼 쌍에 대한 하드코딩
상관계수 표를 썼고, 표에 없는 페어는 `correlation_with()`가 조용히
0.0(무상관)으로 치환해 통과시켰다(R3 감사 지적). 여기서는 미지 페어·
최소 중첩(`min_overlap`) 미달을 None으로 반환해 호출자가 DENY로 처리하게
한다 — 절대 0.0으로 암묵 치환하지 않는다.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import numpy as np

from src.core.risk_stats.correlation_matrix import pearson_matrix
from src.core.risk_stats.returns import log_returns
from src.data.models.market_data import Candle


def correlated_exposure(
    histories: Mapping[str, Sequence[Candle]],
    positions: Sequence[tuple[str, Decimal]],
    target: str,
    *,
    threshold: float,
    min_overlap: int,
) -> tuple[Decimal | None, float | None]:
    """`positions`는 (symbol, exposure_value) 목록 — 단위(시가평가액·
    지분율 등)는 호출자가 정하고 그대로 합산해 돌려준다.

    대상(target)과 같은 symbol은 상관 1.0으로 취급한다(자기 자신 — 히스토리
    불필요). 다른 symbol은 `histories`에 있는 종가로 log 수익률을 만들어
    Pearson 상관을 계산한다. target 자신의 히스토리가 없거나, 상대 symbol의
    히스토리가 없거나, 중첩 표본이 `min_overlap` 미만이면 그 페어는 결손
    (correlation unresolved)이고 — 결손 페어가 하나라도 있으면 0.0으로
    암묵 치환하지 않고 전체를 (None, None)으로 반환한다(R3 fail-closed)."""
    aggregated: dict[str, Decimal] = {}
    for symbol, value in positions:
        aggregated[symbol] = aggregated.get(symbol, Decimal("0")) + value

    if not aggregated:
        return Decimal("0"), 0.0

    other_symbols = [symbol for symbol in aggregated if symbol != target]

    returns: dict[str, np.ndarray[Any, Any]] = {}
    if other_symbols:
        target_candles = histories.get(target)
        if not target_candles:
            return None, None
        target_returns = log_returns([c.close for c in target_candles])
        if target_returns.size == 0:
            return None, None
        returns[target] = target_returns

        for symbol in other_symbols:
            candles = histories.get(symbol)
            if not candles:
                return None, None
            r = log_returns([c.close for c in candles])
            if r.size == 0:
                return None, None
            returns[symbol] = r

    matrix = pearson_matrix(returns, min_overlap=min_overlap) if returns else {}

    exposed = Decimal("0")
    max_corr: float | None = None
    for symbol, value in aggregated.items():
        corr: float | None = 1.0 if symbol == target else matrix.get((target, symbol))
        if corr is None:
            return None, None
        max_corr = corr if max_corr is None else max(max_corr, corr)
        if corr > threshold:
            exposed += value

    return exposed, max_corr
