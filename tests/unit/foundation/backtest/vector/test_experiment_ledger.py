"""BT-16b — `backtest/vector/experiment_ledger.py` + `grid.sweep_grid_and_record` tests.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 BT-16
(실험 원장 기록, task-2426 DoD).

- (a)/(e) 순수성은 zone manifest + import 자체로 검증(이 모듈은 I/O를 하지
  않는다 — 별도 fixture 없이 순수 함수만 호출).
- (c) 결정론: 같은 설정·시드로 두 번 실행하면 재현 키가 바이트 동일, 시드
  하나만 바꾸면 달라진다(단언 2건: `test_..._same_inputs_are_byte_identical`,
  `test_..._seed_alone_changes_the_key`).
- negative: `ExperimentLedgerEntry`의 fail-closed 검증(빈 combo_key·음수
  combo_index) + `sweep_grid_and_record`의 script_hashes/combos 키 불일치 거부.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

from src.foundation.backtest.domain.models import (
    BacktestMetrics,
    BacktestMetricsBasis,
)
from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.backtest.ports.experiment_ledger import ExperimentLedgerPort
from src.foundation.backtest.vector.experiment_ledger import (
    ExperimentLedgerEntry,
    record_grid_entry,
)
from src.foundation.backtest.vector.fills import VectorSignal
from src.foundation.backtest.vector.grid import (
    GridSweepResult,
    sweep_grid_and_record,
)
from src.foundation.backtest.vector.signals import BoolSignal
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
_D = Decimal
_CASH = _D("100000")


def _config() -> BacktestConfigV2:
    return BacktestConfigV2(
        slippage=FixedSlippage(bps=_D("10")),
        commission=VenueTierCommission(
            venue="BITGET", maker_bps=_D("2"), taker_bps=_D("5"), min_fee=_D("0")
        ),
        latency_ms=0,
        partial_fill=PartialFillConfig(max_participation_pct=_D("1")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=True, trailing=True),
        magnifier_tf=None,
        costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False),
        calendar="24x7",
    )


def _rising_columns(n: int) -> CandleColumns:
    closes = [_D(str(100 + i)) for i in range(n)]
    opens = [closes[i - 1] if i else closes[0] for i in range(n)]
    return CandleColumns(
        ts=[_T0 + timedelta(minutes=1) * i for i in range(n)],
        open=opens,
        high=[max(o, c) + 1 for o, c in zip(opens, closes, strict=True)],
        low=[min(o, c) - 1 for o, c in zip(opens, closes, strict=True)],
        close=closes,
        volume=[_D("1000")] * n,
        quote_volume=[None] * n,
    )


def _buy_and_hold_signal(n: int) -> VectorSignal:
    always = BoolSignal(
        values=np.ones(n, dtype=np.bool_),
        na=np.zeros(n, dtype=np.bool_),
    )
    never = BoolSignal(
        values=np.zeros(n, dtype=np.bool_),
        na=np.zeros(n, dtype=np.bool_),
    )
    return VectorSignal(entries=always, exits=never, quantity=_D("1"))


def _metrics() -> BacktestMetrics:
    return BacktestMetrics(
        period_start=_T0,
        period_end=_T0 + timedelta(days=30),
        total_return_pct=_D("0.05"),
        max_drawdown_pct=_D("0.01"),
        sharpe_ratio=_D("1.5"),
        sortino_ratio=_D("2.0"),
        win_rate_pct=_D("55.0"),
        total_trades=10,
        turnover=_D("50000"),
        gross_return_pct=_D("0.06"),
        net_return_pct=_D("0.05"),
        total_fees=_D("100"),
        total_slippage=_D("50"),
        total_funding=_D("0"),
        calmar_ratio=_D("5.0"),
        exposure_time_pct=_D("100.0"),
        annualization=365,
        basis=BacktestMetricsBasis(
            base_asset="USDT",
            quote_asset="USDT",
            contract_type="perpetual",
            venue="BITGET",
        ),
    )


# ================= ExperimentLedgerEntry (fail-closed) =================


def test_entry_rejects_blank_combo_key() -> None:
    with pytest.raises(ValueError, match="combo_key"):
        ExperimentLedgerEntry(combo_key="  ", combo_index=0, seed=1, reproducibility_key="k")


def test_entry_rejects_negative_combo_index() -> None:
    with pytest.raises(ValueError, match="combo_index"):
        ExperimentLedgerEntry(combo_key="c0", combo_index=-1, seed=1, reproducibility_key="k")


# ================= record_grid_entry determinism (BT-9 delegation) =================


def test_record_grid_entry_same_inputs_are_byte_identical() -> None:
    config = _config()
    kwargs = dict(
        combo_key="rsi_len=14",
        combo_index=0,
        script_hash="script-abc",
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        config=config,
        seed=42,
    )
    a = record_grid_entry(**kwargs)
    b = record_grid_entry(**kwargs)
    assert a.reproducibility_key == b.reproducibility_key
    assert len(a.reproducibility_key) == 64  # sha256 hex


def test_record_grid_entry_seed_alone_changes_the_key() -> None:
    config = _config()
    base = dict(
        combo_key="rsi_len=14",
        combo_index=0,
        script_hash="script-abc",
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        config=config,
    )
    a = record_grid_entry(seed=42, **base)
    b = record_grid_entry(seed=43, **base)
    assert a.reproducibility_key != b.reproducibility_key


def test_record_grid_entry_delegates_to_bt9_reproducibility_key() -> None:
    """BT-9의 순수 계산 결과와 바이트 동일해야 한다 -- 이 모듈이 자체 해시를
    새로 정의하지 않았음을 직접 증명한다(task-2426 DoD(b))."""
    from src.foundation.backtest.domain.reproducibility import reproducibility_key

    config = _config()
    entry = record_grid_entry(
        combo_key="c0",
        combo_index=0,
        script_hash="script-abc",
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        config=config,
        seed=7,
    )
    expected = reproducibility_key(
        script_hash="script-abc:seed=7",
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        config=config,
    )
    assert entry.reproducibility_key == expected


# ================= sweep_grid_and_record =================


def test_sweep_grid_and_record_rejects_script_hash_key_mismatch() -> None:
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n), "c1": _buy_and_hold_signal(n)}
    with pytest.raises(ValueError, match="script_hashes"):
        sweep_grid_and_record(
            cols,
            combos,
            _config(),
            timeframe=Timeframe.M1,
            initial_cash=_CASH,
            script_hashes={"c0": "hash-0"},  # missing c1
            data_lineage_hash="lineage-xyz",
            rollup_version="rollup-1",
            seed=1,
        )


def test_sweep_grid_and_record_produces_one_entry_per_combo_in_order() -> None:
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n), "c1": _buy_and_hold_signal(n)}
    script_hashes = {"c0": "hash-0", "c1": "hash-1"}

    result = sweep_grid_and_record(
        cols,
        combos,
        _config(),
        timeframe=Timeframe.M1,
        initial_cash=_CASH,
        script_hashes=script_hashes,
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        seed=1,
    )

    assert len(result.entries) == 2
    assert result.entries[0].combo_key == "c0"
    assert result.entries[1].combo_key == "c1"
    assert result.metrics[0].total_return_pct > _D("0")
    assert result.metrics[1].total_return_pct > _D("0")


def test_sweep_grid_and_record_deterministic_with_same_seed() -> None:
    """동일 시드 → 동일한 entries + metrics (BT-16b 결정론 요구)."""
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n)}
    script_hashes = {"c0": "hash-0"}

    r1 = sweep_grid_and_record(
        cols,
        combos,
        _config(),
        timeframe=Timeframe.M1,
        initial_cash=_CASH,
        script_hashes=script_hashes,
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        seed=42,
    )
    r2 = sweep_grid_and_record(
        cols,
        combos,
        _config(),
        timeframe=Timeframe.M1,
        initial_cash=_CASH,
        script_hashes=script_hashes,
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        seed=42,
    )
    assert r1.entries[0].reproducibility_key == r2.entries[0].reproducibility_key
    assert r1.metrics[0].total_return_pct == r2.metrics[0].total_return_pct


def test_sweep_grid_and_record_seed_affects_metrics() -> None:
    """시드가 다르면 결과도 달라야 한다."""
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n)}
    script_hashes = {"c0": "hash-0"}

    r1 = sweep_grid_and_record(
        cols,
        combos,
        _config(),
        timeframe=Timeframe.M1,
        initial_cash=_CASH,
        script_hashes=script_hashes,
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        seed=42,
    )
    r2 = sweep_grid_and_record(
        cols,
        combos,
        _config(),
        timeframe=Timeframe.M1,
        initial_cash=_CASH,
        script_hashes=script_hashes,
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        seed=99,
    )
    # 시드가 다르면 reproducibility_key가 달라져야 함
    assert r1.entries[0].reproducibility_key != r2.entries[0].reproducibility_key


def test_sweep_grid_and_record_fills_entry_with_metrics() -> None:
    """sweep_grid_and_record가 각 entry의 metrics를 채운다."""
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n)}
    script_hashes = {"c0": "hash-0"}

    result = sweep_grid_and_record(
        cols,
        combos,
        _config(),
        timeframe=Timeframe.M1,
        initial_cash=_CASH,
        script_hashes=script_hashes,
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        seed=1,
    )
    entry = result.entries[0]
    assert entry.metrics is not None
    assert isinstance(entry.metrics, BacktestMetrics)
    assert entry.metrics.total_trades > 0
    assert entry.metrics.total_pnl is not None


# ================= ExperimentLedgerPort =================


def test_port_load_returns_empty_when_no_entries() -> None:
    port = ExperimentLedgerPort(combo_key="nonexistent")
    entries = port.load()
    assert entries == []


def test_port_save_and_load_roundtrip() -> None:
    entry = ExperimentLedgerEntry(
        combo_key="test-combo",
        seed="seed1",
        script_hash="abc123",
        start=_T0,
        end=_T0 + timedelta(days=30),
        tf=Timeframe.M1,
        initial_cash=_CASH,
        config=_config(),
        candles=_rising_columns(100),
        signal=_buy_and_hold_signal(100),
        metrics=_metrics(),
        combo_index=1,
        repro_key="repro-key",
    )
    port = ExperimentLedgerPort(combo_key="test-combo")
    port.save([entry])
    loaded = port.load()
    assert len(loaded) == 1
    assert loaded[0].combo_key == "test-combo"
    assert loaded[0].seed == "seed1"
    assert loaded[0].repro_key == "repro-key"


def test_port_load_filters_by_combo_key() -> None:
    entries = [
        ExperimentLedgerEntry(
            combo_key="combo-a",
            seed="seed1",
            script_hash="abc",
            start=_T0,
            end=_T0 + timedelta(days=1),
            tf=Timeframe.M1,
            initial_cash=_CASH,
            config=_config(),
            candles=_rising_columns(10),
            signal=_buy_and_hold_signal(10),
            metrics=_metrics(),
            combo_index=0,
            repro_key="repro-a",
        ),
        ExperimentLedgerEntry(
            combo_key="combo-b",
            seed="seed2",
            script_hash="def",
            start=_T0,
            end=_T0 + timedelta(days=1),
            tf=Timeframe.M1,
            initial_cash=_CASH,
            config=_config(),
            candles=_rising_columns(10),
            signal=_buy_and_hold_signal(10),
            metrics=_metrics(),
            combo_index=0,
            repro_key="repro-b",
        ),
    ]
    port = ExperimentLedgerPort(combo_key="combo-a")
    port.save(entries)
    loaded = port.load()
    assert len(loaded) == 1
    assert loaded[0].combo_key == "combo-a"


# ================= GridSweepResult =================


def test_grid_sweep_result_metrics_count_matches_entries() -> None:
    """GridSweepResult의 metrics 길이 = entries 길이."""
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n), "c1": _buy_and_hold_signal(n)}
    script_hashes = {"c0": "hash-0", "c1": "hash-1"}

    result = sweep_grid_and_record(
        cols,
        combos,
        _config(),
        timeframe=Timeframe.M1,
        initial_cash=_CASH,
        script_hashes=script_hashes,
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        seed=1,
    )
    assert isinstance(result, GridSweepResult)
    assert len(result.entries) == len(result.metrics) == len(result.columns)
    assert len(result.configs) == len(combos)


# ================= Negative: empty combos =================


def test_sweep_rejects_empty_combos() -> None:
    n = 10
    cols = _rising_columns(n)
    with pytest.raises(ValueError):
        sweep_grid_and_record(
            cols,
            {},  # empty combos
            _config(),
            timeframe=Timeframe.M1,
            initial_cash=_CASH,
            script_hashes={},
            data_lineage_hash="lineage-xyz",
            rollup_version="rollup-1",
            seed=1,
        )
