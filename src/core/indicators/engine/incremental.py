"""IND-1 — Streaming state-based incremental indicator computation (identical replay/live results).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.3 `engine/incremental.py`,
§9.3 IND-1 (DoD: incremental = batch (`engine/vectorized.py`) results within 1e-9).

Pure module — no I/O, no numpy. Indicator lookup, parameter validation, and lookback
are delegated via `engine/__init__.py` to the L02 registry (lookback relies on L01
TA-Lib measured values as the single source of truth; re-computation is prohibited).
If the "ready" state after each bar diverges from the registry lookback,
fail-closed with `INDICATOR_LOOKBACK_MISMATCH` (self-validation that prevents the
window calculation from silently drifting from L01).

Numerical rules (both engines use the same formula for 1e-9 identity):
- Window statistics (SMA, max, min, deviation) are re-computed over the full window
  each bar — additive/subtractive accumulation can exceed 1e-9 float drift on long streams.
- EMA seed: simple average of the first `period` bars (TA-Lib default compatible),
  update: `prev + (x - prev) * k`.
- Wilder smoothing (RSI, ATR): seed with simple average of the first `period` bars,
  then `(prev * (p - 1) + x) / p`.
- MACD fast line: discard `slow - fast` bars then seed, matching TA-Lib.
- Periods before lookback is satisfied return None explicitly (never substitute 0).
- Zero-division guard uses exact 0 comparison (not TA-Lib's 1e-8 approximation).
  MFI: when total flow < 1.0, return 0, matching TA-Lib.
"""
from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable, Mapping
from typing import Protocol

from src.core.indicators.engine import Bar, Values, resolve_request, validate_input
from src.core.indicators.registry import DEFAULT_REGISTRY, IndicatorError, IndicatorRegistry

__all__ = ["IncrementalIndicator"]


class _Window:
    """Fixed-length window. Statistics are re-computed over the full window
    each bar (no cumulative drift)."""

    def __init__(self, size: int) -> None:
        self.size = size
        self._buf: deque[float] = deque(maxlen=size)

    def push(self, x: float) -> bool:
        self._buf.append(x)
        return len(self._buf) == self.size

    def total(self) -> float:
        return sum(self._buf)

    def mean(self) -> float:
        return self.total() / self.size

    def max(self) -> float:
        return max(self._buf)

    def min(self) -> float:
        return min(self._buf)

    def mean_abs_dev(self, center: float) -> float:
        return sum(abs(v - center) for v in self._buf) / self.size

    def pop_std(self, center: float) -> float:
        return math.sqrt(sum((v - center) ** 2 for v in self._buf) / self.size)


class _Ema:
    """SMA-seeded EMA. Discards `skip` bars, then fills the seed window (for MACD fast line)."""

    def __init__(self, period: int, skip: int = 0) -> None:
        self._k = 2.0 / (period + 1)
        self._seed = _Window(period)
        self._skip = skip
        self.value: float | None = None

    def push(self, x: float) -> float | None:
        if self._skip > 0:
            self._skip -= 1
            return None
        if self.value is None:
            if self._seed.push(x):
                self.value = self._seed.mean()
            return self.value
        self.value = self.value + (x - self.value) * self._k
        return self.value


class _Wilder:
    """Wilder smoothing: seed with simple average of the first `period` bars,
    then `(prev * (p - 1) + x) / p`."""

    def __init__(self, period: int) -> None:
        self._period = period
        self._seed = _Window(period)
        self.value: float | None = None

    def push(self, x: float) -> float | None:
        if self.value is None:
            if self._seed.push(x):
                self.value = self._seed.mean()
            return self.value
        self.value = (self.value * (self._period - 1) + x) / self._period
        return self.value


class _State(Protocol):
    def update(self, bar: Mapping[str, float]) -> Values | None: ...


class _Sma:
    def __init__(self, p: dict[str, int]) -> None:
        self._w = _Window(p["timeperiod"])

    def update(self, bar: Mapping[str, float]) -> Values | None:
        return (self._w.mean(),) if self._w.push(bar["close"]) else None


class _EmaState:
    def __init__(self, p: dict[str, int]) -> None:
        self._ema = _Ema(p["timeperiod"])

    def update(self, bar: Mapping[str, float]) -> Values | None:
        v = self._ema.push(bar["close"])
        return None if v is None else (v,)


class _Rsi:
    def __init__(self, p: dict[str, int]) -> None:
        self._gain, self._loss = _Wilder(p["timeperiod"]), _Wilder(p["timeperiod"])
        self._prev: float | None = None

    def update(self, bar: Mapping[str, float]) -> Values | None:
        close = bar["close"]
        if self._prev is None:
            self._prev = close
            return None
        diff, self._prev = close - self._prev, close
        gain = self._gain.push(diff if diff > 0 else 0.0)
        loss = self._loss.push(-diff if diff < 0 else 0.0)
        if gain is None or loss is None:
            return None
        total = gain + loss
        return (100.0 * (gain / total) if total != 0.0 else 0.0,)


class _Atr:
    def __init__(self, p: dict[str, int]) -> None:
        self._wilder = _Wilder(p["timeperiod"])
        self._prev_close: float | None = None

    def update(self, bar: Mapping[str, float]) -> Values | None:
        high, low, close = bar["high"], bar["low"], bar["close"]
        if self._prev_close is None:
            self._prev_close = close
            return None
        tr = max(high - low, abs(high - self._prev_close), abs(low - self._prev_close))
        self._prev_close = close
        v = self._wilder.push(tr)
        return None if v is None else (v,)


