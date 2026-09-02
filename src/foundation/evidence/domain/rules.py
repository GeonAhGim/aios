"""Audit Event 순수 규칙 함수 — DB/HTTP 없이 단위 테스트 가능해야 한다.

Spec: AIOSproject 79번 §1(해시 체인)/§2(payload 안전성)/§4(에러 taxonomy).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from src.foundation.evidence.domain.models import AuditEvent, Classification, Outcome

_UNSAFE_KEY_PATTERN = re.compile(
    r"(secret|token|password|passwd|private[_-]?key|api[_-]?key|credential)", re.IGNORECASE
)
"""79번 §2 "serializer rejects key names matching secret/token/password/
private-key patterns"(AUD-004). 값이 아니라 **키 이름**을 검사한다 — 호출자가
opaque ref를 넣었는지 원문을 넣었는지까지는 이 함수가 판단할 수 없지만, 최소
한 필드 이름 자체가 위험 신호면 구조적으로 막는다(108번 §2.1과 동일 원칙,
런타임 실행 경로에서도 강제)."""


class UnsafePayloadError(Exception):
    """payload에 secret/token/password류로 보이는 키가 있다 — 호출자는 그
    필드를 opaque `*_ref`로 바꿔서 다시 시도해야 한다."""


def assert_safe_payload(payload: dict[str, Any], *, _path: str = "") -> None:
    for key, value in payload.items():
        full_key = f"{_path}.{key}" if _path else key
        if _UNSAFE_KEY_PATTERN.search(key):
            raise UnsafePayloadError(
                f"payload 필드 '{full_key}'는 secret/token/password류 이름이라 "
                "감사 이벤트에 직접 담을 수 없습니다 — opaque ref로 바꾸세요."
            )
        if isinstance(value, dict):
            assert_safe_payload(value, _path=full_key)


def compute_payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_event_hash(
    *,
    previous_hash: str | None,
    tenant_id: UUID | None,
    sequence_no: int,
    aggregate_type: str,
    aggregate_id: UUID,
    action: str,
    outcome: Outcome,
    payload_hash: str,
    classification: Classification,
    occurred_at: datetime,
) -> str:
    """79번 §1 해시 체인의 링크 하나. `previous_hash`가 None이면(그 tenant의
    첫 이벤트) 빈 문자열로 취급해 체인이 항상 결정론적으로 시작하게 한다."""
    payload = "|".join(
        [
            previous_hash or "",
            str(tenant_id) if tenant_id is not None else "system",
            str(sequence_no),
            aggregate_type,
            str(aggregate_id),
            action,
            outcome.value,
            payload_hash,
            classification.value,
            occurred_at.isoformat(),
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ChainIntegrityError(Exception):
    """79번 §4 `INTEGRITY_AUDIT_CHAIN_BROKEN` — 변조되거나 빠진 구간을
    발견했다. `detail`에 어느 sequence_no에서 깨졌는지 남긴다."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def verify_chain(events: list[AuditEvent]) -> None:
    """AUD-003 — sequence_no 순으로 정렬된 한 tenant(또는 system)의 이벤트
    목록이 진짜 체인인지 확인한다. 문제가 없으면 조용히 반환하고, 문제가
    있으면 ChainIntegrityError를 던진다(호출자가 209/500류로 매핑)."""
    expected_previous: str | None = None
    for event in events:
        if event.previous_hash != expected_previous:
            raise ChainIntegrityError(
                f"sequence_no={event.sequence_no}: previous_hash가 이전 이벤트의 "
                "event_hash와 일치하지 않습니다(체인 단절 또는 변조)."
            )
        recomputed = compute_event_hash(
            previous_hash=event.previous_hash,
            tenant_id=event.tenant_id,
            sequence_no=event.sequence_no,
            aggregate_type=event.aggregate_type,
            aggregate_id=event.aggregate_id,
            action=event.action,
            outcome=event.outcome,
            payload_hash=event.payload_hash,
            classification=event.classification,
            occurred_at=event.occurred_at,  # type: ignore[arg-type]
        )
        if recomputed != event.event_hash:
            raise ChainIntegrityError(
                f"sequence_no={event.sequence_no}: event_hash가 필드로부터 재계산한 "
                "값과 다릅니다(내용 변조 의심)."
            )
        expected_previous = event.event_hash
