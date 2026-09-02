"""LC-3 — idempotency 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-3
("idempotency_key 결정적 생성", negative: "같은 이벤트 두 번의 키 불일치").
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import LedgerEvent, LedgerEventType
from src.foundation.ledger.domain import idempotency


def _event(**overrides: object) -> LedgerEvent:
    base: dict[str, object] = dict(
        event_type=LedgerEventType.TOPUP_CONFIRMED,
        event_ref="topup:1",
        tenant_id=uuid4(),
        actor_subject_id=uuid4(),
        trace_id=uuid4(),
        amount=Decimal("1000"),
        currency=Currency.KRW,
        parties={"user": uuid4()},
    )
    base.update(overrides)
    return LedgerEvent(**base)  # type: ignore[arg-type]


def test_idempotency_key_is_deterministic() -> None:
    event = _event()
    assert idempotency.idempotency_key(event) == idempotency.idempotency_key(event)
    assert idempotency.idempotency_key(event) == "TOPUP_CONFIRMED:topup:1"


def test_idempotency_key_ignores_amount_and_trace() -> None:
    """같은 (event_type, event_ref)면 금액·trace_id가 달라도 키는 같다 —
    그래서 재전송 판별은 `event_digest` 비교로 넘어간다."""
    a = _event(amount=Decimal("1000"), trace_id=uuid4())
    b = _event(amount=Decimal("2000"), trace_id=uuid4())
    assert idempotency.idempotency_key(a) == idempotency.idempotency_key(b)


def test_event_digest_is_deterministic() -> None:
    event = _event()
    assert idempotency.event_digest(event) == idempotency.event_digest(event)


def test_event_digest_differs_by_amount() -> None:
    a = _event(amount=Decimal("1000"))
    b = _event(amount=Decimal("2000"))
    assert idempotency.event_digest(a) != idempotency.event_digest(b)


def test_assert_same_digest_accepts_matching_replay() -> None:
    event = _event()
    digest = idempotency.event_digest(event)
    idempotency.assert_same_digest(idempotency.idempotency_key(event), digest, digest)


def test_assert_same_digest_rejects_mismatch_on_replay() -> None:
    """같은 이벤트(같은 idempotency_key)가 두 번째로 다른 내용으로 오면 거부."""
    first = _event(amount=Decimal("1000"))
    second = _event(amount=Decimal("2000"))
    key = idempotency.idempotency_key(first)
    assert key == idempotency.idempotency_key(second)

    with pytest.raises(idempotency.IdempotencyDigestMismatchError):
        idempotency.assert_same_digest(
            key, idempotency.event_digest(first), idempotency.event_digest(second)
        )
