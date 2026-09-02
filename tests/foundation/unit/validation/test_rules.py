"""76번 §1/§3/§6 규칙의 단위테스트 — DB 없이 순수 함수만 검증한다."""
from datetime import datetime, timezone
from decimal import Decimal

from src.data.models.market_data import Candle
from src.foundation.validation.domain.models import Outcome
from src.foundation.validation.domain.rules import (
    compute_input_snapshot_hash,
    compute_result_hash,
    evaluate_validation_policy,
)

NOW = datetime(2026, 9, 2, tzinfo=timezone.utc)


def _bar(close: str = "100") -> Candle:
    return Candle(
        symbol="BTC/USDT",
        exchange="bitget",
        timeframe="1h",
        open_time=NOW,
        close_time=NOW,
        open=Decimal(close),
        high=Decimal(close),
        low=Decimal(close),
        close=Decimal(close),
        volume=Decimal("1"),
    )


def _snapshot_kwargs(**overrides):
    defaults = dict(
        fsm_definition={"states": ["IDLE"]},
        cost_model={"fee_bps": "5", "slippage_bps": "2"},
        warmup_bars=0,
        periods_per_year=252,
        initial_equity=Decimal("10000"),
        bars=[_bar()],
    )
    defaults.update(overrides)
    return defaults


def test_snapshot_hash_is_stable_for_identical_input():
    a = compute_input_snapshot_hash(**_snapshot_kwargs())
    b = compute_input_snapshot_hash(**_snapshot_kwargs())
    assert a == b


def test_snapshot_hash_changes_when_fsm_definition_changes():
    a = compute_input_snapshot_hash(**_snapshot_kwargs())
    b = compute_input_snapshot_hash(**_snapshot_kwargs(fsm_definition={"states": ["OTHER"]}))
    assert a != b


def test_snapshot_hash_changes_when_bars_change():
    a = compute_input_snapshot_hash(**_snapshot_kwargs())
    b = compute_input_snapshot_hash(**_snapshot_kwargs(bars=[_bar("200")]))
    assert a != b


def test_snapshot_hash_changes_when_cost_model_changes():
    a = compute_input_snapshot_hash(**_snapshot_kwargs())
    b = compute_input_snapshot_hash(
        **_snapshot_kwargs(cost_model={"fee_bps": "10", "slippage_bps": "2"})
    )
    assert a != b


def test_result_hash_is_order_independent_over_dict_keys():
    assert compute_result_hash({"a": 1, "b": 2}) == compute_result_hash({"b": 2, "a": 1})


def test_no_warnings_passes_cleanly():
    outcome, obligations = evaluate_validation_policy([])
    assert outcome == Outcome.PASS
    assert obligations == []


def test_warnings_become_explicit_obligations_not_silently_dropped():
    outcome, obligations = evaluate_validation_policy(["zero-cost model used"])
    assert outcome == Outcome.PASS_WITH_OBLIGATIONS
    assert obligations == ["zero-cost model used"]
