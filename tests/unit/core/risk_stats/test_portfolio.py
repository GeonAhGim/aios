"""L4_risk_and_safety_v1.0.md#R-20 — portfolio.py 포트폴리오 VaR ≤ Σ(개별 가중 VaR)."""

import json
from decimal import Decimal
from time import perf_counter

import numpy as np
import pytest

from src.core.eventstore.replay import verify_replay
from src.core.risk_stats.models import VarMethod
from src.core.risk_stats.portfolio import portfolio_returns, portfolio_var_es
from src.core.risk_stats.var_parametric import parametric_var_es

_R = np.array(
    [
        [0.01, 0.02],
        [-0.02, -0.01],
        [0.03, 0.00],
        [-0.01, 0.02],
        [0.02, -0.03],
        [0.00, 0.01],
        [-0.03, 0.02],
        [0.01, -0.01],
    ]
)
_W = [Decimal("0.5"), Decimal("0.5")]


def test_portfolio_returns_matches_matrix_product():
    result = portfolio_returns(_R, _W)
    expected = _R @ np.array([0.5, 0.5])
    assert result == pytest.approx(expected)


def test_portfolio_var_less_equal_sum_of_weighted_individual_vars():
    portfolio = portfolio_var_es(
        VarMethod.PARAMETRIC, _R, _W, confidence=0.95, horizon_days=1, bars_per_day=1
    )
    var1 = parametric_var_es(_R[:, 0], confidence=0.95, horizon_days=1, bars_per_day=1)
    var2 = parametric_var_es(_R[:, 1], confidence=0.95, horizon_days=1, bars_per_day=1)
    weighted_sum = Decimal("0.5") * var1.var_pct + Decimal("0.5") * var2.var_pct
    assert portfolio.var_pct <= weighted_sum


def test_portfolio_var_es_known_value_parametric():
    result = portfolio_var_es(
        VarMethod.PARAMETRIC, _R, _W, confidence=0.95, horizon_days=1, bars_per_day=1
    )
    assert float(result.var_pct) == pytest.approx(0.016990345019945514, rel=1e-6)
    assert result.method == VarMethod.PARAMETRIC
    assert result.bars_used == _R.shape[0]


def test_portfolio_var_es_historical_uses_portfolio_return_series():
    result = portfolio_var_es(
        VarMethod.HISTORICAL, _R, _W, confidence=0.8, horizon_days=1, bars_per_day=1
    )
    assert result.method == VarMethod.HISTORICAL
    assert result.lookback_bars == _R.shape[0]
    assert result.es_pct >= result.var_pct


def test_portfolio_var_es_unsupported_method_raises():
    with pytest.raises(ValueError):
        portfolio_var_es(
            VarMethod.CORNISH_FISHER, _R, _W, confidence=0.95, horizon_days=1, bars_per_day=1
        )


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), float("inf")])
@pytest.mark.parametrize("method", [VarMethod.PARAMETRIC, VarMethod.HISTORICAL])
def test_adversarial_invalid_confidence_fails_closed(method, confidence):
    """I-07/I-10: invalid risk inputs must reach the real domain rejection path."""
    with pytest.raises(ValueError):
        portfolio_var_es(method, _R, _W, confidence=confidence, horizon_days=1, bars_per_day=1)


def test_numeric_failure_is_not_replaced_by_zero_risk_parametric(monkeypatch):
    """I-07: a failed numeric dependency cannot manufacture a successful result."""

    def fail(*args, **kwargs):
        raise ArithmeticError("injected numeric failure")

    monkeypatch.setattr(np, "cov", fail)
    with pytest.raises(ArithmeticError, match="injected numeric failure"):
        portfolio_var_es(
            VarMethod.PARAMETRIC, _R, _W, confidence=0.95, horizon_days=1, bars_per_day=1
        )


def test_numeric_failure_is_not_replaced_by_zero_risk_historical(monkeypatch):
    """I-07: a failed numeric dependency cannot manufacture a successful result."""

    def fail(*args, **kwargs):
        raise ArithmeticError("injected numeric failure")

    monkeypatch.setattr(np, "quantile", fail)
    with pytest.raises(ArithmeticError, match="injected numeric failure"):
        portfolio_var_es(
            VarMethod.HISTORICAL, _R, _W, confidence=0.95, horizon_days=1, bars_per_day=1
        )


@pytest.mark.parametrize("method", [VarMethod.PARAMETRIC, VarMethod.HISTORICAL])
def test_replay_verify_serialized_inputs_and_tampered_result(method):
    """I-05/I-10: replay uses production calculation and FA-15's verifier.

    The DB replay adapter covers orders/ledger, not pure VaR calculations;
    use its actual verify_replay core on all result fields after input reload.
    Tampering is the red-gate reproduction: the verifier must return not ok.
    """
    payload = {
        "R": _R.tolist(),
        "w": [str(x) for x in _W],
        "confidence": 0.95,
        "horizon_days": 2,
        "bars_per_day": 1,
    }

    def _call(raw):
        return portfolio_var_es(
            method,
            np.array(raw["R"]),
            [Decimal(x) for x in raw["w"]],
            confidence=raw["confidence"],
            horizon_days=raw["horizon_days"],
            bars_per_day=raw["bars_per_day"],
        ).model_dump()

    actual = _call(payload)
    restored = json.loads(json.dumps(payload))
    replayed = _call(restored)
    key = ("risk_stats", "portfolio")
    report = verify_replay({key: (replayed, actual)})
    assert report.ok
    assert report.streams_checked == 1
    assert report.mismatches == ()
    assert report == verify_replay({key: (replayed, actual)})

    tampered = dict(actual, bars_used=actual["bars_used"] + 1)
    rejected = verify_replay({key: (replayed, tampered)})
    assert not rejected.ok
    assert len(rejected.mismatches) == 1
    assert rejected.mismatches[0].key == "portfolio"
    assert rejected.combined_digest != report.combined_digest


@pytest.mark.perf
def test_default_250_bar_two_asset_calculation_p95_under_5ms():
    """ADR-2026-09-09-C D1: component ceiling within the 5ms risk-gate budget.

    This component p95 check does not claim end-to-end gate p99 compliance.
    Default daily lookback=250 (L4 risk R4); setup/warmup are not timed.
    """
    rng = np.random.default_rng(8333)
    returns = rng.normal(0, 0.01, (250, 2))
    kwargs = {"confidence": 0.95, "horizon_days": 1, "bars_per_day": 1}
    for _ in range(5):
        portfolio_var_es(VarMethod.PARAMETRIC, returns, _W, **kwargs)
    durations = []
    for _ in range(100):
        started = perf_counter()
        result = portfolio_var_es(VarMethod.PARAMETRIC, returns, _W, **kwargs)
        durations.append(perf_counter() - started)
        assert result.es_pct >= result.var_pct >= 0
        assert result.bars_used == 250
    p95 = float(np.quantile(durations, 0.95))
    assert p95 < 0.005, f"250-bar 2-asset calculation p95={p95 * 1000:.3f}ms >= 5ms"
