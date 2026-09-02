"""LC-3 — 원장 이벤트 멱등성.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3, §4.3, §9 LC-3.

멱등키 형식은 `{event_type}:{event_ref}`(§4.3 "idempotency_key 유일, 재전송은
digest 동일해야"). 같은 키로 재전송된 이벤트가 이전과 다른 내용(금액·통화·
당사자 등)이면 `LEDGER_IDEMPOTENCY_DIGEST_MISMATCH` — 호출자 버그이므로
재시도 불가(§3.3 에러 taxonomy). 순수 함수만 — I/O·시계 직접 호출 금지.
"""
from __future__ import annotations

import hashlib

from src.foundation.ledger.contracts.v1 import LedgerEvent
from src.foundation.ledger.domain.hash_chain import canonical_json


def idempotency_key(event: LedgerEvent) -> str:
    """`{event_type}:{event_ref}` — 전역 UNIQUE 제약의 대상(§4.3)."""
    return f"{event.event_type.value}:{event.event_ref}"


def event_digest(event: LedgerEvent) -> str:
    """재전송 시 비교할 이벤트 내용 다이제스트. `idempotency_key`에 쓰이지
    않는 필드(금액·통화·당사자·extra)까지 모두 담아 "같은 키, 다른 내용"을
    잡아낸다."""
    canonical = {
        "event_type": event.event_type.value,
        "event_ref": event.event_ref,
        "amount": str(event.amount),
        "currency": event.currency.value,
        "parties": {k: str(v) for k, v in event.parties.items()},
        "extra": {k: str(v) for k, v in event.extra.items()},
    }
    return hashlib.sha256(canonical_json(canonical).encode("utf-8")).hexdigest()


class IdempotencyDigestMismatchError(Exception):
    """`LEDGER_IDEMPOTENCY_DIGEST_MISMATCH` — 같은 `idempotency_key`가 이전과
    다른 내용으로 재전송됐다. 재시도 불가, 409 + 감사 DENIED로 매핑."""

    def __init__(self, key: str) -> None:
        super().__init__(f"idempotency_key={key!r}: 재전송 다이제스트가 기존과 다릅니다.")
        self.key = key


def assert_same_digest(key: str, existing: str, new: str) -> None:
    """같은 `key`로 두 번째 이벤트가 들어왔을 때 내용이 같은지 확인한다.
    다르면 `IdempotencyDigestMismatchError`(재시도 불가)."""
    if existing != new:
        raise IdempotencyDigestMismatchError(key)
