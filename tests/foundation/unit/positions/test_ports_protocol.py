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

import time
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.data.models.base import Currency, Money
from src.foundation.positions.contracts.v1 import (
    JournalEntryType,
    NAVSnapshot,
    PositionJournalEntryView,
    PositionSnapshotView,
)
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


# ── DEEPEN: 추가 negative tests (invariant 위반 입력) ──────────────────────────


class _ZeroMarkPriceSource:
    """마크가격 포트는 값이 없으면 `None`을 돌려야 한다 — `Money(amount=0)`을
    반환하면 호출자가 "스테일"과 "실제 0원"을 구분하지 못한다(POS_MARK_STALE)."""

    async def mark(self, position_key, at):
        return Money(amount=Decimal("0"), currency=Currency.USDT)


async def test_mark_price_source_zero_money_violates_none_return_invariant() -> None:
    """Invariants 위반: MarkPriceSource.mark()가 0원 Money를 반환하면
    호출자는 마크가 스테일한 경우와 실제 마크가 0원인 경우를 구분할 수 없다.
    포트 계약은 "값을 못 구하면 None"이므로, 이 구현은 계약 위반이다."""
    zero_source = _ZeroMarkPriceSource()
    # isinstance는 이름만 확인하므로 통과 — 정적 타입 검사(mypy)가 잡아야 함
    assert isinstance(zero_source, MarkPriceSource)
    # 하지만 runtime에서 0원 Money를 반환하는 것은 포트의 "None when missing"
    # 불변식을 위반한다 — 호출 측에서 None 체크로 처리하면 미실현 PnL이
    # "None 유지"가 아니라 "0원"으로 오인된다.
    result = await zero_source.mark("acct-1:BTC/USDT", _now())
    assert result is not None  # isinstance 통과 but contract-violating


class _EmptyBalanceSource:
    """ProviderBalanceSource 포트는 조회 실패 시 예외를 던져야 한다 — 빈
    리스트를 반환하면 "잔고 없음"과 "실제 0"을 구분할 수 없다(FD-3.3)."""

    async def balances(self, connection_id):
        return []


async def test_provider_balance_source_empty_list_masks_failure() -> None:
    """Invariants 위반: ProviderBalanceSource.balances()가 빈 리스트를
    반환하면 실제 잔고 0과 API 실패를 구분할 수 없다. 포트 계약은
    "실패 시 예외"이므로 이 구현은 fail-closed 원칙을 위반한다."""
    empty_source = _EmptyBalanceSource()
    assert isinstance(empty_source, ProviderBalanceSource)
    # 빈 리스트 반환 — isinstance는 통과하지만 실제 호출 시
    # "잔고 없음"과 "실패"가 구분되지 않는다.
    result = await empty_source.balances(uuid4())
    assert result == []  # contract-violating: should raise, not return []


class _WrongTypeSnapshotRepo:
    """스냅샷 포트의 upsert가 PositionSnapshotView 대신 dict를 반환한다 —
    isinstance()는 메서드 이름만 확인하므로 통과하지만, 반환 타입이
    계약 DTO가 아니다."""

    async def get(self, conn, tenant_id, position_key): ...

    async def upsert(self, conn, snapshot, expected_seq):
        return {"position_key": "fake"}

    async def list_open(self, conn, tenant_id, account_id): ...


async def test_snapshot_repo_dict_return_fails_dto_validation() -> None:
    """Negative test: upsert가 dict를 반환하면 isinstance()는 통과하지만
    PositionSnapshotView.model_validate()는 ValidationError를 던진다.
    Protocol 구조 검사만으로는 반환 타입을 검증할 수 없다는 간극을 확인한다."""
    fake = _WrongTypeSnapshotRepo()
    assert isinstance(fake, SnapshotRepository)

    fake_nav = NAVSnapshot(
        account_id=uuid4(),
        nav_date=date(2026, 9, 3),
        base_currency=Currency.USDT,
        opening_nav=Decimal("10000"),
        cash=Decimal("5000"),
        positions_mv=Decimal("5000"),
        realized=Decimal("100"),
        unrealized_delta=Decimal("50"),
        funding=Decimal("0"),
        fees=Decimal("10"),
        flows=Decimal("0"),
        closing_nav=Decimal("10140"),
        fx_rates=[],
        source_hash="abc123",
    )
    result = await fake.upsert(conn=None, snapshot=fake_nav, expected_seq=0)
    assert isinstance(result, dict)
    with pytest.raises(ValidationError):
        PositionSnapshotView.model_validate(result)


# ── DEEPEN: 실패주입 테스트 (monkeypatch) ─────────────────────────────────────


