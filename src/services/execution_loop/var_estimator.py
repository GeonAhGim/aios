"""R-29 — execution_loop/var_estimator.py

risk_stats 어댑터: 캔들 히스토리 + 포트폴리오 가중치를 log 수익률로 바꿔
`src/core/risk_stats/{returns,portfolio,var_parametric,var_historical,
var_cornish_fisher}.py`(R-18~R-20, task-104 5ec19fb)에 위임한다 — VaR/ES
계산 자체는 여기서 재구현하지 않는다.

이 파일이 교체하는 이전 Draft는 1분봉 표준편차에 일 단위 horizon만
곱해(봉→일 환산 누락) 위험을 과소평가했다(§9 R4). 이제 bars_per_day
스케일링은 전부 `risk_stats.returns.scale_sigma`(하위 호출부에서 사용)에
위임되므로 이 어댑터가 직접 시간 단위를 계산하지 않는다.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Any

import numpy as np

from src.core.loader.risk_policy_loader import VarPolicy
from src.core.risk_stats.models import VarEs, VarMethod
from src.core.risk_stats.portfolio import portfolio_returns, portfolio_var_es
from src.core.risk_stats.returns import bars_per_day, log_returns
from src.core.risk_stats.var_cornish_fisher import cornish_fisher_var_es
from src.core.risk_stats.var_historical import historical_var_es
from src.core.risk_stats.var_parametric import parametric_var_es
from src.data.models.market_data import Candle

_METHODS: dict[str, VarMethod] = {m.value.lower(): m for m in VarMethod}


def estimate_portfolio_var_es(
    histories: Mapping[str, Sequence[Candle]],
    weights: Mapping[str, Decimal],
    policy: VarPolicy,
) -> VarEs | None:
    """symbol별 캔들 히스토리 + 가중치 → 포트폴리오 VaR/ES.

    표본 부족(가중치가 있는 종목의 히스토리가 없거나, log 수익률 개수 <
    `policy.min_bars`)은 None — 호출자가 "판단 불가"로 DENY 처리하지,
    조용히 0(무위험)으로 통과시키지 않는다(`candle_history.py`의
    stale-cache 미반환 관례와 동일한 fail-closed 원칙, R3)."""
    method = _METHODS.get(policy.method.lower())
    if method is None:
        return None

    symbols = sorted(symbol for symbol, w in weights.items() if w != 0)
    if not symbols:
        return None

    bars_per_day_count = bars_per_day(policy.timeframe)
    horizon_days = float(policy.horizon_days)

    returns_by_symbol: dict[str, np.ndarray[Any, Any]] = {}
    for symbol in symbols:
        candles = histories.get(symbol)
        if not candles:
            return None
        r = log_returns([c.close for c in candles])
        if r.size < policy.min_bars:
            return None
        returns_by_symbol[symbol] = r

    if len(symbols) == 1:
        r = returns_by_symbol[symbols[0]]
        if method == VarMethod.PARAMETRIC:
            return parametric_var_es(
                r, confidence=policy.confidence, horizon_days=horizon_days,
                bars_per_day=bars_per_day_count,
            )
        if method == VarMethod.HISTORICAL:
            return historical_var_es(
                r, confidence=policy.confidence, horizon_days=horizon_days,
                bars_per_day=bars_per_day_count,
            )
        return cornish_fisher_var_es(
            r, confidence=policy.confidence, horizon_days=horizon_days,
            bars_per_day=bars_per_day_count,
        )

    min_len = min(r.size for r in returns_by_symbol.values())
    R = np.column_stack([returns_by_symbol[s][-min_len:] for s in symbols])
    w = [weights[s] for s in symbols]

    if method in (VarMethod.PARAMETRIC, VarMethod.HISTORICAL):
        return portfolio_var_es(
            method, R, w, confidence=policy.confidence, horizon_days=horizon_days,
            bars_per_day=bars_per_day_count,
        )

    # CORNISH_FISHER: portfolio.py는 PARAMETRIC/HISTORICAL만 지원한다 —
    # 가중 합산 수익률 시계열(portfolio_returns, 순수 선형결합)을 만들어
    # cornish_fisher_var_es에 그대로 위임한다(재구현이 아니라 조합).
    pr = portfolio_returns(R, w)
    if pr.size < 3:
        return None
    return cornish_fisher_var_es(
        pr, confidence=policy.confidence, horizon_days=horizon_days,
        bars_per_day=bars_per_day_count,
    )
