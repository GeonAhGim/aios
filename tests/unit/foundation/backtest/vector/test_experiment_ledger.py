"""BT-16b — `backtest/vector/experiment_ledger.py` + `grid.sweep_grid_and_record` tests.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.9 BT-16
(실험 원장 기록, task-2426 DoD).

- (a)/(e) 순수성은 zone manifest + import 자체로 검증(이 모듈은 I/O를 하지
  않는다 — 별도 fixture 없이 순수 함수만 호출).
- (c) 결정론: 같은 설정·시드로 두 번 실행하면 재현 키가 바이트 동일, 시드
  하나만 바꾸면 달라진다(단언 2건: `test_..._same_inputs_are_byte_identical`,
  `test_..._seed_alone_changes_the_key`).
- negative(≥3): `ExperimentLedgerEntry`의 fail-closed 검증(빈 combo_key·음수
  combo_index) + `sweep_grid_and_record`의 script_hashes/combos 키 불일치 거부.
- `ExperimentLedgerPort`는 `runtime_checkable` Protocol이라 어댑터 없이도
  구조적 타이핑 계약(conforming/non-conforming)을 직접 검증할 수 있다 —
  실제 DB 어댑터는 task-2426 DoD(a)로 이 리프의 범위 밖이다.

DEEPEN(task-3053): 원래 이 파일은 실제 소스와 맞지 않는(구현된 적 없는)
API(`GridSweepResult.metrics/columns/configs`, 동기 `ExperimentLedgerPort(
combo_key=...).save()/.load()`, `sweep_grid_and_record(...).entries`)를
가정해 작성되어 있어 수집 단계에서부터 ImportError로 깨져 있었다 — 실제
`experiment_ledger.py`/`grid.py`/`ports/experiment_ledger.py`에 맞춰
전면 재작성하고, 없던 실패 주입·게이트 적색 재현 2종을 추가한다(수치
성능 단언은 이미 `tests/performance/test_vector_grid_throughput.py`가
1개월 M1 실측으로 담당한다 — 여기서 중복하지 않는다).
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pytest

import src.foundation.backtest.vector.experiment_ledger as experiment_ledger_mod
from src.foundation.backtest.application.quick_backtest import QuickBacktestResult
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
    always = BoolSignal(values=np.ones(n, dtype=np.bool_), na=np.zeros(n, dtype=np.bool_))
    never = BoolSignal(values=np.zeros(n, dtype=np.bool_), na=np.zeros(n, dtype=np.bool_))
    return VectorSignal(entries=always, exits=never, quantity=_D("1"))


# ================= ExperimentLedgerEntry (fail-closed, negative 1/3+2/3) =================


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


# ================= DEEPEN(task-3053): 실패 주입 =================


@pytest.mark.perf
def test_record_grid_entry_still_correct_when_bt9_key_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: BT-9 `reproducibility_key` 계산이 실제로 느려지면(예: 회귀로
    config 정규화 단계가 무거워진 상황) `record_grid_entry`가 그 지연을
    그대로 감내하면서도 지연 없는 호출과 바이트 동일한 키를 내는지 확인한다
    -- combo_index/seed 결합 로직이 지연 유무와 무관하게 결정적임을 보장한다
    (BT-9 test_reproducibility.py의 config_hash stall 주입과 동일한 형태)."""
    original_key = experiment_ledger_mod.reproducibility_key
    delay_s = 0.01

    def _stalled_key(
        *,
        script_hash: str,
        data_lineage_hash: str,
        rollup_version: str,
        config: BacktestConfigV2,
    ) -> str:
        time.sleep(delay_s)
        return original_key(
            script_hash=script_hash,
            data_lineage_hash=data_lineage_hash,
            rollup_version=rollup_version,
            config=config,
        )

    monkeypatch.setattr(experiment_ledger_mod, "reproducibility_key", _stalled_key)

    config = _config()
    base = dict(
        combo_key="rsi_len=14",
        combo_index=0,
        script_hash="script-abc",
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        config=config,
        seed=42,
    )
    started = time.perf_counter()
    stalled = record_grid_entry(**base)
    elapsed_s = time.perf_counter() - started

    monkeypatch.undo()
    baseline = record_grid_entry(**base)

    assert elapsed_s >= delay_s
    assert stalled.reproducibility_key == baseline.reproducibility_key


# ================= DEEPEN(task-3053): 게이트 적색 재현 =================


