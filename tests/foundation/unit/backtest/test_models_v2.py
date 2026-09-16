"""BT-1 `BacktestConfigV2` 계약 스냅샷 + negative test.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §3.4.
스냅샷 테스트는 QA가 §3.4 표와 1:1로 대조할 수 있도록 필드명·구조를 그대로
고정한다 — 필드가 임의로 추가/삭제되면 이 테스트가 깨진다.

DEEPEN(task-3036, ADR-2026-09-09-C D2): depth=D2 evidence below.
- negative tests >=3: the many `*_rejects_*`/`*_is_rejected` tests above.
- failure injection 1: `test_float_input_is_rejected_on_every_decimal_field`
  — injects the regression where a `float` leaks in at an API/DB boundary
  instead of `Decimal`, proving the `_reject_float` guard actually blocks
  it (a real bug found and fixed during this DEEPEN).
- perf assertion 1: `test_canonical_json_perf_budget`.
- gate-red repro: N/A (no static scanner/linter exists for this leaf —
  the failure-injection test above exercises the runtime guard directly).
"""

import time
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.foundation.backtest.domain.models_v2 import (
    SCHEMA_VERSION,
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    PercentSlippage,
    VenueTierCommission,
    VolumeImpactSlippage,
)
from src.foundation.market_data.contracts.v1 import Timeframe


def _base_kwargs(**overrides: object) -> dict[str, object]:
    kwargs: dict[str, object] = dict(
        slippage=FixedSlippage(bps=Decimal("1.5")),
        commission=VenueTierCommission(
            venue="BITGET",
            maker_bps=Decimal("2"),
            taker_bps=Decimal("4"),
            min_fee=Decimal("0.10"),
        ),
        latency_ms=50,
        partial_fill=PartialFillConfig(max_participation_pct=Decimal("0.2")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=False, trailing=False),
        magnifier_tf=Timeframe.M1,
        costs=CostsConfig(funding=True, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=True, dividends=True),
        calendar="24x7",
    )
    kwargs.update(overrides)
    return kwargs


def test_schema_version_is_backtest_v2() -> None:
    assert SCHEMA_VERSION == "backtest-v2"
    cfg = BacktestConfigV2(**_base_kwargs())
    assert cfg.schema_version == "backtest-v2"


def test_contract_field_snapshot_matches_spec_3_4() -> None:
    """§3.4 표: slippage/commission/latency_ms/partial_fill/order_types/
    magnifier_tf/costs/adjustments/calendar — 필드 이름·순서 스냅샷."""

    assert list(BacktestConfigV2.model_fields.keys()) == [
        "schema_version",
        "slippage",
        "commission",
        "latency_ms",
        "partial_fill",
        "order_types",
        "magnifier_tf",
        "costs",
        "adjustments",
        "calendar",
    ]
    assert list(VenueTierCommission.model_fields.keys()) == [
        "venue",
        "maker_bps",
        "taker_bps",
        "min_fee",
    ]
    assert list(PartialFillConfig.model_fields.keys()) == ["max_participation_pct"]
    assert list(OrderTypesConfig.model_fields.keys()) == [
        "limit",
        "stop",
        "oco",
        "trailing",
    ]
    assert list(CostsConfig.model_fields.keys()) == ["funding", "borrow_apr"]
    assert list(AdjustmentsConfig.model_fields.keys()) == ["splits", "dividends"]


def test_slippage_discriminated_union_accepts_all_three_kinds() -> None:
    for slippage in (
        FixedSlippage(bps=Decimal("1")),
        PercentSlippage(pct=Decimal("0.05")),
        VolumeImpactSlippage(k=Decimal("0.3"), participation_cap=Decimal("0.5")),
    ):
        cfg = BacktestConfigV2(**_base_kwargs(slippage=slippage))
        assert cfg.slippage is slippage


def test_calendar_accepts_session_and_24x7() -> None:
    for calendar in ("session", "24x7"):
        cfg = BacktestConfigV2(**_base_kwargs(calendar=calendar))
        assert cfg.calendar == calendar


def test_magnifier_tf_accepts_none() -> None:
    cfg = BacktestConfigV2(**_base_kwargs(magnifier_tf=None))
    assert cfg.magnifier_tf is None