class _Cci:
    def __init__(self, p: dict[str, int]) -> None:
        self._w = _Window(p["timeperiod"])

    def update(self, bar: Mapping[str, float]) -> Values | None:
        tp = (bar["high"] + bar["low"] + bar["close"]) / 3.0
        if not self._w.push(tp):
            return None
        avg = self._w.mean()
        md, num = self._w.mean_abs_dev(avg), tp - avg
        return (num / (0.015 * md) if num != 0.0 and md != 0.0 else 0.0,)


class _Willr:
    def __init__(self, p: dict[str, int]) -> None:
        self._hi, self._lo = _Window(p["timeperiod"]), _Window(p["timeperiod"])

    def update(self, bar: Mapping[str, float]) -> Values | None:
        self._hi.push(bar["high"])
        if not self._lo.push(bar["low"]):
            return None
        hh, ll = self._hi.max(), self._lo.min()
        diff = hh - ll
        return ((hh - bar["close"]) / diff * -100.0 if diff != 0.0 else 0.0,)


class _Mfi:
    def __init__(self, p: dict[str, int]) -> None:
        self._pos, self._neg = _Window(p["timeperiod"]), _Window(p["timeperiod"])
        self._prev_tp: float | None = None

    def update(self, bar: Mapping[str, float]) -> Values | None:
        tp = (bar["high"] + bar["low"] + bar["close"]) / 3.0
        if self._prev_tp is None:
            self._prev_tp = tp
            return None
        diff, self._prev_tp = tp - self._prev_tp, tp
        flow = tp * bar["volume"]
        self._pos.push(flow if diff > 0 else 0.0)
        if not self._neg.push(flow if diff < 0 else 0.0):
            return None
        pos = self._pos.total()
        total = pos + self._neg.total()
        return (0.0 if total < 1.0 else 100.0 * (pos / total),)


class _Macd:
    def __init__(self, p: dict[str, int]) -> None:
        self._fast = _Ema(p["fastperiod"], skip=p["slowperiod"] - p["fastperiod"])
        self._slow = _Ema(p["slowperiod"])
        self._signal = _Ema(p["signalperiod"])

    def update(self, bar: Mapping[str, float]) -> Values | None:
        fast, slow = self._fast.push(bar["close"]), self._slow.push(bar["close"])
        if fast is None or slow is None:
            return None
        macd = fast - slow
        signal = self._signal.push(macd)
        return None if signal is None else (macd, signal, macd - signal)


class _Bbands:
    def __init__(self, p: dict[str, int]) -> None:
        self._w = _Window(p["timeperiod"])

    def update(self, bar: Mapping[str, float]) -> Values | None:
        if not self._w.push(bar["close"]):
            return None
        mid = self._w.mean()
        band = 2.0 * self._w.pop_std(mid)
        return (mid + band, mid, mid - band)


class _Stoch:
    def __init__(self, p: dict[str, int]) -> None:
        self._hi, self._lo = _Window(p["fastk_period"]), _Window(p["fastk_period"])
        self._k, self._d = _Window(p["slowk_period"]), _Window(p["slowd_period"])

    def update(self, bar: Mapping[str, float]) -> Values | None:
        self._hi.push(bar["high"])
        if not self._lo.push(bar["low"]):
            return None
        hh, ll = self._hi.max(), self._lo.min()
        diff = hh - ll
        fastk = (bar["close"] - ll) / diff * 100.0 if diff != 0.0 else 0.0
        if not self._k.push(fastk):
            return None
        slowk = self._k.mean()
        return (slowk, self._d.mean()) if self._d.push(slowk) else None


class _Obv:
    def __init__(self, _p: dict[str, int]) -> None:
        self._prev_close: float | None = None
        self._obv = 0.0

    def update(self, bar: Mapping[str, float]) -> Values | None:
        close, volume = bar["close"], bar["volume"]
        if self._prev_close is None:
            self._obv = volume
        elif close > self._prev_close:
            self._obv = self._obv + volume
        elif close < self._prev_close:
            self._obv = self._obv + -volume
        self._prev_close = close
        return (self._obv,)


_STATES: dict[str, Callable[[dict[str, int]], _State]] = {
    "SMA": _Sma, "EMA": _EmaState, "RSI": _Rsi, "ATR": _Atr, "CCI": _Cci,
    "WILLR": _Willr, "MFI": _Mfi, "MACD": _Macd, "BBANDS": _Bbands,
    "STOCH": _Stoch, "OBV": _Obv,
}  # fmt: skip


class IncrementalIndicator:
    """Streaming calculator that consumes bars one at a time (identical replay/live path)."""

    def __init__(
        self,
        name: str,
        params: Mapping[str, int] | None = None,
        registry: IndicatorRegistry = DEFAULT_REGISTRY,
    ) -> None:
        self.spec, self.params, self.lookback = resolve_request(name, params, registry)
        state_cls = _STATES.get(name)
        if state_cls is None:
            raise IndicatorError("STRATEGY_INDICATOR_UNKNOWN")
        self._state = state_cls(self.params)
        self.bars_seen = 0

    def update(self, bar: Bar) -> dict[str, float | None]:
        """Process the next bar and return output values.
        Returns None for outputs not yet past lookback."""
        inputs = validate_input(self.spec, bar)
        values = self._state.update(inputs)
        self.bars_seen += 1
        if (values is not None) != (self.bars_seen > self.lookback):
            raise IndicatorError("INDICATOR_LOOKBACK_MISMATCH")
        if values is None:
            return dict.fromkeys(self.spec.outputs)
        return dict(zip(self.spec.outputs, values, strict=True))
