"""LC-8a ports/*.py 구조적 계약 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.4, §9 LC-8.

`@runtime_checkable` Protocol이므로 `isinstance()`는 메서드 이름만 확인한다
(시그니처는 mypy가 정적으로 확인) — `tests/unit/oms/test_repository_ports.py`,
`tests/foundation/unit/positions/test_ports_protocol.py`와 같은 패턴. 메서드
하나라도 빠지면 어댑터는 포트를 만족하지 못한다는 것을 fail-closed로 실증한다.
"""
from __future__ import annotations

from src.foundation.ledger.ports.balance_repository import BalanceRepository
from src.foundation.ledger.ports.hold_repository import HoldRepository
from src.foundation.ledger.ports.journal_repository import LedgerJournalRepository
from src.foundation.ledger.ports.payout_repository import PayoutRepository


class _FullJournalRepo:
    async def append(self, conn, entry, lines): ...
    async def find_by_idempotency_key(self, conn, key): ...
    async def list_since(self, conn, seq): ...
    async def last(self, conn): ...


class _MissingLastJournalRepo:
    """`last`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def append(self, conn, entry, lines): ...
    async def find_by_idempotency_key(self, conn, key): ...
    async def list_since(self, conn, seq): ...


class _FullBalanceRepo:
    async def get_for_update(self, conn, account_ids): ...
    async def apply(self, conn, account_id, delta_balance, delta_held, expected_seq): ...


class _FullHoldRepo:
    async def create(self, conn, **kwargs): ...
    async def transition(self, conn, hold_id, **kwargs): ...


class _FullPayoutRepo:
    async def create_batch(self, conn, **kwargs): ...
    async def list_due(self, conn): ...
    async def mark_paid(self, conn, batch_id, **kwargs): ...


def test_full_implementations_satisfy_their_ports() -> None:
    assert isinstance(_FullJournalRepo(), LedgerJournalRepository)
    assert isinstance(_FullBalanceRepo(), BalanceRepository)
    assert isinstance(_FullHoldRepo(), HoldRepository)
    assert isinstance(_FullPayoutRepo(), PayoutRepository)


def test_incomplete_implementation_fails_port_check() -> None:
    """포트 메서드 하나 누락 → isinstance() False(fail-closed 구조 증명)."""
    assert not isinstance(_MissingLastJournalRepo(), LedgerJournalRepository)
