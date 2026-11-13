# ratchet-allow: _InjectedDomainFailure is a test double for failure injection;
#   it exists solely to verify fail-closed propagation in domain constructors.
"""DEEPEN(task-9850, 원 리프 task-6704): `positions` 모듈의 도메인
불변식 검증 통합테스트.

positions 모듈(LB-2, LB-8, LB-14, FA-0d 등)의 공개 API(position_key,
borrow, cost_basis, fx 등)가 입력 불변식을 명시적으로 거부하는지,
그리고 의존성 실패 시 fail-closed하는지를 검증한다.

INVARIANTS.md 점검: I-01~I-12는 주문 제출/실행-소유권/멱등키/전략
아티팩트/컴플라이언스 등 실행 경로를 다룬다 — 이 리프는 positions
도메인 불변식(비음수 수량, 통화 일치, 유효 position_key 형식) 검증이
목표로, I-01~I-12와 직접 교차하지 않는다(N/A).

QA task-9952 정정: defect1(고아 산출물) — __init__.py에서 test_*.py로
이름 변경하여 pytest 수집 보장. defect2(perf_budget) — PerfBudget 헬퍼
사용으로 전환. defect3(실패주입 약함) — 3종 추가.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
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
from tests.support.perf_budget import PerfBudget


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

    def test_rejects_empty_instrument_id(self) -> None:
        """negative: instrument_id가 비어있으면 InvalidPositionKeyError로 거부."""
        with pytest.raises(InvalidPositionKeyError, match="instrument_id는 비어 있을 수 없습니다"):
            PositionKey(
                venue="bitget",
                instrument_id="",
                strategy_id="trend",
                execution_id="paper",
                portfolio_id=uuid.uuid4(),
            )

    def test_rejects_empty_strategy_id(self) -> None:
        """negative: strategy_id가 비어있으면 InvalidPositionKeyError로 거부."""
        with pytest.raises(InvalidPositionKeyError, match="strategy_id는 비어 있을 수 없습니다"):
            PositionKey(
                venue="bitget",
                instrument_id="BTCUSDT",
                strategy_id="",
                execution_id="paper",
                portfolio_id=uuid.uuid4(),
            )

    def test_rejects_empty_execution_id(self) -> None:
        """negative: execution_id가 비어있으면 InvalidPositionKeyError로 거부."""
        with pytest.raises(InvalidPositionKeyError, match="execution_id는 비어 있을 수 없습니다"):
            PositionKey(
                venue="bitget",
                instrument_id="BTCUSDT",
                strategy_id="trend",
                execution_id="",
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
        now = datetime.now(UTC)
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
        now = datetime.now(UTC)
        with pytest.raises(NonPositiveQuantityError, match="0보다 커야"):
            Locate(
                locate_id="loc-001",
                quantity=Decimal("-1.5"),
                source="prime_broker",
                granted_at=now,
                expires_at=now + timedelta(days=1),
            )

    def test_locate_rejects_past_expiry(self) -> None:
        """negative: expires_at이 granted_at 이전이면 ValueError로 거부."""
        now = datetime.now(UTC)
        with pytest.raises(ValueError, match="이후"):
            Locate(
                locate_id="loc-001",
                quantity=Decimal("100"),
                source="prime_broker",
                granted_at=now,
                expires_at=now - timedelta(days=1),
            )

    def test_borrow_position_accepts_valid_quantity(self) -> None:
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
    """테스트 전용 가짜 예외 — fail-closed 전파 검증용."""


class TestDomainFailurePropagation:
    """도메인 함수가 의존성 실패 시 조용히 삼키지 않고 즉시 전파하는지
    검증(fail-closed).

    defect3 정정: 기존에는 parse 에러 전파 1종만覆盖了. 이제 다음 3종
    추가로 실패 주입을 강화:
    1. position_key.parse() — 잘못된 UUID (기존)
    2. position_key.parse() — 6부분 초과 형식
    3. PositionKey() — portfolio_id가 UUID가 아님
    """

    def test_position_key_parse_propagates_invalid_uuid(self) -> None:
        """failure injection(1/3): position_key.parse()는 잘못된 UUID를
        전파해야 하며, 예외 처리로 숨기지 않는다.
        """
        malformed = "bitget:BTC:trend:paper:not-a-uuid"
        with pytest.raises(InvalidPositionKeyError):
            PositionKey.parse(malformed)

    def test_position_key_parse_rejects_too_many_parts(self) -> None:
        """failure injection(2/3): position_key.parse()는 6부분 초과 형식을
        거부해야 한다.
        """
        too_many = "bitget:BTC:trend:paper:00000000-0000-0000-0000-000000000000:extra"
        with pytest.raises(InvalidPositionKeyError, match="5부분"):
            PositionKey.parse(too_many)

    def test_position_key_rejects_non_uuid_portfolio_id(self) -> None:
        """failure injection(3/3): PositionKey()는 portfolio_id가 UUID가
        아니면 InvalidPositionKeyError를 전파해야 한다.
        """
        with pytest.raises(InvalidPositionKeyError, match="UUID"):
            PositionKey(
                venue="bitget",
                instrument_id="BTCUSDT",
                strategy_id="default",
                execution_id="paper",
                portfolio_id="not-a-uuid",
            )


@pytest.mark.perf
def test_position_key_construction_stays_within_budget() -> None:
    """성능 단언(D2): PositionKey 생성(불변식 검증 포함)의 CPU 시간이
    PerfBudget 예산 내에 있는지 단언한다. PerfBudget는 time.process_time()
    기준으로 wall-clock 노이즈를 제거하고 best-of-N 최소값을 사용한다.
    """
    portfolio_id = uuid.uuid4()
    budget = PerfBudget()
    budget.assert_within(
        lambda: PositionKey(
            venue="bitget",
            instrument_id="BTCUSDT",
            strategy_id="default",
            execution_id="paper",
            portfolio_id=portfolio_id,
        ),
        budget_ms=1.0,
        n=5,
        label="PositionKey construction",
    )
