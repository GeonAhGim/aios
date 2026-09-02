"""LC-3 — hash_chain 단위 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§9 LC-3
("변조 감지: entry 1건 수정 시 체인 검증 실패").
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import JournalEntryView, LedgerEventType, PostingLine, Side
from src.foundation.ledger.domain import hash_chain


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def _lines() -> list[PostingLine]:
    return [
        PostingLine(
            line_no=1,
            account_code="PLATFORM:CASH_CLEARING",
            side=Side.DEBIT,
            amount=Decimal("100.00"),
            currency=Currency.KRW,
        ),
        PostingLine(
            line_no=2,
            account_code=f"USER:{uuid4()}:AVAILABLE",
            side=Side.CREDIT,
            amount=Decimal("100.00"),
            currency=Currency.KRW,
        ),
    ]


def _entry(
    *, seq: int, prev_hash: str | None, lines: list[PostingLine] | None = None
) -> JournalEntryView:
    ls = lines if lines is not None else _lines()
    digest = hash_chain.lines_digest(ls)
    posted_at = _now()
    h = hash_chain.entry_hash(
        prev_hash, seq, LedgerEventType.TOPUP_CONFIRMED, f"topup:{seq}", digest, posted_at
    )
    return JournalEntryView(
        entry_id=uuid4(),
        sequence_no=seq,
        event_type=LedgerEventType.TOPUP_CONFIRMED,
        event_ref=f"topup:{seq}",
        idempotency_key=f"TOPUP_CONFIRMED:topup:{seq}",
        lines=ls,
        lines_digest=digest,
        prev_hash=prev_hash,
        entry_hash=h,
        audit_event_id=uuid4(),
        posted_at=posted_at,
    )


def test_lines_digest_is_order_independent() -> None:
    lines = _lines()
    reversed_lines = list(reversed(lines))
    assert hash_chain.lines_digest(lines) == hash_chain.lines_digest(reversed_lines)


def test_lines_digest_changes_with_amount() -> None:
    lines = _lines()
    tampered = [lines[0].model_copy(update={"amount": Decimal("999.00")}), lines[1]]
    assert hash_chain.lines_digest(lines) != hash_chain.lines_digest(tampered)


def test_verify_chain_accepts_untouched_chain() -> None:
    e1 = _entry(seq=1, prev_hash=None)
    e2 = _entry(seq=2, prev_hash=e1.entry_hash)
    hash_chain.verify_chain([e1, e2])


def test_verify_chain_rejects_tampered_line_amount() -> None:
    """entry 1건의 행 하나를 수정하면(변조) 체인 검증이 실패해야 한다."""
    e1 = _entry(seq=1, prev_hash=None)
    e2 = _entry(seq=2, prev_hash=e1.entry_hash)
    tampered_lines = [e1.lines[0].model_copy(update={"amount": Decimal("999.00")}), e1.lines[1]]
    tampered_e1 = e1.model_copy(update={"lines": tampered_lines})

    with pytest.raises(hash_chain.ChainIntegrityError):
        hash_chain.verify_chain([tampered_e1, e2])


def test_verify_chain_rejects_forged_prev_hash() -> None:
    """prev_hash 위조: e2.prev_hash가 e1.entry_hash와 다르면 체인이 끊긴다."""
    e1 = _entry(seq=1, prev_hash=None)
    e2 = _entry(seq=2, prev_hash="f" * 64)  # 실제 e1.entry_hash가 아닌 위조 값

    with pytest.raises(hash_chain.ChainIntegrityError):
        hash_chain.verify_chain([e1, e2])


def test_verify_chain_rejects_forged_entry_hash() -> None:
    e1 = _entry(seq=1, prev_hash=None)
    forged_e1 = e1.model_copy(update={"entry_hash": "0" * 64})

    with pytest.raises(hash_chain.ChainIntegrityError):
        hash_chain.verify_chain([forged_e1])
