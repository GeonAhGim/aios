"""IND-7g — three-way cross-verification: TA-Lib C vs incremental engine
vs vectorized engine.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.3 IND-7g,
docs/design/ADR-2026-09-06-F-adopt-dont-rewrite.md D2.

Instead of hand-written expected values, runs three implementations (direct
TA-Lib C, `engine.incremental`, `engine.vectorized`) on the same input and
diffs them. Agreement within `REFERENCE_TOLERANCE` (1e-9, scale-relative)
snapshots the result as a reference vector; any deviation excludes that
indicator from the verified list (fail-closed) — `VerificationReport.
mismatches` always carries the detail so nothing passes silently.

Verification is limited to indicators with a kernel in both
`engine.incremental` and `engine.vectorized` (IND-1, currently 11) — the
three-way comparison presupposes both engines exist. The remaining 150
(TA-Lib auto-generated, IND-2g) are out of scope here.

Two dataset branches:
- `synthetic:<seed>` — seeded normal-distribution random walk (same formula
  as `test_engine_equivalence.py`), scale varied by order of magnitude to
  probe floating-point precision boundaries.
- `real_sample_proxy` — second fixed-seed dataset mixing a
  trend->range->volatility-spike regime. **Unverified**: this worker has no
  external network access, so it is not real exchange data — only a
  substitute approximating real data's statistical traits (e.g. volatility
  clustering). Swap in a real data file by changing only this function.

CI/nightly split: `sample_names()` draws a fixed-seed subset — with only 11
indicators today, a sample of 30 equals the full set, but the sampling path
is opened up in advance for when IND-13 grows the catalog.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import talib

from src.core.indicators.engine import incremental, vectorized
from src.core.indicators.engine.vectorized import compute, run_incremental
from src.core.indicators.registry import DEFAULT_REGISTRY
from src.core.indicators.spec import IndicatorSpec
from src.core.indicators.specs_talib import TALIB_SPECS

__all__ = [
    "REFERENCE_TOLERANCE",
    "VECTORS_DIR",
    "VERIFIABLE_NAMES",
    "Dataset",
    "Mismatch",
    "VerificationReport",
    "main",
    "reference_vector",
    "run_verification",
    "sample_names",
    "verify_indicator",
    "write_snapshot",
]

logger = logging.getLogger(__name__)

REFERENCE_TOLERANCE = 1e-9
VECTORS_DIR = Path(__file__).parent / "vectors"

FloatArray = np.ndarray[Any, np.dtype[np.float64]]

VERIFIABLE_NAMES: tuple[str, ...] = tuple(
    sorted(set(TALIB_SPECS) & set(vectorized._KERNELS) & set(incremental._STATES))
)


@dataclass(frozen=True)
class Dataset:
    id: str
    columns: dict[str, FloatArray]


@dataclass(frozen=True)
class Mismatch:
    """One mismatch detail — kept as-is in the report to prevent a silent pass."""

    name: str
    dataset: str
    params: dict[str, int]
    output: str
    index: int
    talib_value: float | None
    incremental_value: float | None
    vectorized_value: float | None
    worst_rel_error: float


@dataclass(frozen=True)
class VerificationReport:
    verified: tuple[str, ...]
    excluded: tuple[str, ...]
    mismatches: tuple[Mismatch, ...]


def _synthetic_ohlcv(seed: int, n: int, scale: float) -> dict[str, FloatArray]:
    rng = np.random.default_rng(seed)
    close = scale * (1.0 + 0.01 * np.cumsum(rng.normal(size=n)))
    close = np.maximum(close, scale * 0.05)
    spread = scale * 0.005 * np.abs(rng.normal(size=n))
    high = close + spread + scale * 0.001
    low = close - spread - scale * 0.001
    volume = np.abs(rng.normal(size=n)) * 1000.0 + 1.0
    flat = rng.integers(0, n, size=max(1, n // 10))
    close[flat[flat > 0]] = close[flat[flat > 0] - 1]
    return {"open": close, "high": high, "low": low, "close": close, "volume": volume}


def _real_sample_proxy_ohlcv(seed: int, n: int) -> dict[str, FloatArray]:
    """Unverified: not real exchange data — a substitute fixed-seed dataset
    mimicking a regime transition (trend->range->volatility spike) (see module docstring)."""
    rng = np.random.default_rng(seed)
    segment = max(1, n // 3)
    drift = np.concatenate(
        [
            np.full(segment, 0.0006),
            np.full(segment, 0.0),
            np.full(n - 2 * segment, -0.0004),
        ]
    )
    vol = np.concatenate(
        [
            np.full(segment, 0.004),
            np.full(segment, 0.002),
            np.full(n - 2 * segment, 0.012),
        ]
    )
    log_returns = drift + vol * rng.normal(size=n)
    close = 100.0 * np.exp(np.cumsum(log_returns))
    spread = close * 0.003 * (0.5 + np.abs(rng.normal(size=n)))
    high = close + spread
    low = np.maximum(close - spread, 0.01)
    volume = np.abs(rng.normal(size=n)) * 5000.0 + 100.0
    return {"open": close, "high": high, "low": low, "close": close, "volume": volume}


def default_datasets() -> list[Dataset]:
    datasets = [
        Dataset(f"synthetic:{seed}", _synthetic_ohlcv(seed, 400, 10.0**seed)) for seed in range(3)
    ]
    datasets.append(Dataset("real_sample_proxy", _real_sample_proxy_ohlcv(4242, 600)))
    return datasets


def _param_variants(spec: IndicatorSpec) -> list[dict[str, int]]:
    """Default + minimum boundary values (only when they differ) — also exercises
    numerical error near the window boundary."""
    default = {p.name: p.default for p in spec.params}
    minimal = {p.name: p.min for p in spec.params}
    if spec.name == "MACD" and minimal["fastperiod"] >= minimal["slowperiod"]:
        minimal["slowperiod"] = minimal["fastperiod"] + 1
    return [default] if minimal == default else [default, minimal]


def _talib_direct(
    name: str, spec: IndicatorSpec, columns: Mapping[str, FloatArray], params: dict[str, int]
) -> dict[str, FloatArray]:
    inputs = [np.asarray(columns[key], dtype=np.float64) for key in spec.inputs]
    raw = getattr(talib, name)(*inputs, **params)
    if len(spec.outputs) == 1:
        return {spec.outputs[0]: np.asarray(raw, dtype=np.float64)}
    return dict(zip(spec.outputs, raw, strict=True))


def verify_indicator(name: str, datasets: Sequence[Dataset]) -> list[Mismatch]:
    """Three-way comparison for one indicator across every dataset x parameter combo."""
    spec = TALIB_SPECS[name]
    mismatches: list[Mismatch] = []
    for dataset in datasets:
        n = len(dataset.columns["close"])
        for params in _param_variants(spec):
            lookback = DEFAULT_REGISTRY.lookback(name, params)
            if n <= lookback + 5:
                continue
            reference = _talib_direct(name, spec, dataset.columns, params)
            batch = compute(name, dataset.columns, params)
            streamed = run_incremental(name, dataset.columns, params)
            for key in spec.outputs:
                ref, bat, strm = reference[key], batch[key], streamed[key]
                nan_ok = np.array_equal(np.isnan(ref), np.isnan(bat)) and np.array_equal(
                    np.isnan(ref), np.isnan(strm)
                )
                if not nan_ok:
                    mismatches.append(
                        Mismatch(name, dataset.id, params, key, -1, None, None, None, float("inf"))
                    )
                    continue
                finite = ~np.isnan(ref)
                if not finite.any():
                    continue
                abs_max = np.maximum(np.abs(bat[finite]), np.abs(strm[finite]))
                scale = np.maximum(1.0, np.maximum(np.abs(ref[finite]), abs_max))
                err_bat = np.abs(ref[finite] - bat[finite])
                err_strm = np.abs(ref[finite] - strm[finite])
                err = np.maximum(err_bat, err_strm) / scale
                worst = float(np.max(err))
                if worst > REFERENCE_TOLERANCE:
                    idx = int(np.flatnonzero(finite)[int(np.argmax(err))])
                    mismatches.append(
                        Mismatch(
                            name, dataset.id, params, key, idx,
                            float(ref[idx]), float(strm[idx]), float(bat[idx]), worst,
                        )
                    )
    return mismatches


def reference_vector(name: str, dataset: Dataset) -> dict[str, list[float | None]]:
    """Default-parameter output for a verified indicator — the snapshot's `outputs` field."""
    spec = TALIB_SPECS[name]
    params = {p.name: p.default for p in spec.params}
    values = compute(name, dataset.columns, params)
    return {
        key: [None if np.isnan(v) else float(v) for v in arr] for key, arr in values.items()
    }


