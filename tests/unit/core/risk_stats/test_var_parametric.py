"""L4_risk_and_safety_v1.0.md#R-19 — var_parametric.py known-value + ES>=VaR."""
import json
from decimal import Decimal
from time import perf_counter

import numpy as np
import pytest

from src.core.eventstore.replay import verify_replay
from src.core.risk_stats.models import VarMethod
from src.core.risk_stats.var_parametric import norm_ppf, parametric_var_es

_R = np.array([-0.02, -0.01, 0.0, 0.01, 0.02])


def test_known_value_95_confidence_horizon_1():
    result = parametric_var_es(_R, confidence=0.95, horizon_days=1, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.02600741939377787, rel=1e-6)
    assert float(result.es_pct) == pytest.approx(0.03261435315261965, rel=1e-6)


def test_known_value_99_confidence_horizon_1():
    result = parametric_var_es(_R, confidence=0.99, horizon_days=1, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.036782789559297764, rel=1e-6)


def test_known_value_95_confidence_horizon_4_days():
    result = parametric_var_es(_R, confidence=0.95, horizon_days=4, bars_per_day=1)
    assert float(result.var_pct) == pytest.approx(0.05201483878755574, rel=1e-6)


def test_es_greater_equal_var_across_confidences():
    for confidence in (0.90, 0.95, 0.975, 0.99, 0.999):
        result = parametric_var_es(_R, confidence=confidence, horizon_days=1, bars_per_day=1)
        assert result.es_pct >= result.var_pct


def test_method_and_bar_counts_recorded():
    result = parametric_var_es(_R, confidence=0.95, horizon_days=1, bars_per_day=1)
    assert result.method == VarMethod.PARAMETRIC
    assert result.bars_used == len(_R)
    assert result.lookback_bars == len(_R)


def test_parametric_var_es_requires_at_least_two_observations():
    with pytest.raises(ValueError):
        parametric_var_es(np.array([0.01]), confidence=0.95, horizon_days=1, bars_per_day=1)


def test_norm_ppf_rejects_out_of_range_probability():
    with pytest.raises(ValueError):
        norm_ppf(0.0)
    with pytest.raises(ValueError):
        norm_ppf(1.0)


def test_var_pct_is_decimal():
    result = parametric_var_es(_R, confidence=0.95, horizon_days=1, bars_per_day=1)
    assert isinstance(result.var_pct, Decimal)


@pytest.mark.parametrize("confidence", [-0.1, 1.1, float("nan"), float("inf")])
def test_adversarial_invalid_confidence_fails_closed(confidence):
    """I-07/I-10: invalid risk inputs must reach the real domain rejection path."""
    with pytest.raises(ValueError):
        parametric_var_es(
            _R, confidence=confidence, horizon_days=1, bars_per_day=1
        )


def test_numeric_failure_is_not_replaced_by_zero_risk(monkeypatch):
    """I-07: a failed numeric dependency cannot manufacture a successful result."""
    def fail(*args, **kwargs):
        raise ArithmeticError("injected numeric failure")

    monkeypatch.setattr(np, "std", fail)
    with pytest.raises(ArithmeticError, match="injected numeric failure"):
        parametric_var_es(
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
    actual = parametric_var_es(**payload).model_dump()
    restored = json.loads(json.dumps(payload))
    replayed = parametric_var_es(**restored).model_dump()
    key = ("risk_stats", "parametric")
    report = verify_replay({key: (replayed, actual)})
    assert report.ok
    assert report.streams_checked == 1
    assert report.mismatches == ()
    assert report == verify_replay({key: (replayed, actual)})

    tampered = dict(actual, bars_used=actual["bars_used"] + 1)
    rejected = verify_replay({key: (replayed, tampered)})
    assert not rejected.ok
    assert len(rejected.mismatches) == 1
    assert rejected.mismatches[0].key == "parametric"
    assert rejected.combined_digest != report.combined_digest


def test_default_250_bar_calculation_p95_under_5ms():
    """ADR-2026-09-09-C D1: component ceiling within the 5ms risk-gate budget.

    This component p95 check does not claim end-to-end gate p99 compliance.
    Default daily lookback=250 (L4 risk R4); setup/warmup are not timed.
    """
    returns = np.random.default_rng(8333).normal(0, 0.01, 250)
    kwargs = {"confidence": 0.95, "horizon_days": 1, "bars_per_day": 1}
    for _ in range(5):
        parametric_var_es(returns, **kwargs)
    durations = []
    for _ in range(100):
        started = perf_counter()
        result = parametric_var_es(returns, **kwargs)
        durations.append(perf_counter() - started)
        assert result.es_pct >= result.var_pct >= 0
        assert result.bars_used == 250
    p95 = float(np.quantile(durations, 0.95))
    assert p95 < 0.005, f"250-bar calculation p95={p95 * 1000:.3f}ms >= 5ms"
