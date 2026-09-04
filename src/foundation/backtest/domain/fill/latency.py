"""BT-4 — 체결 지연 모델(순수).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-4, §3.4(`latency_ms`).

주문 제출 시각에 `latency_ms`를 더해 "실제로 시장에 도달하는 시각"을
계산하고, 그 시각 이후 처음 열리는 bar를 체결 대상으로 고른다. bar
목록·제출 시각은 전부 호출자가 주입한다(시계 접근 없음 — 순수).
"""
from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta


def _require_utc(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name}는 tz-aware UTC datetime이어야 한다: {value}")


def _reject_negative_latency(latency_ms: int) -> None:
    if latency_ms < 0:
        raise ValueError(f"latency_ms는 음수를 허용하지 않는다: {latency_ms}")


def delayed_arrival_time(*, submitted_at: datetime, latency_ms: int) -> datetime:
    """주문이 지연을 거쳐 실제로 시장에 도달하는 시각."""

    _require_utc(submitted_at, "submitted_at")
    _reject_negative_latency(latency_ms)
    return submitted_at + timedelta(milliseconds=latency_ms)


def resolve_execution_bar_index(
    *, submitted_at: datetime, latency_ms: int, bar_open_times: Sequence[datetime]
) -> int:
    """지연 도달 시각 이후 처음 열리는 bar의 인덱스를 반환한다.

    look-ahead 금지 — 도달 시각과 정확히 같은 시각에 열리는 bar도 아직
    그 bar의 정보를 쓸 수 없으므로 그 다음 bar까지 기다린다(엄격 초과
    `>`, `domain/rules.py`의 `is_look_ahead_safe`와 같은 원칙).
    """

    arrival = delayed_arrival_time(submitted_at=submitted_at, latency_ms=latency_ms)
    for index, open_time in enumerate(bar_open_times):
        _require_utc(open_time, f"bar_open_times[{index}]")
        if open_time > arrival:
            return index
    raise LookupError("도달 시각 이후에 열리는 bar가 없다 — 데이터 범위 밖")
