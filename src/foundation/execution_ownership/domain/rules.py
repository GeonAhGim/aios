"""EO-01 — 실행 소유권 순수 판정 규칙.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md
§2-A, §3.2, §4.1(I-02). I/O·asyncpg 임포트 금지(SCAFFOLD zone 순수성) —
실제 획득/갱신은 EO-02 저장소 어댑터(§5.1 조건부 UPSERT)의 책임이고,
이 함수는 "이 리스를 요청해도 되는가"만 판정한다.
"""
from __future__ import annotations

from datetime import datetime

from src.foundation.execution_ownership.domain.models import ExecutionLease


def is_lease_available(
    existing: ExecutionLease | None,
    *,
    now: datetime,
    requesting_owner: str,
) -> bool:
    """리스가 없거나, 만료됐거나, 이미 요청자 본인이 쥐고 있으면 True.
    다른 소유자가 만료 전 리스를 쥐고 있으면 False(§4.1 "유효한 리스를
    가진 프로세스 하나에서만 동시에 tick된다")."""
    if now.tzinfo is None:
        raise ValueError("naive datetime은 허용하지 않는다 — tz-aware UTC만 사용한다")
    if existing is None:
        return True
    if existing.owner_id == requesting_owner:
        return True
    return existing.expires_at <= now
