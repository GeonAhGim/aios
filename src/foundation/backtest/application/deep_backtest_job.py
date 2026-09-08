"""BT-11 — deep backtest job (checkpoint / resume / progress).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-11, §5 ("deep backtest job: checkpoint (bar index) conditional
UPDATE, resume on worker restart"), §9.5 BT-11 (DoD: interrupt/resume
byte-identical result, monotonic progress, reject a resume whose config
changed).

This module adds only checkpointing and progress reporting around BT-10's
execution core (`quick_backtest.run_quick_backtest`) — it never
reimplements fill, cost, or slippage math (BT-2~8 stay the only source of
those numbers). `run_quick_backtest` is a pure function of
`(config, columns, strategy, initial_cash, funding_rate)`: replaying the
same growing bar prefix always reproduces the same state. That determinism
is what makes checkpoint/resume byte-identical — a checkpoint only needs
to remember *how far* a job got (`bars_processed`), not the loop's
internal state (cash, pending order, holding, ...); resuming simply
re-derives that state by re-running the core over `columns[:end]` for each
remaining chunk, ending at the same final prefix (`columns[:n]`) a single
uninterrupted run would reach.

Persistence: this leaf ships a `CheckpointPort` (Protocol) plus an
in-memory adapter for tests. A durable (Postgres) adapter is a follow-up
leaf — the migration slot is occupied by task-2136 (decision on
task-2152).

Resume is fail-closed: a checkpoint only resumes a job whose current
`config_hash` (BT-9, `domain/reproducibility.py`) matches the checkpoint's
stored `config_hash`. Any mismatch (e.g. a changed commission) raises
`BacktestCheckpointMismatchError` instead of silently restarting from
scratch or silently continuing under the new config.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from src.foundation.backtest.application.quick_backtest import (
    QuickBacktestResult,
    SignalSource,
    run_quick_backtest,
)
from src.foundation.backtest.domain.models_v2 import BacktestConfigV2
from src.foundation.backtest.domain.reproducibility import config_hash as compute_config_hash
from src.foundation.market_data.contracts.v1 import Timeframe
from src.foundation.market_data.domain.candle_columns import CandleColumns

__all__ = [
    "DEFAULT_CHUNK_BARS",
    "BacktestCheckpointMismatchError",
    "CheckpointPort",
    "ContinuePredicate",
    "DeepBacktestCheckpoint",
    "DeepBacktestOutcome",
    "InMemoryCheckpointStore",
    "ProgressCallback",
    "run_deep_backtest_job",
]

DEFAULT_CHUNK_BARS = 200  # bar segment between checkpoints/progress reports


class BacktestCheckpointMismatchError(ValueError):
    """`BT_DEEP_CHECKPOINT_MISMATCH` — resume 요청의 현재 `config_hash`가
    저장된 체크포인트와 다르다. 조용히 처음부터 재실행하거나 이어서
    계산하지 않고 거부한다(fail-closed, §9.5 BT-11 DoD (b))."""


@dataclass(frozen=True, slots=True)
class DeepBacktestCheckpoint:
    config_hash: str
    last_processed_ts: datetime
    bars_processed: int
    total_bars: int
    state_hash: str

    @property
    def progress(self) -> float:
        return self.bars_processed / self.total_bars


class CheckpointPort(Protocol):
    """체크포인트 저장소 포트 — 영속 어댑터는 후속 리프(task-2152 decision)."""

    def load(self, job_id: str) -> DeepBacktestCheckpoint | None: ...
    def save(self, job_id: str, checkpoint: DeepBacktestCheckpoint) -> None: ...


class InMemoryCheckpointStore:
    """테스트·단일 프로세스용 인메모리 `CheckpointPort` 구현."""

    def __init__(self) -> None:
        self._checkpoints: dict[str, DeepBacktestCheckpoint] = {}

    def load(self, job_id: str) -> DeepBacktestCheckpoint | None:
        return self._checkpoints.get(job_id)

    def save(self, job_id: str, checkpoint: DeepBacktestCheckpoint) -> None:
        self._checkpoints[job_id] = checkpoint


class ProgressCallback(Protocol):
    def __call__(self, progress: float) -> None: ...


class ContinuePredicate(Protocol):
    """`False`를 돌려주면 다음 구간을 처리하지 않고 현재 체크포인트에서
    멈춘다(협조적 취소 — 강제 종료가 아니다)."""

    def __call__(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class DeepBacktestOutcome:
    """`suspended`면 `result`가 없다 — 호출자가 같은 `job_id`로 다시
    호출해 체크포인트부터 재개한다. `completed`면 `result`가 전체 구간을
    처리한 BT-10 `QuickBacktestResult`다."""

    status: Literal["completed", "suspended"]
    result: QuickBacktestResult | None
    checkpoint: DeepBacktestCheckpoint | None


def _state_digest(result: QuickBacktestResult) -> str:
    """BT-9(`reproducibility.py`)와 같은 정준 직렬화 규칙(정렬 키, 고정
    구분자, ensure_ascii, allow_nan=False)으로 누적 상태를 sha256 hex한다."""
    payload = {
        "cash": str(result.cash),
        "position_quantity": str(result.position_quantity),
        "final_equity": str(result.final_equity),
        "funding_cost": str(result.funding_cost),
        "borrow_cost": str(result.borrow_cost),
        "bars": result.bars,
        "expired_orders": result.expired_orders,
        "fills": [
            [
                f.bar_index, f.side.value, f.order_type, str(f.quantity), str(f.price),
                str(f.commission), str(f.remaining_quantity),
            ]
            for f in result.fills
        ],
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _prefix(columns: CandleColumns, end: int) -> CandleColumns:
    return CandleColumns(
        ts=columns.ts[:end], open=columns.open[:end], high=columns.high[:end],
        low=columns.low[:end], close=columns.close[:end], volume=columns.volume[:end],
        quote_volume=columns.quote_volume[:end],
    )


def run_deep_backtest_job(
    config: BacktestConfigV2,
    columns: CandleColumns,
    *,
    timeframe: Timeframe,
    strategy: SignalSource,
    initial_cash: Decimal,
    job_id: str,
    checkpoints: CheckpointPort,
    funding_rate: Decimal | None = None,
    lower_columns: CandleColumns | None = None,
    chunk_bars: int = DEFAULT_CHUNK_BARS,
    on_progress: ProgressCallback | None = None,
    should_continue: ContinuePredicate | None = None,
) -> DeepBacktestOutcome:
    """`columns` 전체를 `chunk_bars` 단위 구간으로 나눠 BT-10 실행 코어를
    반복 호출한다. 매 구간 끝마다 체크포인트를 저장하고 진행률을 보고한다
    — `should_continue()`가 `False`를 돌려주면 다음 구간을 처리하지 않고
    멈춘다(호출자가 나중에 같은 `job_id`로 재개)."""
    if not job_id.strip():
        raise ValueError("job_id는 비어 있을 수 없다")
    if chunk_bars <= 0:
        raise ValueError(f"chunk_bars는 양수여야 한다: {chunk_bars}")
    n = len(columns)
    if n == 0:
        raise ValueError("캔들이 0개다 — 딥 백테스트할 구간이 없다")

    def _run_chunk(end: int) -> tuple[DeepBacktestCheckpoint, QuickBacktestResult]:
        chunk_result = run_quick_backtest(
            config, _prefix(columns, end), timeframe=timeframe, strategy=strategy,
            initial_cash=initial_cash, funding_rate=funding_rate,
            lower_columns=lower_columns, max_bars=n,
        )
        chunk_checkpoint = DeepBacktestCheckpoint(
            config_hash=current_hash, last_processed_ts=columns.ts[end - 1],
            bars_processed=end, total_bars=n, state_hash=_state_digest(chunk_result),
        )
        return chunk_checkpoint, chunk_result

    current_hash = compute_config_hash(config)
    checkpoint = checkpoints.load(job_id)
    start = 0
    if checkpoint is not None:
        if checkpoint.config_hash != current_hash:
            raise BacktestCheckpointMismatchError(
                f"job {job_id!r} 체크포인트의 config_hash가 현재 설정과 다르다 — "
                "resume을 거부한다(fail-closed)"
            )
        start = checkpoint.bars_processed

    boundaries = list(range(chunk_bars, n, chunk_bars))
    if not boundaries or boundaries[-1] != n:
        boundaries.append(n)

    result: QuickBacktestResult | None = None
    for end in boundaries:
        if end <= start:
            continue
        if should_continue is not None and not should_continue():
            return DeepBacktestOutcome(status="suspended", result=None, checkpoint=checkpoint)
        checkpoint, result = _run_chunk(end)
        checkpoints.save(job_id, checkpoint)
        if on_progress is not None:
            on_progress(checkpoint.progress)

    if result is None:  # start == n이었다 — 이미 완결된 job에 다시 걸린 호출
        checkpoint, result = _run_chunk(n)
        checkpoints.save(job_id, checkpoint)
        if on_progress is not None:
            on_progress(checkpoint.progress)
    return DeepBacktestOutcome(status="completed", result=result, checkpoint=checkpoint)
