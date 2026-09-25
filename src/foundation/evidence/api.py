"""Evidence 애그리게잇의 top-level 재노출 모듈 — task-5415.

`contracts/v1.py`는 107번(계약 버저닝) 관례상 domain을 import하지 않는 순수
pydantic 재선언이라, 다른 bounded context가 실제 domain 타입(`AuditEvent`)·
adapter 클래스(`PostgresAuditEventRepository`)·순수 함수
(`assert_safe_payload`/`compute_payload_hash`)를 그대로 재사용해야 하는
자리(같은 DB 트랜잭션에 `append_event_in`을 참여시키는 호출부 등)에는 맞지
않는다. 이 파일이 그 자리를 메운다 — `evidence.domain`/`evidence.adapters`
내부 경로 대신 이 모듈을 통해서만 다른 애그리게잇이 접근한다
(import-linter `boundary:foundation-aggregates`, task-5404/5415).

같은 애그리게잇 내부에서 domain/adapters를 import하는 것은 경계 위반이
아니므로(교차 애그리게잇일 때만 검사됨) 여기서는 그대로 재노출만 한다 —
객체 identity가 그대로 유지되어 런타임 동작은 바뀌지 않는다.
"""
from __future__ import annotations

from src.foundation.evidence.adapters.postgres_repository import PostgresAuditEventRepository
from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome
from src.foundation.evidence.domain.rules import (
    UnsafePayloadError,
    assert_safe_payload,
    compute_payload_hash,
)

__all__ = [
    "AuditEvent",
    "Classification",
    "Outcome",
    "PostgresAuditEventRepository",
    "UnsafePayloadError",
    "assert_safe_payload",
    "compute_payload_hash",
]
