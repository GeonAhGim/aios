"""LatencyModel 단위테스트 — L4-22. 절대 지연(벽시계) 단언 없음 — sleeper 대역."""
from __future__ import annotations

import pytest

from src.exchanges.paper.latency_model import LatencyModel
from tests.unit.exchanges.paper.helpers import FixedRng


class RecordingSleeper:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _model(drop: float = 0.0) -> LatencyModel:
    return LatencyModel(ack_ms_p50=50.0, ack_ms_p99=500.0, drop_response_prob=drop)


def test_quantile_anchors() -> None:
    m = _model()
    assert m.sample(FixedRng([0.0])).delay_ms == 0.0
    assert m.sample(FixedRng([0.5])).delay_ms == 50.0
    assert m.sample(FixedRng([0.99])).delay_ms == pytest.approx(500.0)
    assert m.sample(FixedRng([0.995])).delay_ms == pytest.approx(750.0)


def test_delay_is_monotonic_in_u() -> None:
    m = _model()
    samples = [m.sample(FixedRng([u / 100])).delay_ms for u in range(100)]
    assert samples == sorted(samples)


def test_drop_prob_zero_never_drops_and_consumes_one_draw() -> None:
    rng = FixedRng([0.3])
    assert _model(0.0).sample(rng).kind == "ACK"
    assert rng.calls == 1


def test_drop_prob_one_always_drops_but_still_delays() -> None:
    out = _model(1.0).sample(FixedRng([0.5, 0.999]))
    assert out.kind == "DROP"
    assert out.delay_ms == 50.0


def test_drop_threshold_boundary() -> None:
    m = _model(0.25)
    assert m.sample(FixedRng([0.5, 0.2499])).kind == "DROP"
    assert m.sample(FixedRng([0.5, 0.25])).kind == "ACK"


async def test_apply_uses_injected_sleeper_only() -> None:
    sleeper = RecordingSleeper()
    out = await _model(0.0).apply(FixedRng([0.5]), sleeper=sleeper)
    assert out.kind == "ACK"
    assert sleeper.calls == [0.05]


async def test_apply_returns_drop_after_sleeping() -> None:
    sleeper = RecordingSleeper()
    out = await _model(1.0).apply(FixedRng([0.0, 0.0]), sleeper=sleeper)
    assert out.kind == "DROP"
    assert sleeper.calls == [0.0]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ack_ms_p50": -1.0},
        {"ack_ms_p99": 10.0},  # < p50
        {"drop_response_prob": 1.01},
        {"drop_response_prob": -0.01},
    ],
)
def test_constructor_rejects_out_of_range(kwargs: dict[str, float]) -> None:
    base = {"ack_ms_p50": 50.0, "ack_ms_p99": 500.0, "drop_response_prob": 0.0}
    base.update(kwargs)
    with pytest.raises(ValueError):
        LatencyModel(**base)
