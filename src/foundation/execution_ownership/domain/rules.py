"""EO-01 — 실행 소유권 순수 판정 규칙.

Spec: docs/specs/L4_execution_ownership_and_safety_gate_wiring_v1.0.md
§2-A, §3.2, §4.1(I-02). I/O·asyncpg 임포트 금지(SCAFFOLD zone 순수성) —
실제 획득/갱신은 EO-02 저장소 어댑터(§5.1 조건부 UPSERT)의 책임이고,
이 함수는 "이 리스를 요청해도 되는가"만 판정한다.
"""
from __future__ import annotations

from datetime import datetime

from src.foundation.execution_ownership.domain.models import ExecutionLease, require_aware_utc


def is_lease_available(
    existing: ExecutionLease | None,
    *,
    now: datetime,
    requesting_owner: str,
) -> bool:
    """리스가 없거나, 만료됐거나, 이미 요청자 본인이 쥐고 있으면 True.
    다른 소유자가 만료 전 리스를 쥐고 있으면 False(§4.1 "유효한 리스를
    가진 프로세스 하나에서만 동시에 tick된다"). 만료 경계는 §5.1 SQL
    `expires_at < now()`와 동일하게 strict — `expires_at == now`는 아직 유효.

    전수감사 2026-09-06 P1-C(task-1722) — src 임포터 0(운영 코드에서 호출
    안 됨)이 의도된 설계다: §5.1 조건부 UPSERT(`postgres_repository.py`
    `acquire_or_renew_many`)가 이 판정을 SQL `WHERE` 절로 원자적으로
    재구현한다(체크-후-갱신 레이스를 없애려면 1왕복 SQL이어야 하고, 그
    안에서 이 Python 함수를 호출할 수 없다). 이 함수는 그 SQL이 반드시
    일치해야 할 판정 규칙의 실행 가능한 명세이며,
    `tests/foundation/unit/execution_ownership/test_rules.py`의 경계값
    테스트가 그 일치를 증명한다 — 어댑터를 호출하는 진입점이 아니다."""
    require_aware_utc(now)
    if existing is None:
        return True
    if existing.owner_id == requesting_owner:
        return True
    return existing.expires_at < now
