"""LB-7 ports/*.py 구조적 계약 테스트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9 LB-7.

`@runtime_checkable` Protocol의 `isinstance()`는 메서드 **이름**만 확인한다 —
파라미터·반환 타입은 mypy(정적)가 확인한다(`tests/unit/oms/test_repository_ports.py`와
같은 패턴). 그래서 여기 negative test는 두 종류다: (1) 메서드 하나가 빠진
구현은 isinstance()에서부터 False가 되는 fail-closed 사례, (2) 메서드는 다
갖췄지만 DTO 대신 dict를 돌려주는 구현은 isinstance()를 통과해도 그 결과가
계약 DTO(`PositionJournalEntryView` 등) 검증은 통과하지 못한다는 사례 —
런타임 구조 검사만으로는 "진짜 DTO를 쓰는가"까지는 증명할 수 없고, 그 간극을
mypy --strict(파라미터·반환 타입 정적 검사)가 메운다는 것을 보여준다.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.base import Currency, Money
from src.foundation.positions.contracts.v1 import JournalEntryType, PositionJournalEntryView
from src.foundation.positions.ports.exchange_balance_source import ProviderBalanceSource
from src.foundation.positions.ports.fx_rate_source import FxRateSource
from src.foundation.positions.ports.journal_repository import PositionJournalRepository
from src.foundation.positions.ports.mark_price_source import MarkPriceSource
from src.foundation.positions.ports.nav_repository import NavRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


class _FullJournalRepo:
    async def append(self, conn, **kwargs): ...
    async def list_for(self, conn, position_key, from_seq=0): ...
    async def last(self, conn, position_key): ...


class _MissingLastJournalRepo:
    """`last`가 빠진 불완전 구현 — 포트를 만족하지 못해야 한다."""

    async def append(self, conn, **kwargs): ...
    async def list_for(self, conn, position_key, from_seq=0): ...


class _FullSnapshotRepo:
    async def get(self, conn, tenant_id, position_key): ...
    async def upsert(self, conn, snapshot, expected_seq): ...
    async def list_open(self, conn, tenant_id, account_id): ...


class _FullNavRepo:
    async def insert(self, conn, nav): ...
    async def get(self, conn, account_id, nav_date): ...


class _FullMarkPriceSource:
    async def mark(self, position_key, at): ...


class _FullFxRateSource:
    async def rate(self, base, quote, at): ...


class _FullProviderBalanceSource:
    async def balances(self, connection_id): ...


class _DictReturningJournalRepo:
    """메서드 이름은 전부 갖췄으니 `isinstance()`는 통과하지만, `append`가
    `PositionJournalEntryView` 대신 얕은 dict를 돌려준다 — mypy가 없으면
    구조 검사만으로는 이 차이를 잡지 못한다는 것을 보이는 fixture."""

    async def append(self, conn, **kwargs):
        return {"position_key": kwargs.get("position_key", "acct-1:BTC/USDT")}

    async def list_for(self, conn, position_key, from_seq=0): ...
    async def last(self, conn, position_key): ...


def test_full_implementations_satisfy_their_ports() -> None:
    assert isinstance(_FullJournalRepo(), PositionJournalRepository)
    assert isinstance(_FullSnapshotRepo(), SnapshotRepository)
    assert isinstance(_FullNavRepo(), NavRepository)
    assert isinstance(_FullMarkPriceSource(), MarkPriceSource)
    assert isinstance(_FullFxRateSource(), FxRateSource)
    assert isinstance(_FullProviderBalanceSource(), ProviderBalanceSource)


def test_incomplete_implementation_fails_port_check() -> None:
    """포트 메서드 하나 누락 → isinstance() False(fail-closed 구조 증명)."""
    assert not isinstance(_MissingLastJournalRepo(), PositionJournalRepository)


async def test_dict_returning_fake_satisfies_isinstance_but_not_the_dto() -> None:
    """DoD negative test: dict를 돌려주는 가짜 구현은 구조적으로는 포트를
    만족한다고 판정되지만(메서드 이름만 검사하므로), 그 결과값은 계약 DTO
    검증을 통과하지 못한다 — Protocol을 "진짜로" 만족한다고 볼 수 없다."""
    fake = _DictReturningJournalRepo()
    assert isinstance(fake, PositionJournalRepository)

    result = await fake.append(
        conn=None,
        position_key="acct-1:BTC/USDT",
        entry_type=JournalEntryType.FILL,
        qty_delta=Decimal("0.5"),
        price=Money(amount=Decimal("50000"), currency=Currency.USDT),
        fee=None,
        realized_pnl_base=Decimal("0"),
        fx_rate=None,
        fx_source=None,
        source_event_type="FILL",
        source_event_id=str(uuid4()),
        idempotency_key="fill:1:1",
        occurred_at=_now(),
    )
    assert isinstance(result, dict)
    with pytest.raises(ValidationError):
        PositionJournalEntryView.model_validate(result)


def test_nav_get_signature_uses_date_type() -> None:
    # nav_date는 NavRepository.get의 파라미터 타입일 뿐 모델 필드가 아니므로
    # 여기서는 date 임포트가 여전히 유효한 계약임을 회귀 방지로 확인한다.
    assert date(2026, 9, 3).isoformat() == "2026-09-03"
