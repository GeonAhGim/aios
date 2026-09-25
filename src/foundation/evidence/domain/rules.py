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
"""AUD-004: serializer rejects key names matching secret/token/password/
private-key patterns (§2). Validates **key names**, not values — this function
cannot tell whether the caller passed an opaque ref or the original text, but
blocks structurally if the field name itself is a risk signal (same principle
as §2.1 of spec 108, also enforced at runtime execution paths)."""


class UnsafePayloadError(Exception):
    """Payload contains a key that looks like secret/token/password — the caller
    must replace that field with an opaque `*_ref` and retry."""


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
    """79 §1: one link in the hash chain. If `previous_hash` is None (first event
    for that tenant), treat it as empty string so the chain always starts
    deterministically."""
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
    """79 §4 `INTEGRITY_AUDIT_CHAIN_BROKEN` — detected tampered or missing
    segment. Records which sequence_no broke in `detail`."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def verify_chain(events: list[AuditEvent]) -> None:
    """AUD-003 — verify whether a tenant's (or system's) event list sorted by
    sequence_no forms a valid chain. Silently returns if OK, raises
    ChainIntegrityError if broken (caller maps to 209/500-class codes)."""
    expected_previous: str | None = None
    for event in events:
        if event.previous_hash != expected_previous:
            raise ChainIntegrityError(
                f"sequence_no={event.sequence_no}: previous_hash가 이전 이벤트의 "
                "event_hash와 일치하지 않습니다(체인 단절 또는 변조)."
            )
        if event.occurred_at is None:
            raise ChainIntegrityError(
                f"sequence_no={event.sequence_no}: occurred_at이 비어 있어 해시를 "
                "재계산할 수 없습니다(체인 단절 또는 변조)."
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
            occurred_at=event.occurred_at,
        )
        if recomputed != event.event_hash:
            raise ChainIntegrityError(
                f"sequence_no={event.sequence_no}: event_hash가 필드로부터 재계산한 "
                "값과 다릅니다(내용 변조 의심)."
            )
        expected_previous = event.event_hash
