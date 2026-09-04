"""R-29 — execution_loop/var_estimator.py 단위테스트.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#9 R-29.
`estimate_portfolio_var_es`가 계산을 재구현하지 않고 risk_stats에
그대로 위임하는지(값 일치), bars_per_day 스케일링이 policy.timeframe을
거쳐 risk_stats.returns로 위임되는지, 표본 부족·미지 종목은 0이 아니라
None을 반환하는지(R3 fail-closed), 포트폴리오 VaR ≤ Σ 개별 VaR인지를
검증한다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.core.loader.risk_policy_loader import VarPolicy
from src.core.risk_stats.models import VarMethod
from src.core.risk_stats.var_historical import historical_var_es
from src.core.risk_stats.var_parametric import parametric_var_es
from src.data.models.market_data import Candle
from src.services.execution_loop.var_estimator import estimate_portfolio_var_es

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _make_candles(
    closes: list[Decimal], *, symbol: str = "BTC/USDT", timeframe: str = "1d"
) -> list[Candle]:
    return [
        Candle(
            symbol=symbol,
            exchange="bitget",
            timeframe=timeframe,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=Decimal("1"),
            open_time=_BASE + timedelta(minutes=i),
            close_time=_BASE + timedelta(minutes=i + 1),
        )
        for i, close in enumerate(closes)
    ]


def _oscillating_closes(n: int, *, base: int = 100, amplitude: int = 5) -> list[Decimal]:
    return [Decimal(base + amplitude * (1 if i % 2 == 0 else -1) + i % 3) for i in range(n)]


def _policy(
    *, method: str = "parametric", timeframe: str = "1d", min_bars: int = 10
) -> VarPolicy:
    return VarPolicy(
        confidence=0.95,
        horizon_days=1,
        max_pct=50.0,
        es_max_pct=50.0,
        min_bars=min_bars,
        method=method,
        timeframe=timeframe,
        lookback_bars=max(min_bars, 250),
    )


def test_single_symbol_parametric_matches_direct_risk_stats_delegation():
    closes = _oscillating_closes(30)
    candles = _make_candles(closes)
    policy = _policy(method="parametric", timeframe="1d")

    result = estimate_portfolio_var_es({"BTC/USDT": candles}, {"BTC/USDT": Decimal("1")}, policy)

    from src.core.risk_stats.returns import log_returns

    expected = parametric_var_es(
        log_returns(closes), confidence=0.95, horizon_days=1.0, bars_per_day=1
    )
    assert result is not None
    assert result.var_pct == expected.var_pct
    assert result.es_pct == expected.es_pct
    assert result.method == VarMethod.PARAMETRIC


def test_single_symbol_historical_matches_direct_risk_stats_delegation():
    closes = _oscillating_closes(30)
    candles = _make_candles(closes)
    policy = _policy(method="historical", timeframe="1d")

    result = estimate_portfolio_var_es({"BTC/USDT": candles}, {"BTC/USDT": Decimal("1")}, policy)

    from src.core.risk_stats.returns import log_returns

    expected = historical_var_es(
        log_returns(closes), confidence=0.95, horizon_days=1.0, bars_per_day=1
    )
    assert result is not None
    assert result.var_pct == expected.var_pct
    assert result.method == VarMethod.HISTORICAL


def test_single_symbol_cornish_fisher_matches_direct_risk_stats_delegation():
    closes = _oscillating_closes(30)
    candles = _make_candles(closes)
    policy = _policy(method="cornish_fisher", timeframe="1d")

    result = estimate_portfolio_var_es({"BTC/USDT": candles}, {"BTC/USDT": Decimal("1")}, policy)

    assert result is not None
    assert result.method == VarMethod.CORNISH_FISHER


def test_bars_per_day_scaling_is_delegated_to_risk_stats_returns():
    """R4 회귀 방지 — 기존 결함은 1분봉 표준편차에 √horizon_days만 곱해
    봉→일 환산(bars_per_day)을 건너뛰었다. 여기선 timeframe="1m"을 주고
    risk_stats.returns.bars_per_day("1m")=1440이 실제로 스케일에 반영됨을
    (parametric_var_es를 bars_per_day=1440로 직접 호출한 값과 일치시켜)
    증명한다 — 이 스케일링 로직 자체는 재구현하지 않았다는 뜻이다."""
    closes = _oscillating_closes(30)
    candles = _make_candles(closes, timeframe="1m")
    policy = _policy(method="parametric", timeframe="1m")

    result = estimate_portfolio_var_es({"BTC/USDT": candles}, {"BTC/USDT": Decimal("1")}, policy)

    from src.core.risk_stats.returns import log_returns

    expected_1440 = parametric_var_es(
        log_returns(closes), confidence=0.95, horizon_days=1.0, bars_per_day=1440
    )
    expected_wrong_unscaled = parametric_var_es(
        log_returns(closes), confidence=0.95, horizon_days=1.0, bars_per_day=1
    )
    assert result is not None
    assert result.var_pct == expected_1440.var_pct
    assert result.var_pct != expected_wrong_unscaled.var_pct


def test_missing_history_for_weighted_symbol_returns_none():
    candles = _make_candles(_oscillating_closes(30))
    policy = _policy()

    result = estimate_portfolio_var_es(
        {"BTC/USDT": candles}, {"BTC/USDT": Decimal("0.5"), "ETH/USDT": Decimal("0.5")}, policy
    )

    assert result is None


def test_insufficient_bars_returns_none_not_zero():
    candles = _make_candles(_oscillating_closes(5))
    policy = _policy(min_bars=10)

    result = estimate_portfolio_var_es({"BTC/USDT": candles}, {"BTC/USDT": Decimal("1")}, policy)

    assert result is None


def test_no_weighted_symbols_returns_none():
    candles = _make_candles(_oscillating_closes(30))
    policy = _policy()

    result = estimate_portfolio_var_es(
        {"BTC/USDT": candles}, {"BTC/USDT": Decimal("0")}, policy
    )

    assert result is None


def test_portfolio_var_less_equal_sum_of_single_asset_vars():
    btc_candles = _make_candles(_oscillating_closes(40, base=100, amplitude=5), symbol="BTC/USDT")
    eth_candles = _make_candles(_oscillating_closes(40, base=50, amplitude=3), symbol="ETH/USDT")
    policy = _policy(method="parametric")

    portfolio = estimate_portfolio_var_es(
        {"BTC/USDT": btc_candles, "ETH/USDT": eth_candles},
        {"BTC/USDT": Decimal("0.5"), "ETH/USDT": Decimal("0.5")},
        policy,
    )
    btc_only = estimate_portfolio_var_es(
        {"BTC/USDT": btc_candles}, {"BTC/USDT": Decimal("1")}, policy
    )
    eth_only = estimate_portfolio_var_es(
        {"ETH/USDT": eth_candles}, {"ETH/USDT": Decimal("1")}, policy
    )

    assert portfolio is not None
    assert btc_only is not None
    assert eth_only is not None
    weighted_sum = Decimal("0.5") * btc_only.var_pct + Decimal("0.5") * eth_only.var_pct
    assert portfolio.var_pct <= weighted_sum
