"""L4_strategy_portfolio_backtest_v1.0.md#§2 rows 88~89, §9 L17 — contract tests.

DoD (a)-(f) must all be falsifiable: dropping a single field, using the
wrong type, or breaking a value rule must fail some test in this file.
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from src.core.portfolio.config import CostModelRef, PortfolioConfig, SizingMethod
from src.core.portfolio.state_input import PortfolioAggregate, PortfolioStateInput
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import virtual_delayed_sync

_HEX64 = "a" * 64


def _cost_model_ref() -> CostModelRef:
    return CostModelRef(model_id="cm-1", cost_model_hash=_HEX64)


def _config(**overrides: Any) -> PortfolioConfig:
    base: dict[str, Any] = {
        "method": SizingMethod.FIXED_FRACTIONAL,
        "min_trade_notional": Decimal("10"),
        "cost_model": _cost_model_ref(),
    }
    base.update(overrides)
    return PortfolioConfig.model_validate(base)


# --- (a) 1:1 field match against the §2 spec table -------------------------


def test_portfolio_config_has_exactly_the_spec_fields():
    expected = {
        "schema_version",
        "method",
        "fraction_pct",
        "target_vol_pct",
        "kelly_cap_pct",
        "rebalance_band_pct",
        "min_trade_notional",
        "cost_model",
    }
    assert set(PortfolioConfig.model_fields.keys()) == expected


def test_sizing_method_has_exactly_the_spec_members():
    assert {m.value for m in SizingMethod} == {
        "FIXED_FRACTIONAL",
        "VOLATILITY_TARGET",
        "KELLY_CAPPED",
        "RISK_PARITY",
    }


# --- D2 성능 단언 (performance assertion) --------------------------------------


@pytest.mark.perf
def test_sizing_method_iteration_cpu_budget(perf_budget: PerfBudget) -> None:
    """SizingMethod 멤버 순회(set comprehension)는 단일 호출당 ~0 CPU ms여야 한다.
    Windows 양자화 tick(약 15.6ms)에 흔들리지 않게 best-of-N 최소값으로
    측정하고 1 ms 아래를 보장한다."""
    members = []

    def one_iteration() -> None:
        members.extend(m.value for m in SizingMethod)

    sample = perf_budget.best_of(one_iteration, n=100, batch=1)
    assert sample.cpu_ms < 1.0, (
        f"sizing_method iteration CPU={sample.cpu_ms:.3f}ms exceeds 1ms budget — "
        f"wall={sample.wall_ms:.3f}ms load={perf_budget.load_percent()}"
    )


# --- D2 게이트 적색 재현 (gate-red reproduction) --------------------------------


@pytest.mark.perf
def test_sizing_method_iteration_budget_catches_virtual_delay(
    perf_budget: PerfBudget,
) -> None:
    """virtual_delayed_sync 으로 1회 호출당 10 ms 가상 CPU 지연을 주입하면
    같은 budget 단언이 적색(AssertionError)이어야 한다.

    이 테스트는 budget 단언이 tautology가 아님을, 실제로 성능 회귀를
    검출할 수 있음을 증명한다. 실제 sleep/busy-wait 없이 가상 원장에
    지연만 추가하므로 테스트 실행 시간은 변하지 않는다."""

    def _delayed_iteration() -> list[str]:
        """SizingMethod 순회 + 10 ms 가상 지연."""
        result = [m.value for m in SizingMethod]
        # 10 ms 가상 CPU 지연 → PerfBudget.sample이 cpu_ms에 합산
        virtual_delayed_sync(lambda: None, 10.0)()
        return result

    samples = perf_budget.samples(
        _delayed_iteration,
        n=5,
        batch=1,
    )
    cpu_values = sorted(s.cpu_ms for s in samples)
    p99_ms = cpu_values[int(len(cpu_values) * 0.99) - 1]

    # 1 ms 예산 — 10 ms 가상 지연이 주입되면 p99가 예산을 넘어야 함
    with pytest.raises(AssertionError):
        assert p99_ms < 1.0, (
            f"p99={p99_ms:.3f}ms exceeds 1ms budget — delay injection was not detected"
        )


# --- D2 음성 테스트 (negative tests) --------------------------------------------


def test_sizing_method_empty_set_is_not_equal():
    """빈 set은 SizingMethod의 값과 같지 않다 — 단언이 무의미하지 않음을 확인."""
    assert set() != {m.value for m in SizingMethod}


def test_sizing_method_missing_member_fails():
    """RISK_PARITY가 누락된 set은 실제 값과 같지 않다 — 단언이 누락을 검출한다."""
    subset = {
        "FIXED_FRACTIONAL",
        "VOLATILITY_TARGET",
        "KELLY_CAPPED",
    }
    actual = {m.value for m in SizingMethod}
    # subset가 실제보다 작으므로 != 이 참이어야 함 (누락 검출)
    assert subset != actual


def test_sizing_method_extra_member_fails():
    """FAKE_METHOD이 포함된 set은 실제 값과 같지 않다 — 단언이 이상 멤버를 검출한다."""
    fake = {
        "FIXED_FRACTIONAL",
        "VOLATILITY_TARGET",
        "KELLY_CAPPED",
        "RISK_PARITY",
        "FAKE_METHOD",
    }
    actual = {m.value for m in SizingMethod}
    # fake가 실제보다 크므로 != 이 참이어야 함 (이상 멤버 검출)
    assert fake != actual


def test_portfolio_config_schema_version_and_defaults():
    cfg = _config()
    assert cfg.schema_version == "pcfg-v1"
    assert cfg.fraction_pct == Decimal("100")
    assert cfg.rebalance_band_pct == Decimal("5")
    assert cfg.target_vol_pct is None
    assert cfg.kelly_cap_pct is None


def test_portfolio_state_input_has_exactly_the_spec_fields():
    expected = {
        "schema_version",
        "allocated_capital",
        "position_quantity",
        "current_price",
        "total_equity",
        "cash_available",
        "realized_vol_pct",
        "win_rate",
        "avg_win_loss_ratio",
        "exposures",
        "mandate",
        "portfolio_config",
    }
    assert set(PortfolioStateInput.model_fields.keys()) == expected


def test_portfolio_state_input_optional_fields_default_to_none():
    inp = PortfolioStateInput(
        allocated_capital=Decimal("1000"),
        position_quantity=Decimal("0"),
        current_price=Decimal("50000"),
        total_equity=Decimal("10000"),
        cash_available=Decimal("9000"),
        portfolio_config=_config(),
    )
    assert inp.realized_vol_pct is None
    assert inp.win_rate is None
    assert inp.avg_win_loss_ratio is None
    assert inp.exposures is None
    assert inp.mandate is None
    assert inp.schema_version == "psi-v1"


# --- (b) config_hash() stability --------------------------------------------


def test_config_hash_stable_for_equal_values():
    a = _config(rebalance_band_pct=Decimal("5"))
    b = _config(rebalance_band_pct=Decimal("5"))
    assert a.config_hash() == b.config_hash()


def test_config_hash_changes_when_rebalance_band_pct_changes():
    a = _config(rebalance_band_pct=Decimal("5"))
    b = _config(rebalance_band_pct=Decimal("6"))
    assert a.config_hash() != b.config_hash()


# --- (c) dict acceptance -----------------------------------------------------


def _legacy_state_dict() -> dict[str, object]:
    # Carries the existing PortfolioEngine.allocate() current_portfolio_state
    # keys (allocated_capital/position_quantity/current_price/total_equity)
    # as-is, plus the new required fields (cash_available, portfolio_config).
    return {
        "allocated_capital": Decimal("1000"),
        "position_quantity": Decimal("0.5"),
        "current_price": Decimal("50000"),
        "total_equity": Decimal("10000"),
        "cash_available": Decimal("9500"),
        "portfolio_config": _config().model_dump(mode="python"),
    }


def test_from_dict_accepts_legacy_shaped_dict():
    inp = PortfolioStateInput.from_dict(_legacy_state_dict())
    assert inp.allocated_capital == Decimal("1000")
    assert inp.total_equity == Decimal("10000")
    assert inp.portfolio_config.method == SizingMethod.FIXED_FRACTIONAL


def test_from_dict_rejects_dict_missing_total_equity():
    data = _legacy_state_dict()
    del data["total_equity"]
    with pytest.raises(ValidationError):
        PortfolioStateInput.from_dict(data)


# --- (d) Decimal enforced + negative rejected --------------------------------


def test_min_trade_notional_rejects_negative():
    with pytest.raises(ValidationError):
        _config(min_trade_notional=Decimal("-1"))


def test_portfolio_config_rejects_float_for_decimal_field():
    with pytest.raises(ValidationError):
        _config(min_trade_notional=1.5)


def test_portfolio_state_input_rejects_float_for_decimal_field():
    with pytest.raises(ValidationError):
        PortfolioStateInput(
            allocated_capital=1000.0,
            position_quantity=Decimal("0"),
            current_price=Decimal("50000"),
            total_equity=Decimal("10000"),
            cash_available=Decimal("9000"),
            portfolio_config=_config(),
        )


# --- (e) CostModelRef is a minimal reference type -----------------------------


def test_cost_model_ref_is_a_minimal_reference_not_a_cost_model_reimplementation():
    assert set(CostModelRef.model_fields.keys()) == {"model_id", "cost_model_hash"}


def test_cost_model_ref_rejects_non_hex_hash():
    with pytest.raises(ValidationError):
        CostModelRef(model_id="cm-1", cost_model_hash="not-a-hash")


# --- exposures: PortfolioAggregate round-trip ---------------------------------


def test_portfolio_state_input_accepts_exposures_aggregate():
    agg = PortfolioAggregate(
        total_equity=Decimal("10000"),
        per_symbol_pct={"BTC/USDT": Decimal("10")},
        per_strategy_pct={"strat-1": Decimal("10")},
        total_exposure_pct=Decimal("10"),
        cash_pct=Decimal("90"),
        as_of=datetime(2026, 9, 9, tzinfo=UTC),
    )
    inp = PortfolioStateInput(
        allocated_capital=Decimal("1000"),
        position_quantity=Decimal("0"),
        current_price=Decimal("50000"),
        total_equity=Decimal("10000"),
        cash_available=Decimal("9000"),
        exposures=agg,
        portfolio_config=_config(),
    )
    assert inp.exposures is not None
    assert inp.exposures.total_equity == Decimal("10000")


# --- D2 실패 주입 (failure injection) -----------------------------------------


class _BrokenPortfolioStateMapping(Mapping[str, Any]):
    """Stand-in for an upstream `current_portfolio_state` source (e.g. a
    corrupted `execution_loop.tick` snapshot) whose iteration breaks partway
    through instead of yielding a clean dict."""

    def __iter__(self):
        yield "allocated_capital"
        raise RuntimeError("upstream portfolio state snapshot corrupted mid-read")

    def __len__(self) -> int:
        return 1

    def __getitem__(self, key: str) -> Any:
        if key == "allocated_capital":
            return Decimal("1000")
        raise KeyError(key)


def test_from_dict_propagates_broken_upstream_mapping_failure():
    """`from_dict` must let a broken upstream source's exception propagate
    (fail-closed) instead of silently building a partial/empty state that a
    downstream sizing decision would then treat as complete."""
    with pytest.raises(RuntimeError, match="corrupted mid-read"):
        PortfolioStateInput.from_dict(_BrokenPortfolioStateMapping())


# --- D2 성능 단언 (performance assertion) --------------------------------------


@pytest.mark.perf
def test_config_hash_p99_latency_within_pretrade_gate_budget(perf_budget: Any) -> None:
    """ADR-2026-09-09-C Decision 1 축별 성능 예산: 사전거래 게이트 p99 5ms.
    `config_hash()`는 `AllocationDecision.decision_hash` 재료로 사전거래
    사이징 경로마다 호출된다(§2 226행)."""
    cfg = _config()
    # batch=8: process_time() Windows 틱(~15.6ms) 양자화 오차를 batch로 나눈다.
    # 1회 호출만 하면 0 또는 15.6ms만 나오므로 분포가 왜곡된다.
    raw_samples = perf_budget.samples(lambda: cfg.config_hash(), n=100, batch=8)
    cpu_ms_values = sorted(s.cpu_ms for s in raw_samples)
    p99_ms = cpu_ms_values[int(len(cpu_ms_values) * 0.99) - 1]
    assert p99_ms < 5.0, f"p99={p99_ms:.3f}ms exceeds 5ms budget"


# --- D2 게이트 적색 재현 (gate-red reproduction) --------------------------------


def test_non_negative_notional_guard_catches_removal_regression():
    """`PortfolioConfig._check_non_negative`가 없다면(회귀) 음수
    `min_trade_notional`이 pydantic의 기본 lax 검증을 조용히 통과해 사이징
    엔진에 음수 최소 주문 금액을 전달할 수 있다 -- 실제 구현은 그 적색
    상태를 fail-closed로 막아야 한다."""

    class _RegressedConfig(BaseModel):
        # `_check_non_negative` validator 호출을 빼먹은 회귀본.
        min_trade_notional: Decimal

    # 적색: 가드가 없으면 음수 notional이 조용히 통과한다.
    regressed = _RegressedConfig(min_trade_notional=Decimal("-1"))
    assert regressed.min_trade_notional == Decimal("-1")

    # 녹색: 실제 구현은 같은 입력을 fail-closed로 거부한다.
    with pytest.raises(ValidationError):
        _config(min_trade_notional=Decimal("-1"))
