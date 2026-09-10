"""R-29 — execution_loop/var_estimator.py 단위테스트.

Spec: docs/specs/L4_risk_and_safety_v1.0.md#9 R-29.
`estimate_portfolio_var_es`가 계산을 재구현하지 않고 risk_stats에
그대로 위임하는지(값 일치), bars_per_day 스케일링이 policy.timeframe을
거쳐 risk_stats.returns로 위임되는지, 표본 부족·미지 종목은 0이 아니라
None을 반환하는지(R3 fail-closed), 포트폴리오 VaR ≤ Σ 개별 VaR인지를
검증한다.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from src.core.loader.risk_policy_loader import VarPolicy, load_risk_policy
from src.core.risk.decision import RiskOutcome
from src.core.risk.inputs import (
    ActivityInputs,
    EquityInputs,
    ExposureSnapshot,
    OrderIntent,
    RiskInputs,
    SafetyInputs,
    StatsInputs,
)
from src.core.risk.rules.var_es import var_es as var_es_rule
from src.core.risk_stats.models import VarMethod
from src.core.risk_stats.var_historical import historical_var_es
from src.core.risk_stats.var_parametric import parametric_var_es
from src.data.models.market_data import Candle
from src.services.execution_loop.var_estimator import estimate_portfolio_var_es

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)
_RISK_NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _risk_inputs(stats: StatsInputs) -> RiskInputs:
    """`rules/var_es.py`가 소비하는 최소 `RiskInputs` — 이 테스트가 검증하려는
    필드(stats)만 격리해서 게이트 체인을 재현하기 위한 헬퍼."""
    return RiskInputs(
        tenant_id=uuid4(),
        execution_ref="exec:1",
        certified_badge=True,
        allocated_capital=Decimal("1000"),
        intent=OrderIntent(
            symbol="BTC/USDT", asset_class="CRYPTO_SPOT", side="BUY", quantity=Decimal("0.1"),
            ref_price=Decimal("50000"), notional=Decimal("5000"), reduce_only=False,
            strategy_id="strat-1", strategy_version="1.0", capital_pct=Decimal("10"),
        ),
        equity=EquityInputs(as_of=_RISK_NOW),
        exposure=ExposureSnapshot(as_of=_RISK_NOW),
        stats=stats,
        activity=ActivityInputs(),
        safety=SafetyInputs(),
        limits=(),
        as_of=_RISK_NOW,
    )


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


def test_unsupported_var_method_returns_none_not_crash():
    """실패 주입 — `VarPolicy.method`는 평문 `str`이라(Literal 검증 없음)
    설정 오타·손상된 config가 그대로 여기까지 도달할 수 있다. 미지 method는
    조용히 0으로 통과시키거나 예외로 죽지 않고 None(판단 불가)이어야 한다."""
    candles = _make_candles(_oscillating_closes(30))
    policy = _policy(method="not_a_real_method")

    result = estimate_portfolio_var_es({"BTC/USDT": candles}, {"BTC/USDT": Decimal("1")}, policy)

    assert result is None


def test_repeated_multi_symbol_estimation_has_no_performance_regression():
    """성능 단언 — 3종목 250봉 포트폴리오 VaR/ES 계산 300회가 느슨한 상한
    (3000ms, 회귀 감지용 — 절대 성능목표 아님) 안에 끝나야 한다."""
    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    histories = {
        s: _make_candles(_oscillating_closes(250, base=100 + i * 10, amplitude=5 + i), symbol=s)
        for i, s in enumerate(symbols)
    }
    weights = {s: Decimal("1") / Decimal(len(symbols)) for s in symbols}
    policy = _policy(method="historical", min_bars=10)

    started = time.perf_counter()
    for _ in range(300):
        result = estimate_portfolio_var_es(histories, weights, policy)
        assert result is not None
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert elapsed_ms < 3000.0, f"포트폴리오 VaR 계산 300회 지연 회귀 의심: {elapsed_ms:.2f}ms"


def test_none_result_denies_via_var_es_gate_chain():
    """게이트 적색 재현 — 표본 부족으로 `estimate_portfolio_var_es`가 실제로
    반환하는 None이, 그대로 `rules/var_es.py` 게이트에 들어가면 조용히
    ALLOW로 새지 않고 DENY함을 체인 전체로 증명한다(이 리프의 docstring이
    주장하는 fail-closed 계약, R3)."""
    candles = _make_candles(_oscillating_closes(5))  # min_bars(10) 미달.
    policy = load_risk_policy()

    var_result = estimate_portfolio_var_es(
        {"BTC/USDT": candles}, {"BTC/USDT": Decimal("1")}, policy.var
    )
    assert var_result is None

    stats = StatsInputs(as_of=_RISK_NOW, var_pct=None, es_pct=None, var_method=None, bars_used=None)
    decision = var_es_rule(_risk_inputs(stats), policy)

    assert decision.outcome == RiskOutcome.DENY
    assert decision.missing_fields == ("stats.var_pct",)


def test_deterministic_across_independently_constructed_inputs_for_replay():
    """리플레이 증명(D3) — 감사 재생(R2)은 나중에 독립적으로 재구성된 입력
    (별도 리스트/딕셔너리 객체, 같은 논리적 값)으로도 최초 평가와 정확히
    같은 VaR/ES를 재현해야 한다. 객체 동일성이 아니라 값 동일성에 의존하는
    숨은 상태(캐시, 객체 id 기반 분기 등)가 없는지 증명한다."""
    policy = _policy(method="parametric")

    def _fresh_call() -> object:
        closes = _oscillating_closes(40)  # 매번 새 리스트를 만든다.
        candles = _make_candles(list(closes), symbol="BTC/USDT")
        histories = {"BTC/USDT": list(candles)}
        weights = {"BTC/USDT": Decimal("1")}
        return estimate_portfolio_var_es(histories, weights, policy)

    first = _fresh_call()
    second = _fresh_call()

    assert first is not None and second is not None
    assert first is not second
    assert first.var_pct == second.var_pct
    assert first.es_pct == second.es_pct
    assert first.bars_used == second.bars_used