def write_snapshot(name: str, dataset: Dataset, *, vectors_dir: Path | None = None) -> Path:
    target_dir = vectors_dir if vectors_dir is not None else VECTORS_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{name}.json"
    payload = {
        "indicator": name,
        "dataset": dataset.id,
        "params": {p.name: p.default for p in TALIB_SPECS[name].params},
        "outputs": reference_vector(name, dataset),
    }
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")
    return path


def sample_names(k: int, *, seed: int = 20260906) -> tuple[str, ...]:
    """Fixed-seed sample for CI. Returns the full set if the catalog is
    smaller than `k` (currently 11)."""
    names = list(VERIFIABLE_NAMES)
    if k >= len(names):
        return tuple(names)
    rng = np.random.default_rng(seed)
    idx = rng.choice(len(names), size=k, replace=False)
    return tuple(sorted(names[i] for i in idx))


def run_verification(
    names: Sequence[str] | None = None,
    *,
    datasets: Sequence[Dataset] | None = None,
    write_snapshots: bool = False,
    vectors_dir: Path | None = None,
) -> VerificationReport:
    target = tuple(names) if names is not None else VERIFIABLE_NAMES
    used_datasets = list(datasets) if datasets is not None else default_datasets()
    verified: list[str] = []
    excluded: list[str] = []
    mismatches: list[Mismatch] = []
    for name in target:
        found = verify_indicator(name, used_datasets)
        if found:
            excluded.append(name)
            mismatches.extend(found)
            continue
        verified.append(name)
        if write_snapshots:
            write_snapshot(name, used_datasets[0], vectors_dir=vectors_dir)
    return VerificationReport(tuple(verified), tuple(excluded), tuple(mismatches))


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="IND-7g 참조 벡터 3자 교차검증")
    parser.add_argument("--mode", choices=["nightly", "ci"], default="nightly")
    parser.add_argument("--sample", type=int, default=30)
    args = parser.parse_args(argv)
    names = VERIFIABLE_NAMES if args.mode == "nightly" else sample_names(args.sample)
    report = run_verification(names, write_snapshots=True)
    logger.info(
        "verified=%d excluded=%d names=%s", len(report.verified), len(report.excluded), names
    )
    for m in report.mismatches:
        logger.warning(
            "MISMATCH %s dataset=%s output=%s idx=%s err=%.3e talib=%s inc=%s vec=%s",
            m.name, m.dataset, m.output, m.index, m.worst_rel_error,
            m.talib_value, m.incremental_value, m.vectorized_value,
        )  # fmt: skip
    return 1 if report.excluded else 0


if __name__ == "__main__":
    raise SystemExit(main())