def test_seed_sensitivity_gate_actually_fails_if_seed_folding_regresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: `record_grid_entry`가 `seed`를 `script_hash`에 접어
    넘기는 부분(DoD(c))이 회귀로 사라지면(BT-9에 seed 없는 원본
    `script_hash`를 그대로 넘기게 되면), `test_record_grid_entry_seed_alone_
    changes_the_key`와 동일한 단언식이 실제로 `AssertionError`를 내는지(=
    CI가 실제로 빨간불이 되는지) 확인한다 -- 이 테스트가 없으면 그 단언이
    항상 통과하는 tautology인지 아무도 검증하지 못한다."""
    original_key = experiment_ledger_mod.reproducibility_key

    def _seed_blind_key(
        *,
        script_hash: str,
        data_lineage_hash: str,
        rollup_version: str,
        config: BacktestConfigV2,
    ) -> str:
        # 회귀 흉내: script_hash에 접힌 ":seed=N" 접미사를 지우고 BT-9에 넘긴다.
        stripped = script_hash.split(":seed=")[0]
        return original_key(
            script_hash=stripped,
            data_lineage_hash=data_lineage_hash,
            rollup_version=rollup_version,
            config=config,
        )

    monkeypatch.setattr(experiment_ledger_mod, "reproducibility_key", _seed_blind_key)

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
    with pytest.raises(AssertionError):
        assert a.reproducibility_key != b.reproducibility_key


# ================= sweep_grid_and_record (negative 3/3 + integration) =================


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

    result, entries = sweep_grid_and_record(
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
    assert len(entries) == 2
    assert entries[0].combo_key == "c0"
    assert entries[0].combo_index == 0
    assert entries[1].combo_key == "c1"
    assert entries[1].combo_index == 1
    assert set(result.results) == {"c0", "c1"}
    assert isinstance(result.results["c0"], QuickBacktestResult)


def test_sweep_grid_and_record_deterministic_with_same_seed() -> None:
    """동일 시드 → 동일한 entries + 체결 결과 (BT-16b 결정론 요구)."""
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n)}
    script_hashes = {"c0": "hash-0"}

    r1, e1 = sweep_grid_and_record(
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
    r2, e2 = sweep_grid_and_record(
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
    assert e1[0].reproducibility_key == e2[0].reproducibility_key
    assert r1.results["c0"].final_equity == r2.results["c0"].final_equity


def test_sweep_grid_and_record_seed_changes_reproducibility_key_only() -> None:
    """시드가 달라지면 재현 키는 달라지지만, 체결 로직 자체는 시드를 쓰지
    않으므로(BT-15b 벡터 체결은 결정적) 체결 결과는 그대로다 -- 시드가
    "재현 키 계보"에만 영향을 주고 결과를 조용히 바꾸지 않음을 확인한다."""
    n = 10
    cols = _rising_columns(n)
    combos = {"c0": _buy_and_hold_signal(n)}
    script_hashes = {"c0": "hash-0"}

    r1, e1 = sweep_grid_and_record(
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
    r2, e2 = sweep_grid_and_record(
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
    assert e1[0].reproducibility_key != e2[0].reproducibility_key
    assert r1.results["c0"].final_equity == r2.results["c0"].final_equity


def test_sweep_grid_and_record_allows_empty_combos() -> None:
    """`sweep_grid`(BT-16a)와 동일하게 빈 combos는 예외 없이 빈 결과를 낸다
    (grid.py 모듈 docstring 명시 계약) -- script_hashes도 함께 비어 있으면
    키 집합이 일치해 검증을 통과한다."""
    n = 10
    cols = _rising_columns(n)
    result, entries = sweep_grid_and_record(
        cols,
        {},
        _config(),
        timeframe=Timeframe.M1,
        initial_cash=_CASH,
        script_hashes={},
        data_lineage_hash="lineage-xyz",
        rollup_version="rollup-1",
        seed=1,
    )
    assert result.results == {}
    assert entries == ()


# ================= ExperimentLedgerPort (구조적 타이핑 계약) =================


class _FakeLedgerPort:
    def __init__(self) -> None:
        self.appended: list[ExperimentLedgerEntry] = []

    async def append(self, conn: object, entry: ExperimentLedgerEntry) -> None:
        self.appended.append(entry)


class _NotALedgerPort:
    pass


def test_experiment_ledger_port_accepts_conforming_implementation() -> None:
    assert isinstance(_FakeLedgerPort(), ExperimentLedgerPort)


def test_experiment_ledger_port_rejects_missing_append() -> None:
    assert not isinstance(_NotALedgerPort(), ExperimentLedgerPort)
