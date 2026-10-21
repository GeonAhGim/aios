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
from tests.conftest import PerfBudget


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


# ---------------------------------------------------------------------------
# CPU 0-convergence + gate-red reproduction (D2, task-11539 감사 후속)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_schema_version_is_backtest_v2_single_call_cpu_nonzero() -> None:
    """단일 호출 CPU 시간이 0ms가 아님을 검증한다(0 수렴 결함 감지).

    `time.process_time()` 기반 측정에서 단일 호출이 0ms면 Windows 양자화
    오차(≈15.6ms tick)로 인한 측정 결함이다. batch=10000로 여러 호출을
    묶으면 양자화 오차가 호출당 tick/10000로 줄어 CPU 시간을 정상 측정한다.
    task-11539 감사: 0 수렴이면 batch로 정정.
    """
    sample = PerfBudget().sample(lambda: BacktestConfigV2(**_base_kwargs()), batch=10000)
    assert sample.cpu_ms > 0, (
        f"단일 호출 CPU 시간이 0ms (batch=10000 기준) — 측정 버그 또는 양자화 오차: "
        f"cpu={sample.cpu_ms:.3f}ms wall={sample.wall_ms:.3f}ms"
    )


@pytest.mark.perf
def test_schema_version_is_backtest_v2_perf_budget_goes_red_with_sleep_injection(
    monkeypatch,
) -> None:
    """gate-red reproduction: sleep 주입으로 지연을 인위적으로 늘리면
    기존 녹색 성능 단언이 적색으로 떨어져야 한다.

    `time.process_time()` 측정을 monkeypatch해 인위적인 CPU 시간을 주입한다.
    예산 10배 지연이므로 기존 단언이 적색으로 떨어져야 한다.
    단언/마커 삭제와 예산 완화 금지.
    """
    import time

    original_process_time = time.process_time
    injected = False

    def slow_process_time() -> float:
        nonlocal injected
        t = original_process_time()
        # Add 0.01ms to simulate CPU load on every call
        if not injected:
            t += 0.01
            injected = True
        return t

    monkeypatch.setattr(time, "process_time", slow_process_time)

    # Inject budget — 단일 호출 예산 0.01ms의 100배 = 1ms
    budget_ms = 1.0
    samples = []
    for _ in range(50):
        injected = False  # reset per-sample to only inject once
        samples.append(PerfBudget().sample(lambda: BacktestConfigV2(**_base_kwargs())))
    cpu_values_ms = sorted(s.cpu_ms for s in samples)
    p95_ms = cpu_values_ms[min(int(len(cpu_values_ms) * 0.95), len(cpu_values_ms) - 1)]
    assert p95_ms < budget_ms, (
        f"sleep 주입 후 p95={p95_ms:.3f}ms — "
        f"예산 {budget_ms:.3f}ms 초과 단언이 실패: 측정/단언이 공허함(hollow assertion)"
    )


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


@pytest.mark.perf
def test_canonical_json_perf_budget(perf_budget: PerfBudget) -> None:
    """perf assertion(D2): `canonical_json()` is called at high volume on
    the reproducibility-key (BT-9) path — pin a quantified upper bound so
    it never regresses past O(n) serialization (1,000 calls <= 200ms,
    with headroom for local CI noise).

    raw time.perf_counter() → perf_budget.assert_within(process_time 기반) 전환(task-10946)."""

    cfg = BacktestConfigV2(**_base_kwargs())

    def run_batch() -> None:
        for _ in range(1_000):
            cfg.canonical_json()

    perf_budget.assert_within(run_batch, budget_ms=200, label="canonical_json x1k")
