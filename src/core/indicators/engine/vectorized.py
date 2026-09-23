"""IND-1 — Column-oriented batch indicator computation (backtest path) + equivalence
contract with incremental engine.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.3 `engine/vectorized.py`,
§9.3 IND-1 (DoD: incremental == batch results within 1e-9).

Pure module — no I/O. Window statistics are computed independently per window via
`sliding_window_view` (no cumulative sum drift); EMA and Wilder recursions are
inherently sequential, so they share the same iterative loop expression as
`incremental.py`. Lookback is read only from `engine/__init__.resolve_request`
(L02→L01 TA-Lib measured values); if the produced NaN prefix length differs from
that value, fail-closed with `INDICATOR_LOOKBACK_MISMATCH`. Unmet lookback
regions are explicitly returned as NaN (zero substitution is forbidden).

`check_equivalence()` is the equivalence contract between the two engines: feed
the same columns into the incremental engine bar-by-bar, compare against batch
results — raises `INDICATOR_ENGINE_MISMATCH` if NaN positions differ or scale
deviation `|a-b| / max(1, |a|, |b|)` exceeds `EQUIVALENCE_TOLERANCE` (1e-9).
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from decimal import Decimal
from typing import Any

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from src.core.indicators.engine import resolve_request
from src.core.indicators.engine.incremental import IncrementalIndicator
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorError, IndicatorRegistry

__all__ = ["EQUIVALENCE_TOLERANCE", "check_equivalence", "compute", "run_incremental"]

EQUIVALENCE_TOLERANCE = 1e-9

FloatArray = np.ndarray[Any, np.dtype[np.float64]]
Columns = Mapping[str, Sequence[float | int | Decimal] | FloatArray]
_Kernel = Callable[[dict[str, FloatArray], dict[str, int]], tuple[FloatArray, ...]]


def _pad(values: FloatArray, n: int) -> FloatArray:
    """Right-align: pad leading lookback period with NaN."""
    out = np.full(n, np.nan)
    if len(values):
        out[n - len(values) :] = values
    return out


def _windows(x: FloatArray, size: int) -> FloatArray:
    if len(x) < size:
        return np.empty((0, size))
    return sliding_window_view(x, size)


def _ema(x: FloatArray, period: int, skip: int = 0) -> FloatArray:
    """SMA-seeded EMA (sequential). Skip first bars, then seed (matches MACD fast line,
    TA-Lib behavior)."""
    out = np.full(len(x), np.nan)
    start = skip + period - 1
    if start >= len(x):
        return out
    k = 2.0 / (period + 1)
    prev = float(np.sum(x[skip : start + 1]) / period)
    out[start] = prev
    for i in range(start + 1, len(x)):
        prev = prev + (float(x[i]) - prev) * k
        out[i] = prev
    return out


def _wilder(x: FloatArray, period: int) -> FloatArray:
    out = np.full(len(x), np.nan)
    if len(x) < period:
        return out
    prev = float(np.sum(x[:period]) / period)
    out[period - 1] = prev
    for i in range(period, len(x)):
        prev = (prev * (period - 1) + float(x[i])) / period
        out[i] = prev
    return out


def _typical(c: dict[str, FloatArray]) -> FloatArray:
    return (c["high"] + c["low"] + c["close"]) / 3.0


def _sma(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
    n = len(c["close"])
    return (_pad(_windows(c["close"], p["timeperiod"]).mean(axis=1), n),)


def _ema_kernel(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
    return (_ema(c["close"], p["timeperiod"]),)


def _rsi(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
    diff = np.diff(c["close"])
    gain = _wilder(np.where(diff > 0, diff, 0.0), p["timeperiod"])
    loss = _wilder(np.where(diff < 0, -diff, 0.0), p["timeperiod"])
    total = gain + loss
    with np.errstate(invalid="ignore", divide="ignore"):
        rsi = np.where(total != 0.0, 100.0 * (gain / total), 0.0)
    rsi[np.isnan(total)] = np.nan
    return (_pad(rsi, len(c["close"])),)


def _atr(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
    high, low, close = c["high"], c["low"], c["close"]
    prev_close = close[:-1]
    tr = np.maximum.reduce(
        [high[1:] - low[1:], np.abs(high[1:] - prev_close), np.abs(low[1:] - prev_close)]
    )
    return (_pad(_wilder(tr, p["timeperiod"]), len(close)),)


def _cci(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
    tp = _typical(c)
    win = _windows(tp, p["timeperiod"])
    avg = win.mean(axis=1)
    md = np.abs(win - avg[:, None]).mean(axis=1)
    num = tp[p["timeperiod"] - 1 :] - avg
    with np.errstate(invalid="ignore", divide="ignore"):
        cci = np.where((num != 0.0) & (md != 0.0), num / (0.015 * md), 0.0)
    return (_pad(cci, len(tp)),)


def _willr(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
    period = p["timeperiod"]
    hh = _windows(c["high"], period).max(axis=1, initial=-np.inf)
    ll = _windows(c["low"], period).min(axis=1, initial=np.inf)
    diff = hh - ll
    with np.errstate(invalid="ignore", divide="ignore"):
        willr = np.where(diff != 0.0, (hh - c["close"][period - 1 :]) / diff * -100.0, 0.0)
    return (_pad(willr, len(c["close"])),)


def _mfi(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
    tp = _typical(c)
    diff = np.diff(tp)
    flow = (tp * c["volume"])[1:]
    pos = _windows(np.where(diff > 0, flow, 0.0), p["timeperiod"]).sum(axis=1)
    neg = _windows(np.where(diff < 0, flow, 0.0), p["timeperiod"]).sum(axis=1)
    total = pos + neg
    with np.errstate(invalid="ignore", divide="ignore"):
        mfi = np.where(total < 1.0, 0.0, 100.0 * (pos / total))
    return (_pad(mfi, len(tp)),)


def _macd(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
    fast, slow = p["fastperiod"], p["slowperiod"]
    macd = _ema(c["close"], fast, skip=slow - fast) - _ema(c["close"], slow)
    signal = _pad(_ema(macd[slow - 1 :], p["signalperiod"]), len(macd))
    macd = np.where(np.isnan(signal), np.nan, macd)
    return macd, signal, macd - signal


def _bbands(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
    n = len(c["close"])
    win = _windows(c["close"], p["timeperiod"])
    mid = win.mean(axis=1)
    band = 2.0 * np.sqrt(((win - mid[:, None]) ** 2).mean(axis=1))
    return _pad(mid + band, n), _pad(mid, n), _pad(mid - band, n)


def _stoch(c: dict[str, FloatArray], p: dict[str, int]) -> tuple[FloatArray, ...]:
    n, fk = len(c["close"]), p["fastk_period"]
    hh = _windows(c["high"], fk).max(axis=1, initial=-np.inf)
    ll = _windows(c["low"], fk).min(axis=1, initial=np.inf)
    diff = hh - ll
    with np.errstate(invalid="ignore", divide="ignore"):
        fastk = np.where(diff != 0.0, (c["close"][fk - 1 :] - ll) / diff * 100.0, 0.0)
    slowk = _windows(fastk, p["slowk_period"]).mean(axis=1)
    slowd = _pad(_windows(slowk, p["slowd_period"]).mean(axis=1), n)
    return np.where(np.isnan(slowd), np.nan, _pad(slowk, n)), slowd


def _obv(c: dict[str, FloatArray], _p: dict[str, int]) -> tuple[FloatArray, ...]:
    close, volume = c["close"], c["volume"]
    diff = np.diff(close)
    signed = np.concatenate(
        ([volume[0]], np.where(diff > 0, volume[1:], np.where(diff < 0, -volume[1:], 0.0)))
    )
    return (np.cumsum(signed),)


_KERNELS: dict[str, _Kernel] = {
    "SMA": _sma, "EMA": _ema_kernel, "RSI": _rsi, "ATR": _atr, "CCI": _cci,
    "WILLR": _willr, "MFI": _mfi, "MACD": _macd, "BBANDS": _bbands,
    "STOCH": _stoch, "OBV": _obv,
}  # fmt: skip


def _as_columns(inputs: tuple[str, ...], columns: Columns) -> dict[str, FloatArray]:
    arrays: dict[str, FloatArray] = {}
    for key in inputs:
        if key not in columns:
            raise IndicatorError("INDICATOR_INPUT_INVALID")
        try:
            arr = np.asarray(columns[key], dtype=np.float64)
        except (TypeError, ValueError):
            raise IndicatorError("INDICATOR_INPUT_INVALID") from None
        if arr.ndim != 1 or not np.all(np.isfinite(arr)):
            raise IndicatorError("INDICATOR_INPUT_INVALID")
        arrays[key] = arr
    if len({len(a) for a in arrays.values()}) > 1 or not len(next(iter(arrays.values()))):
        raise IndicatorError("INDICATOR_INPUT_INVALID")
    return arrays


def _leading_nan(arr: FloatArray) -> int:
    finite = np.flatnonzero(~np.isnan(arr))
    return int(finite[0]) if len(finite) else len(arr)


def compute(
    name: str,
    columns: Columns,
    params: Mapping[str, int] | None = None,
    registry: IndicatorRegistry = DEFAULT_REGISTRY,
) -> dict[str, FloatArray]:
    """Columns → float64 arrays per output (length n). Leading lookback NaNs, rest all finite."""
    spec, resolved, lookback = resolve_request(name, params, registry)
    kernel = _KERNELS.get(name)
    if kernel is None:
        raise IndicatorError("STRATEGY_INDICATOR_UNKNOWN")
    arrays = _as_columns(spec.inputs, columns)
    n = len(next(iter(arrays.values())))
    outputs = kernel(arrays, resolved)
    expected = min(lookback, n)
    for arr in outputs:
        if len(arr) != n or _leading_nan(arr) != expected or np.isnan(arr[expected:]).any():
            raise IndicatorError("INDICATOR_LOOKBACK_MISMATCH")
    return dict(zip(spec.outputs, outputs, strict=True))


def run_incremental(
    name: str,
    columns: Columns,
    params: Mapping[str, int] | None = None,
    registry: IndicatorRegistry = DEFAULT_REGISTRY,
) -> dict[str, FloatArray]:
    """Feed same columns bar-by-bar into incremental engine; collect results in compute() format."""
    indicator = IncrementalIndicator(name, params, registry)
    arrays = _as_columns(indicator.spec.inputs, columns)
    n = len(next(iter(arrays.values())))
    collected: dict[str, FloatArray] = {key: np.full(n, np.nan) for key in indicator.spec.outputs}
    for i in range(n):
        result = indicator.update({key: float(arr[i]) for key, arr in arrays.items()})
        for key, value in result.items():
            if value is not None:
                collected[key][i] = value
    return collected


def check_equivalence(
    name: str,
    columns: Columns,
    params: Mapping[str, int] | None = None,
    registry: IndicatorRegistry = DEFAULT_REGISTRY,
    tolerance: float = EQUIVALENCE_TOLERANCE,
) -> float:
    """Incremental == batch contract. Violation raises INDICATOR_ENGINE_MISMATCH; pass
    returns max scale deviation."""
    batch = compute(name, columns, params, registry)
    streamed = run_incremental(name, columns, params, registry)
    worst = 0.0
    for key, a in batch.items():
        b = streamed[key]
        if not np.array_equal(np.isnan(a), np.isnan(b)):
            raise IndicatorError("INDICATOR_ENGINE_MISMATCH")
        finite = ~np.isnan(a)
        if finite.any():
            scale = np.maximum(1.0, np.maximum(np.abs(a[finite]), np.abs(b[finite])))
            worst = max(worst, float(np.max(np.abs(a[finite] - b[finite]) / scale)))
    if worst > tolerance:
        raise IndicatorError("INDICATOR_ENGINE_MISMATCH")
    return worst