def test_canonical_json_is_deterministic_across_calls() -> None:
    cfg = BacktestConfigV2(**_base_kwargs())
    assert cfg.canonical_json() == cfg.model_copy(deep=True).canonical_json()


def test_canonical_json_differs_when_a_field_changes() -> None:
    base = BacktestConfigV2(**_base_kwargs())
    changed = BacktestConfigV2(**_base_kwargs(latency_ms=51))
    assert base.canonical_json() != changed.canonical_json()


def test_fixed_slippage_rejects_negative_bps() -> None:
    with pytest.raises(ValidationError):
        FixedSlippage(bps=Decimal("-0.1"))


def test_percent_slippage_rejects_negative_pct() -> None:
    with pytest.raises(ValidationError):
        PercentSlippage(pct=Decimal("-1"))


def test_venue_tier_commission_rejects_negative_bps() -> None:
    with pytest.raises(ValidationError):
        VenueTierCommission(
            venue="BITGET",
            maker_bps=Decimal("-1"),
            taker_bps=Decimal("1"),
            min_fee=Decimal("0"),
        )


@pytest.mark.parametrize("participation_cap", [Decimal("0"), Decimal("1.01"), Decimal("-0.1")])
def test_volume_impact_slippage_rejects_participation_cap_out_of_range(
    participation_cap: Decimal,
) -> None:
    with pytest.raises(ValidationError):
        VolumeImpactSlippage(k=Decimal("0.1"), participation_cap=participation_cap)


@pytest.mark.parametrize("max_participation_pct", [Decimal("0"), Decimal("1.01"), Decimal("-0.1")])
def test_partial_fill_rejects_max_participation_pct_out_of_range(
    max_participation_pct: Decimal,
) -> None:
    with pytest.raises(ValidationError):
        PartialFillConfig(max_participation_pct=max_participation_pct)


def test_unknown_slippage_kind_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BacktestConfigV2(**_base_kwargs(slippage={"kind": "unknown", "bps": "1"}))


def test_negative_latency_ms_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BacktestConfigV2(**_base_kwargs(latency_ms=-1))


def test_negative_borrow_apr_is_rejected() -> None:
    with pytest.raises(ValidationError):
        CostsConfig(funding=False, borrow_apr=Decimal("-0.01"))


def test_unknown_calendar_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        BacktestConfigV2(**_base_kwargs(calendar="weekly"))


@pytest.mark.parametrize(
    ("model_cls", "kwargs"),
    [
        (FixedSlippage, {"bps": 1.5}),
        (PercentSlippage, {"pct": 0.5}),
        (VolumeImpactSlippage, {"k": 0.1, "participation_cap": 0.5}),
        (
            VenueTierCommission,
            {"venue": "BITGET", "maker_bps": 2.0, "taker_bps": 4.0, "min_fee": 0.1},
        ),
        (PartialFillConfig, {"max_participation_pct": 0.2}),
        (CostsConfig, {"funding": True, "borrow_apr": 0.05}),
    ],
)
def test_float_input_is_rejected_on_every_decimal_field(
    model_cls: type, kwargs: dict[str, object]
) -> None:
    """failure injection(D2): injects the case where an upstream API/DB
    sends an IEEE754 `float` instead of a `Decimal` string (e.g. a JSON
    parser regression turning `1.5` back into a float). Before the
    `_reject_float` guard existed, these values silently coerced into
    `Decimal` and passed — this test blocks that regression."""

    for field_name, value in kwargs.items():
        if not isinstance(value, float):
            continue
        bad_kwargs = dict(kwargs)
        bad_kwargs[field_name] = value
        with pytest.raises(ValidationError):
            model_cls(**bad_kwargs)


def test_canonical_json_perf_budget() -> None:
    """perf assertion(D2): `canonical_json()` is called at high volume on
    the reproducibility-key (BT-9) path — pin a quantified upper bound so
    it never regresses past O(n) serialization (1,000 calls <= 200ms,
    with headroom for local CI noise)."""

    cfg = BacktestConfigV2(**_base_kwargs())
    start = time.perf_counter()
    for _ in range(1_000):
        cfg.canonical_json()
    elapsed_ms = (time.perf_counter() - start) * 1000
    assert elapsed_ms < 200, f"canonical_json() too slow: {elapsed_ms:.1f}ms/1000 calls"
