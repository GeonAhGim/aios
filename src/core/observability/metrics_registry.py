"""프로세스 내 counter/gauge/histogram 레지스트리 + Prometheus 텍스트 노출 형식 직렬화.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 L0-1 (108번 관측성).
I/O 없음 — `/metrics` 라우터(L0-5)가 `render_text()`를 그대로 응답 본문으로 반환한다.
미검증: 실제 Prometheus 서버의 스크레이핑 동작은 확인하지 않았다.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field

LabelNames = tuple[str, ...]
LabelValues = tuple[str, ...]


def _validate_labels(label_names: LabelNames, labels: dict[str, str]) -> LabelValues:
    if set(labels.keys()) != set(label_names):
        raise ValueError(
            f"label keys mismatch: expected {sorted(label_names)}, got {sorted(labels.keys())}"
        )
    return tuple(labels[name] for name in label_names)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_labels(label_names: LabelNames, values: LabelValues) -> str:
    if not label_names:
        return ""
    pairs = ",".join(f'{n}="{_escape(v)}"' for n, v in zip(label_names, values, strict=True))
    return "{" + pairs + "}"


def _format_value(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(float(value))


@dataclass
class Counter:
    name: str
    label_names: LabelNames
    _values: dict[LabelValues, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        if amount < 0:
            raise ValueError("Counter.inc amount must be >= 0")
        key = _validate_labels(self.label_names, labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def samples(self) -> dict[LabelValues, float]:
        with self._lock:
            return dict(self._values)


@dataclass
class Gauge:
    name: str
    label_names: LabelNames
    _values: dict[LabelValues, float] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set(self, value: float, **labels: str) -> None:
        key = _validate_labels(self.label_names, labels)
        with self._lock:
            self._values[key] = value

    def inc(self, amount: float = 1.0, **labels: str) -> None:
        key = _validate_labels(self.label_names, labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(self, amount: float = 1.0, **labels: str) -> None:
        self.inc(-amount, **labels)

    def samples(self) -> dict[LabelValues, float]:
        with self._lock:
            return dict(self._values)


@dataclass
class Histogram:
    name: str
    buckets: tuple[float, ...]
    label_names: LabelNames
    _counts: dict[LabelValues, list[int]] = field(default_factory=dict)  # 누적(le 의미론)
    _sum: dict[LabelValues, float] = field(default_factory=dict)
    _total: dict[LabelValues, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def observe(self, value: float, **labels: str) -> None:
        key = _validate_labels(self.label_names, labels)
        with self._lock:
            counts = self._counts.setdefault(key, [0] * len(self.buckets))
            for i, upper in enumerate(self.buckets):
                if value <= upper:
                    counts[i] += 1
            self._sum[key] = self._sum.get(key, 0.0) + value
            self._total[key] = self._total.get(key, 0) + 1

    def samples(self) -> dict[LabelValues, tuple[list[int], float, int]]:
        with self._lock:
            return {k: (list(v), self._sum[k], self._total[k]) for k, v in self._counts.items()}


def _check_labels(kind: str, name: str, existing: LabelNames, requested: LabelNames) -> None:
    if existing != requested:
        raise ValueError(
            f"{kind} {name!r} already registered with labels {existing}, got {requested}"
        )


class MetricsRegistry:
    """counter/gauge/histogram을 이름으로 등록·재사용하는 프로세스 내 레지스트리.

    같은 이름을 다른 라벨 집합(히스토그램은 버킷도)으로 재등록하면 ValueError.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}

    def counter(self, name: str, labels: tuple[str, ...] = ()) -> Counter:
        label_names = tuple(labels)
        with self._lock:
            existing = self._counters.get(name)
            if existing is None:
                existing = self._counters[name] = Counter(name, label_names)
            else:
                _check_labels("counter", name, existing.label_names, label_names)
            return existing

    def gauge(self, name: str, labels: tuple[str, ...] = ()) -> Gauge:
        label_names = tuple(labels)
        with self._lock:
            existing = self._gauges.get(name)
            if existing is None:
                existing = self._gauges[name] = Gauge(name, label_names)
            else:
                _check_labels("gauge", name, existing.label_names, label_names)
            return existing

    def histogram(
        self, name: str, buckets: tuple[float, ...], labels: tuple[str, ...] = ()
    ) -> Histogram:
        label_names = tuple(labels)
        bucket_values = tuple(buckets)
        with self._lock:
            existing = self._histograms.get(name)
            if existing is None:
                existing = self._histograms[name] = Histogram(name, bucket_values, label_names)
            else:
                _check_labels("histogram", name, existing.label_names, label_names)
                if existing.buckets != bucket_values:
                    raise ValueError(
                        f"histogram {name!r} already registered with buckets "
                        f"{existing.buckets}, got {bucket_values}"
                    )
            return existing

    def render_text(self) -> str:
        lines: list[str] = []
        for counter in self._counters.values():
            lines.append(f"# TYPE {counter.name} counter")
            for label_values, value in sorted(counter.samples().items()):
                label_str = _format_labels(counter.label_names, label_values)
                lines.append(f"{counter.name}{label_str} {_format_value(value)}")
        for gauge in self._gauges.values():
            lines.append(f"# TYPE {gauge.name} gauge")
            for label_values, value in sorted(gauge.samples().items()):
                label_str = _format_labels(gauge.label_names, label_values)
                lines.append(f"{gauge.name}{label_str} {_format_value(value)}")
        for hist in self._histograms.values():
            lines.append(f"# TYPE {hist.name} histogram")
            for label_values, (counts, total_sum, total_count) in sorted(
                hist.samples().items()
            ):
                bucket_label_names = hist.label_names + ("le",)
                for upper, cumulative in zip(hist.buckets, counts, strict=True):
                    bound = _format_value(upper)
                    label_str = _format_labels(bucket_label_names, label_values + (bound,))
                    lines.append(f"{hist.name}_bucket{label_str} {cumulative}")
                inf_label_str = _format_labels(bucket_label_names, label_values + ("+Inf",))
                lines.append(f"{hist.name}_bucket{inf_label_str} {total_count}")
                sum_label_str = _format_labels(hist.label_names, label_values)
                lines.append(f"{hist.name}_sum{sum_label_str} {_format_value(total_sum)}")
                lines.append(f"{hist.name}_count{sum_label_str} {total_count}")
        return "\n".join(lines) + ("\n" if lines else "")


_default_registry = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    return _default_registry
