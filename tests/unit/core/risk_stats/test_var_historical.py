"""L4_risk_and_safety_v1.0.md#R-19 — var_historical.py known-value + ES>=VaR + h>1 겹침합산."""

import json
from time import perf_counter

import numpy as np
import pytest

from src.core.eventstore.replay import verify_replay
from src.core.risk_stats.models import VarMethod
from src.core.risk_stats.var_historical import historical_var_es

_R = np.array([-0.05, -0.03, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06])


def test_known_value_90_confidence_horizon_1():
    result = historical_var_es(_R, confidence=0.9, horizon_days=1, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.032, rel=1e-9)
    assert float(result.es_pct) == pytest.approx(0.05, rel=1e-9)


def test_known_value_80_confidence_horizon_1():
    result = historical_var_es(_R, confidence=0.8, horizon_days=1, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.014, rel=1e-9)
    assert float(result.es_pct) == pytest.approx(0.04, rel=1e-9)


def test_known_value_horizon_2_bars_uses_overlapping_sums():
    # bars_per_day=1, horizon_days=2 -> h_bars=2, 겹침 합산 수익률 9개(=10-2+1)
    result = historical_var_es(_R, confidence=0.9, horizon_days=2, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.048, rel=1e-9)
    assert float(result.es_pct) == pytest.approx(0.08, rel=1e-9)
    assert result.bars_used == 9
    assert result.lookback_bars == 10


def test_es_greater_equal_var_across_confidences():
    for confidence in (0.5, 0.8, 0.9, 0.95):
        result = historical_var_es(_R, confidence=confidence, horizon_days=1, bars_per_day=1)
        assert result.es_pct >= result.var_pct


def test_method_recorded():
    result = historical_var_es(_R, confidence=0.9, horizon_days=1, bars_per_day=1)
    assert result.method == VarMethod.HISTORICAL


def test_insufficient_observations_for_horizon_raises():
    with pytest.raises(ValueError):
        historical_var_es(np.array([0.01, 0.02]), confidence=0.9, horizon_days=5, bars_per_day=1)


def test_empty_observations_raises():
    with pytest.raises(ValueError):
        historical_var_es(np.array([]), confidence=0.9, horizon_days=1, bars_per_day=1)


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), float("inf")])
def test_adversarial_invalid_confidence_fails_closed(confidence):
    """I-07/I-10: invalid risk inputs must reach the real domain rejection path."""
    with pytest.raises(ValueError):
        historical_var_es(
            _R, confidence=confidence, horizon_days=1, bars_per_day=1
        )


def test_numeric_failure_is_not_replaced_by_zero_risk(monkeypatch):
    """I-07: a failed numeric dependency cannot manufacture a successful result."""
    def fail(*args, **kwargs):
        raise ArithmeticError("injected numeric failure")

    monkeypatch.setattr(np, "quantile", fail)
    with pytest.raises(ArithmeticError, match="injected numeric failure"):
        historical_var_es(
            _R, confidence=0.95, horizon_days=1, bars_per_day=1
        )


def test_replay_verify_serialized_inputs_and_tampered_result():
    """I-05/I-10: replay uses production calculation and FA-15's verifier.

    The DB replay adapter covers orders/ledger, not pure VaR calculations;
    use its actual verify_replay core on all result fields after input reload.
    Tampering is the red-gate reproduction: the verifier must return not ok.
    """
    payload = {"r": _R.tolist(), "confidence": 0.95,
               "horizon_days": 2, "bars_per_day": 1}
    actual = historical_var_es(**payload).model_dump()
    restored = json.loads(json.dumps(payload))
    replayed = historical_var_es(**restored).model_dump()
    key = ("risk_stats", "historical")
    report = verify_replay({key: (replayed, actual)})
    assert report.ok
    assert report.streams_checked == 1
    assert report.mismatches == ()
    assert report == verify_replay({key: (replayed, actual)})

    tampered = dict(actual, bars_used=actual["bars_used"] + 1)
    rejected = verify_replay({key: (replayed, tampered)})
    assert not rejected.ok
    assert len(rejected.mismatches) == 1
    assert rejected.mismatches[0].key == "historical"
    assert rejected.combined_digest != report.combined_digest


def test_default_250_bar_calculation_p95_under_5ms():
    """ADR-2026-09-09-C D1: component ceiling within the 5ms risk-gate budget.

    This component p95 check does not claim end-to-end gate p99 compliance.
    Default daily lookback=250 (L4 risk R4); setup/warmup are not timed.
    """
    returns = np.random.default_rng(8333).normal(0, 0.01, 250)
    kwargs = {"confidence": 0.95, "horizon_days": 1, "bars_per_day": 1}
    for _ in range(5):
        historical_var_es(returns, **kwargs)
    durations = []
    for _ in range(100):
        started = perf_counter()
        result = historical_var_es(returns, **kwargs)
        durations.append(perf_counter() - started)
        assert result.es_pct >= result.var_pct >= 0
        assert result.bars_used == 250
    p95 = float(np.quantile(durations, 0.95))
    assert p95 < 0.005, f"250-bar calculation p95={p95 * 1000:.3f}ms >= 5ms"
