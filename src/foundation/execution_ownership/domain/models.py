"""EO-01 — 실행 소유권(리스) 값 객체.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md
§2-A, §3.1. I/O·asyncpg 임포트 금지(SCAFFOLD zone 순수성) — 이 모듈은
`execution_leases` 테이블 행 하나를 표현만 하고 저장은 ports/adapters
(EO-02)의 책임이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def _require_aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("naive datetime은 허용하지 않는다 — tz-aware UTC만 사용한다")
    return value


@dataclass(frozen=True, slots=True)
class ExecutionLease:
    """`execution_leases` 테이블 행 하나(§3.1). `fencing_token`은 소유자가
    바뀔 때만 증가한다 — 이 값객체 자체는 그 규칙을 강제하지 않고
    `rules.is_lease_available`/저장소 SQL(§5.1)이 강제한다."""

    execution_id: int
    owner_id: str
    fencing_token: int
    heartbeat_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_aware_utc(self.heartbeat_at)
        _require_aware_utc(self.expires_at)