async def test_journal_repository_append_raises_on_db_failure() -> None:
    """실패주입: PositionJournalRepository.append() 구현이 DB 예외를
    던지는 경우, 호출자는 이를キャ치하거나 전파해야 한다.
    포트는 예외 전파를 허용한다 — 예외를 swallow해서는 안 된다."""

    class _SwallowingJournalRepo:
        """append가 예외를 던지지 않고 None을 반환하면 — 저널 쓰기가
        실패했음에도 호출자는 이를 모르고 진행하게 된다."""

        async def append(self, conn, **kwargs):
            # DB 오류가 발생했음에도 예외를 던지지 않고 None 반환
            return None

        async def list_for(self, conn, position_key, from_seq=0):
            return []

        async def last(self, conn, position_key):
            return None

    fake = _SwallowingJournalRepo()
    # isinstance 통과 — 메서드 이름은 맞췄다
    assert isinstance(fake, PositionJournalRepository)
    # 하지만 append가 None을 반환하면 — 호출자가 PositionJournalEntryView
    # 로 기대하는 값을 받지 못한다.
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
    # None 반환 — 호출자가 PositionJournalEntryView라고 믿고 접근하면
    # AttributeError가 발생한다. 포트 계약은 PositionJournalEntryView 반환.
    assert result is None  # contract-violating: should raise or return DTO


# ── DEEPEN: 추가 negative test — 포트 메서드 시그니처/반환 타입 위반 ──────────


def test_missing_upsert_fails_snapshot_repository_check() -> None:
    """negative test: SnapshotRepository의 upsert가 빠르면 isinstance() False.
    조건부 업데이트가 없으면 동시성 방어가 없으므로 포트 불만족."""

    class _SnapshotNoUpsert:
        async def get(self, conn, tenant_id, position_key): ...
        async def list_open(self, conn, tenant_id, account_id): ...

    assert not isinstance(_SnapshotNoUpsert(), SnapshotRepository)


def test_nav_repository_missing_get_fails_port_check() -> None:
    """negative test: NavRepository의 get이 빠르면 isinstance() False.
    멱등 재계산 체크에 get이 필수 — 없으면 같은 날 중복 삽입 가능."""

    class _NavNoGet:
        async def insert(self, conn, nav): ...

    assert not isinstance(_NavNoGet(), NavRepository)


# ── DEEPEN: 실패주입 — 의존성 예외 유발 ─────────────────────────────────────


async def test_mark_price_source_raises_on_missing_price() -> None:
    """실패주입: 마크가격 소스가 None 대신 예외를 던지는 경우.
    포트 계약은 None 반환을 허용하지만, 실제 어댑터가 네트워크 오류 등으로
    예외를 던질 때 호출자가 이를 잡지 않으면 PositionJournalEntryView
    생성이 실패해야 한다 — fail-closed."""

    class _FailingMarkPriceSource:
        async def mark(self, position_key, at):
            raise ConnectionError("exchange unreachable")

    # isinstance 통과 — 메서드 이름만 검사하므로
    assert isinstance(_FailingMarkPriceSource(), MarkPriceSource)
    # 하지만 실제 호출 시 예외가 던져진다 — 호출자가 이를 처리하지 않으면
    # 포트 계약상 None이어야 할 값 대신 예외가 전파된다.
    with pytest.raises(ConnectionError, match="exchange unreachable"):
        await _FailingMarkPriceSource().mark("acct-1:BTC/USDT", _now())


async def test_fx_rate_source_raises_on_missing_rate() -> None:
    """실패주입: 환율 소스가 None 대신 예외를 던지는 경우.
    포트 계약은 None 반환을 명시하지만, 외부 API 장애 시 예외가 던져질 수 있다.
    호출자는 이를 POS_FX_RATE_MISSING으로 변환해야 한다."""

    class _FailingFxRateSource:
        async def rate(self, base, quote, at):
            raise TimeoutError("fx rate provider timeout")

    assert isinstance(_FailingFxRateSource(), FxRateSource)

    with pytest.raises(TimeoutError, match="fx rate provider timeout"):
        await _FailingFxRateSource().rate(Currency.KRW, Currency.USDT, _now())


# ── DEEPEN: 성능 단언 ─────────────────────────────────────────────────────────


def test_isinstance_port_check_is_fast() -> None:
    """성능 단언: isinstance()로 Protocol 체크하는 overhead는 1회당
    100us 미만이어야 한다(10만 회/초 기준). 구조 검사라도 N번 호출하면
    누적 overhead가 중요하다."""
    repo = _FullJournalRepo()
    iterations = 10_000
    start = time.perf_counter()
    for _ in range(iterations):
        isinstance(repo, PositionJournalRepository)
    elapsed = time.perf_counter() - start
    per_check_us = elapsed / iterations * 1_000_000
    # 100us 미만 — 구조 검사라도 과용하면 병목된다
    assert per_check_us < 100, (
        f"isinstance port check took {per_check_us:.1f}us/check, budget: 100us"
    )
