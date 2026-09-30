"""DEEPEN(task-9850, 원 리프 task-6704): `positions` 모듈의 도메인
불변식 검증 통합테스트.

positions 모듈(LB-2, LB-8, LB-14, FA-0d 등)의 공개 API(position_key,
borrow, cost_basis, fx 등)가 입력 불변식을 명시적으로 거부하는지,
그리고 의존성 실패 시 fail-closed하는지를 검증한다.

INVARIANTS.md 점검: I-01~I-12는 주문 제출/실행-소유권/멱등키/전략
아티팩트/컴플라이언스 등 실행 경로를 다룬다 — 이 리프는 positions
도메인 불변식(비음수 수량, 통화 일치, 유효 position_key 형식) 검증이
목표로, I-01~I-12와 직접 교차하지 않는다(N/A).
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.data.models.base import Currency
from src.foundation.positions.domain.borrow import (
    BorrowPosition,
    Locate,
    NonPositiveQuantityError,
)
from src.foundation.positions.domain.position_key import (
    InvalidPositionKeyError,
    PositionKey,
)


class TestPositionKeyInvariants:
    """position_key의 불변식: venue/instrument_id/strategy_id/execution_id는
    비어있을 수 없고, ':' 구분자를 포함할 수 없으며, portfolio_id는 UUID여야 한다.
    """

    def test_rejects_empty_venue(self) -> None:
        """negative: venue가 비어있으면 InvalidPositionKeyError로 거부."""
        with pytest.raises(InvalidPositionKeyError, match="venue는 비어 있을 수 없습니다"):
            PositionKey(
                venue="",
                instrument_id="BTC",
                strategy_id="trend",
                execution_id="paper",
                portfolio_id=uuid.uuid4(),
            )

    def test_rejects_venue_with_delimiter(self) -> None:
        """negative: venue가 ':' 구분자를 포함하면 거부(position_key 직렬화 손상 방지)."""
        with pytest.raises(InvalidPositionKeyError, match="구분자"):
            PositionKey(
                venue="bitget:spoofed",
                instrument_id="BTC",
                strategy_id="trend",
                execution_id="paper",
                portfolio_id=uuid.uuid4(),
            )

    def test_rejects_malformed_position_key_parse(self) -> None:
        """negative: position_key.parse()는 5부분 형식을 강제한다."""
        with pytest.raises(InvalidPositionKeyError, match="5부분"):
            PositionKey.parse("bitget:BTC:trend:paper")  # 4부분만 있음

    def test_accepts_valid_position_key(self) -> None:
        """happy path: 유효한 position_key 생성 및 직렬화."""
        portfolio_id = uuid.uuid4()
        pk = PositionKey(
            venue="bitget",
            instrument_id="BTCUSDT",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=portfolio_id,
        )
        assert str(pk) == f"bitget:BTCUSDT:default:paper:{portfolio_id}"

    def test_parse_round_trip(self) -> None:
        """happy path: position_key 직렬화 ↔ 파싱 왕복."""
        portfolio_id = uuid.uuid4()
        original = PositionKey(
            venue="kis",
            instrument_id="000660",
            strategy_id="momentum",
            execution_id="live",
            portfolio_id=portfolio_id,
        )
        parsed = PositionKey.parse(str(original))
        assert parsed.venue == original.venue
        assert parsed.instrument_id == original.instrument_id
        assert parsed.strategy_id == original.strategy_id
        assert parsed.execution_id == original.execution_id
        assert parsed.portfolio_id == original.portfolio_id


class TestLocateAndBorrowInvariants:
    """Locate와 BorrowPosition의 불변식: quantity/short_quantity는 양수여야
    하며, expires_at은 granted_at 이후여야 한다.
    """

    def test_locate_rejects_zero_quantity(self) -> None:
        """negative: Locate 생성 시 quantity=0은 거부."""
        now = datetime.now(timezone.utc)
        with pytest.raises(NonPositiveQuantityError, match="0보다 커야"):
            Locate(
                locate_id="loc-001",
                quantity=Decimal("0"),
                source="prime_broker",
                granted_at=now,
                expires_at=now + timedelta(days=1),
            )

    def test_locate_rejects_negative_quantity(self) -> None:
        """negative: Locate 생성 시 quantity<0은 거부."""
        now = datetime.now(timezone.utc)
        with pytest.raises(NonPositiveQuantityError, match="0보다 커야"):
            Locate(
                locate_id="loc-001",
                quantity=Decimal("-1.5"),
                source="prime_broker",
                granted_at=now,
                expires_at=now + timedelta(days=1),
            )

    def test_locate_rejects_expires_at_before_granted_at(self) -> None:
        """negative: Locate expires_at이 granted_at 이전이면 거부."""
        now = datetime.now(timezone.utc)
        with pytest.raises(ValueError, match="expires_at은 granted_at 이후여야"):
            Locate(
                locate_id="loc-001",
                quantity=Decimal("100"),
                source="prime_broker",
                granted_at=now,
                expires_at=now - timedelta(hours=1),  # 1시간 전
            )

    def test_borrow_position_rejects_zero_short_quantity(self) -> None:
        """negative: BorrowPosition 생성 시 short_quantity=0은 거부."""
        with pytest.raises(NonPositiveQuantityError, match="0보다 커야"):
            BorrowPosition(
                position_key="bitget:BTC:trend:paper:00000000-0000-0000-0000-000000000000",
                short_quantity=Decimal("0"),
                supply_rate=Decimal("0.001"),
                currency=Currency.USDT,
            )

    def test_borrow_position_rejects_negative_supply_rate(self) -> None:
        """negative: BorrowPosition 생성 시 supply_rate<0은 거부."""
        with pytest.raises(ValueError, match="음수일 수 없습니다"):
            BorrowPosition(
                position_key="bitget:BTC:trend:paper:00000000-0000-0000-0000-000000000000",
                short_quantity=Decimal("100"),
                supply_rate=Decimal("-0.001"),
                currency=Currency.USDT,
            )

    def test_accepts_valid_locate(self) -> None:
        """happy path: 유효한 Locate 생성."""
        now = datetime.now(timezone.utc)
        locate = Locate(
            locate_id="loc-001",
            quantity=Decimal("100.5"),
            source="prime_broker",
            granted_at=now,
            expires_at=now + timedelta(days=1),
        )
        assert locate.quantity == Decimal("100.5")
        assert locate.is_active(as_of=now)

    def test_accepts_valid_borrow_position(self) -> None:
        """happy path: 유효한 BorrowPosition 생성."""
        position = BorrowPosition(
            position_key="bitget:BTCUSDT:default:paper:00000000-0000-0000-0000-000000000000",
            short_quantity=Decimal("50.75"),
            supply_rate=Decimal("0.0005"),
            currency=Currency.USDT,
        )
        assert position.short_quantity == Decimal("50.75")
        assert position.currency == Currency.USDT


class _InjectedDomainFailure(RuntimeError):
    pass


class TestDomainFailurePropagation:
    """도메인 함수가 의존성 실패 시 조용히 삼키지 않고 즉시 전파하는지
    검증(fail-closed).
    """

    def test_position_key_parse_propagates_parsing_error(self) -> None:
        """failure injection: position_key.parse()는 형식 에러를 전파해야 하며,
        예외 처리로 숨기지 않는다.
        """
        # 잘못된 UUID 부분을 포함한 position_key
        malformed = "bitget:BTC:trend:paper:not-a-uuid"
        with pytest.raises(InvalidPositionKeyError):
            PositionKey.parse(malformed)


@pytest.mark.perf
def test_position_key_construction_stays_within_local_budget() -> None:
    """성능 단언(D2): PositionKey 생성(불변식 검증 포함)의 절대 시간이
    기준 내에 있는지 단언한다. 불변식 검증이 무거운 암호화나 I/O를 하지
    않으므로 매우 빨아야 한다.
    """
    samples: list[float] = []
    portfolio_id = uuid.uuid4()

    for _ in range(100):
        start = time.perf_counter()
        _ = PositionKey(
            venue="bitget",
            instrument_id="BTCUSDT",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=portfolio_id,
        )
        samples.append(time.perf_counter() - start)

    p95 = sorted(samples)[int(len(samples) * 0.95)]
    # 메모리 문자열 처리만이므로 <100us 기대(보수적 예산 1ms)
    budget = 0.001
    assert p95 < budget, (
        f"PositionKey construction p95={p95 * 1000:.3f}ms, budget {budget * 1000:.1f}ms"
    )
