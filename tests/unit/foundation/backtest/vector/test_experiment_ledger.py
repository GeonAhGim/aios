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

from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.backtest.vector.experiment_ledger import (
    ExperimentLedgerEntry,
    record_grid_entry,
)
from src.foundation.backtest.vector.fills import VectorSignal
from src.foundation.backtest.vector.grid import sweep_grid_and_record
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
        latency_ms=0, partial_fill=PartialFillConfig(max_participation_pct=_D("1")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=True, trailing=True),
        magnifier_tf=None, costs=CostsConfig(funding=False, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=False, dividends=False), calendar="24x7",
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
    always = BoolSignal(values=np.ones(n, dtype=np.bool_), na=np.zeros(n, dtype=np.bool_))
    never = BoolSignal(values=np.zeros(n, dtype=np.bool_), na=np.zeros(n, dtype=np.bool_))
    return VectorSignal(entries=always, exits=never, quantity=_D("1"))


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
        combo_key="rsi_len=14", combo_index=0, script_hash="script-abc",
        data_lineage_hash="lineage-xyz", rollup_version="rollup-1", config=config, seed=42,
    )
    a = record_grid_entry(**kwargs)
    b = record_grid_entry(**kwargs)
    assert a.reproducibility_key == b.reproducibility_key
    assert len(a.reproducibility_key) == 64  # sha256 hex


def test_record_grid_entry_seed_alone_changes_the_key() -> None:
    config = _config()
    base = dict(
        combo_key="rsi_len=14", combo_index=0, script_hash="script-abc",
        data_lineage_hash="lineage-xyz", rollup_version="rollup-1", config=config,
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
        combo_key="c0", combo_index=0, script_hash="script-abc",
        data_lineage_hash="lineage-xyz", rollup_version="rollup-1", config=config, seed=7,
    )
    expected = reproducibility_key(
        script_hash="script-abc:seed=7",
        data_lineage_hash="lineage-xyz", rollup_version="rollup-1", config=config,
    )
    assert entry.reproducibility_key == expected


# ================= sweep_grid_and_record =================


def test_sweep_grid_and_record_rejects_script_hash_key_mismatch() -> None:
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n), "c1": _buy_and_hold_signal(n)}
    with pytest.raises(ValueError, match="script_hashes"):
        sweep_grid_and_record(
            cols, combos, _config(), timeframe=Timeframe.M1, initial_cash=_CASH,
            script_hashes={"c0": "hash-0"},  # missing c1
            data_lineage_hash="lineage-xyz", rollup_version="rollup-1", seed=1,
        )


def test_sweep_grid_and_record_produces_one_entry_per_combo_in_order() -> None:
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n), "c1": _buy_and_hold_signal(n)}
    script_hashes = {"c0": "hash-0", "c1": "hash-1"}

    result, entries = sweep_grid_and_record(
        cols, combos, _config(), timeframe=Timeframe.M1, initial_cash=_CASH,
        script_hashes=script_hashes, data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1", seed=1,
    )

    assert set(result.results.keys()) == set(combos.keys())
    assert [e.combo_key for e in entries] == ["c0", "c1"]
    assert [e.combo_index for e in entries] == [0, 1]
    assert entries[0].reproducibility_key != entries[1].reproducibility_key


def test_sweep_grid_and_record_same_setup_and_seed_are_byte_identical_keys() -> None:
    """(c) 1/2 — 같은 설정·시드로 grid를 두 번 실행하면 원장 엔트리의 재현
    키가 바이트 동일해야 한다."""
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n), "c1": _buy_and_hold_signal(n)}
    script_hashes = {"c0": "hash-0", "c1": "hash-1"}
    kwargs = dict(
        columns=cols, combos=combos, config=_config(), timeframe=Timeframe.M1,
        initial_cash=_CASH, script_hashes=script_hashes,
        data_lineage_hash="lineage-xyz", rollup_version="rollup-1", seed=99,
    )

    _, entries_a = sweep_grid_and_record(**kwargs)
    _, entries_b = sweep_grid_and_record(**kwargs)

    assert [e.reproducibility_key for e in entries_a] == [
        e.reproducibility_key for e in entries_b
    ]


def test_sweep_grid_and_record_seed_alone_changes_every_entry_key() -> None:
    """(c) 2/2 — 시드 하나만 바꾸면 (설정·조합은 그대로) 원장 엔트리의 재현
    키가 전부 달라져야 한다."""
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n), "c1": _buy_and_hold_signal(n)}
    script_hashes = {"c0": "hash-0", "c1": "hash-1"}
    base_kwargs = dict(
        columns=cols, combos=combos, config=_config(), timeframe=Timeframe.M1,
        initial_cash=_CASH, script_hashes=script_hashes,
        data_lineage_hash="lineage-xyz", rollup_version="rollup-1",
    )

    _, entries_seed_1 = sweep_grid_and_record(seed=1, **base_kwargs)
    _, entries_seed_2 = sweep_grid_and_record(seed=2, **base_kwargs)

    keys_1 = [e.reproducibility_key for e in entries_seed_1]
    keys_2 = [e.reproducibility_key for e in entries_seed_2]
    assert keys_1 != keys_2
    assert all(k1 != k2 for k1, k2 in zip(keys_1, keys_2, strict=True))
