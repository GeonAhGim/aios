"""LA-25 — 공매도 차입·마진 원시타입(borrow).

Spec: ADR-2026-09-06-G §9 표("공매도·차입·마진 개념 자체가 없다(locate
없음) — Aladdin/CRIMS는 locate 없는 공매도를 차단" → "LA-25
`positions/domain/borrow.py` + `pos_borrow_position`"). 이 리프는 아직
`docs/specs/L4_market_data_positions_ledger_v1.0.md` §9 표에 반영되지
않았다(같은 문서 §10 R6은 "현물 공매도 금지"만 언급) — ADR-G 본문의
한 줄 설명을 근거로 여기서 계약을 처음 정의한다. 영속화 테이블
`pos_borrow_position`은 마이그레이션 parent(down_revision) 결정 대기 중
(PM decision)이라 이 리프에서는 아직 만들지 않는다 — 아래는 순수
도메인 규칙만이며, 리포지토리 어댑터는 후속 리프의 몫이다.

세 가지 원시 개념(§DoD):
1. **locate 게이트** — 공매도(순포지션을 더 숏으로 만드는 매도)는
   사전에 확보한 소유권 확인서(locate) 수량을 넘을 수 없다
   (`check_locate_gate`). 위반은 `LocateRequiredError`(불가, 호출자가
   먼저 locate를 확보해야 함).
2. **일별 차입 이자 적립** — 숏 수량 × 시가 × 공급 이율(연) / day-count.
   day-count 관례(ACT/360 vs ACT/365)는 프라임 브로커 계약서 대조 전이라
   **미검증**이다 — `DEFAULT_DAY_COUNT=360`은 가정값.
3. **마진콜 판정** — 자기자본(담보 - 숏 시가평가액)이 유지증거금
   요건(유지증거금률 × 숏 시가평가액) 아래로 떨어지면 `MarginCallEvent`를
   낸다. 공매도 대금 재투자·이자 등 실제 프라임 브로커리지의 세부
   증거금 계산(Reg T, 포트폴리오 마진)은 이 원시타입의 범위 밖이다 —
   **미검증**, 실제 브로커 마진 규정과 교차검증 전.

순수 함수·값 객체만 — I/O·시계 직접 호출 금지(호출자가 `as_of`를
넘긴다). `Decimal`만 사용, float 금지(105번 표준과 동일 원칙).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from src.data.models.base import Currency, Money
from src.data.models.trading import OrderSide

DEFAULT_DAY_COUNT = 360
"""연이율을 일할 계산할 때 나누는 일수. ACT/360 관례를 가정한다
(미검증 — 실제 프라임 브로커 계약서 대조 전, docstring 서두 참고)."""


class NonPositiveQuantityError(ValueError):
    """수량·이율 등 양수여야 하는 값이 0 이하다."""


class CurrencyMismatchError(ValueError):
    """서로 다른 통화의 `Money`를 같은 계산에 섞으려 했다."""

    def __init__(self, expected: Currency, actual: Currency) -> None:
        super().__init__(f"통화 불일치: 기대={expected.value}, 실제={actual.value}")
        self.expected = expected
        self.actual = actual


class LocateRequiredError(Exception):
    """공매도 주문이 확보된 locate 수량을 초과한다 — 주문 게이트 거부.

    불가(재시도 불가능): 호출자가 먼저 locate를 추가 확보한 뒤 다시
    제출해야 한다. Aladdin/CRIMS가 locate 없는 공매도를 원천 차단하는
    것과 동일한 게이트(ADR-2026-09-06-G §9)."""

    def __init__(self, *, requested: Decimal, available: Decimal) -> None:
        super().__init__(
            f"locate 부족: 필요 숏 수량 {requested} > 확보된 locate {available} "
            "— 공매도 주문을 게이트에서 거부합니다."
        )
        self.requested = requested
        self.available = available


@dataclass(frozen=True, slots=True)
class Locate:
    """공매도 전 확보한 소유권 확인서(locate) 한 건."""

    locate_id: str
    quantity: Decimal
    source: str
    granted_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise NonPositiveQuantityError(f"quantity는 0보다 커야 합니다: {self.quantity}")
        if self.expires_at <= self.granted_at:
            raise ValueError("expires_at은 granted_at 이후여야 합니다.")

    def is_active(self, *, as_of: datetime) -> bool:
        return self.granted_at <= as_of < self.expires_at


def check_locate_gate(
    *,
    side: OrderSide,
    quantity: Decimal,
    current_position_quantity: Decimal,
    locates: list[Locate],
    as_of: datetime,
) -> None:
    """공매도 주문 게이트. 순수 판정 — 통과하면 `None`을 반환하고 조용히
    끝난다, 위반이면 예외를 던진다(불가, 침묵 통과 금지).

    `side == BUY`는 항상 통과한다(숏커버 또는 롱 진입은 locate가 필요
    없다). `side == SELL`인 경우 체결 후 순포지션(`current_position_quantity
    - quantity`, 음수=숏)이 이전보다 더 숏 방향으로 커진 만큼(=신규
    공매도분)만 locate로 커버돼야 한다 — 기존 롱 보유분을 청산하는
    매도는 공매도가 아니므로 locate가 필요 없다.

    `as_of` 시각에 활성(만료 전·도래 후)인 locate들의 수량 합만
    유효하다고 본다.
    """
    if quantity <= 0:
        raise NonPositiveQuantityError(f"quantity는 0보다 커야 합니다: {quantity}")
    if side != OrderSide.SELL:
        return

    resulting_quantity = current_position_quantity - quantity
    previous_short = max(Decimal("0"), -current_position_quantity)
    resulting_short = max(Decimal("0"), -resulting_quantity)
    new_short_quantity = resulting_short - previous_short
    if new_short_quantity <= 0:
        return  # 기존 롱 청산 또는 숏 축소 — 공매도가 아니다.

    available = sum((loc.quantity for loc in locates if loc.is_active(as_of=as_of)), Decimal("0"))
    if available < new_short_quantity:
        raise LocateRequiredError(requested=new_short_quantity, available=available)


@dataclass(frozen=True, slots=True)
class BorrowPosition:
    """`pos_borrow_position`(영속화는 후속 리프) 한 행의 순수 뷰. 숏
    수량은 항상 양수로 저장한다 — 부호는 이 값 객체 밖의
    `PositionSnapshotView.quantity`(음수 숏)가 담당한다."""

    position_key: str
    short_quantity: Decimal
    supply_rate: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        if self.short_quantity <= 0:
            raise NonPositiveQuantityError(
                f"short_quantity는 0보다 커야 합니다: {self.short_quantity}"
            )
        if self.supply_rate < 0:
            raise ValueError(f"supply_rate는 음수일 수 없습니다: {self.supply_rate}")


def _require_currency(money: Money, expected: Currency) -> None:
    if money.currency != expected:
        raise CurrencyMismatchError(expected, money.currency)


def accrue_daily_interest(
    position: BorrowPosition,
    *,
    mark_price: Money,
    day_count: int = DEFAULT_DAY_COUNT,
) -> Money:
    """숏 포지션의 하루치 차입 이자 = `short_quantity × mark_price ×
    supply_rate / day_count`(공급 이율로 적립, 항상 양수 — 차입자가
    대주기관에 지불하는 비용). `day_count`는 0보다 커야 한다."""
    if day_count <= 0:
        raise NonPositiveQuantityError(f"day_count는 0보다 커야 합니다: {day_count}")
    _require_currency(mark_price, position.currency)
    daily = position.short_quantity * mark_price.amount * position.supply_rate / Decimal(day_count)
    return Money(amount=daily, currency=position.currency)


def accrue_interest_over(
    position: BorrowPosition,
    *,
    daily_marks: list[Money],
    day_count: int = DEFAULT_DAY_COUNT,
) -> Money:
    """여러 날에 걸친 일별 이자의 합(각 날의 시가로 재계산) — 덧셈은
    교환·결합법칙이 성립하므로 순서 무관(fold)."""
    total = Decimal("0")
    for mark in daily_marks:
        total += accrue_daily_interest(position, mark_price=mark, day_count=day_count).amount
    return Money(amount=total, currency=position.currency)


@dataclass(frozen=True, slots=True)
class MarginCallEvent:
    """마진콜 알림 이벤트 — 유지증거금 요건 위반 1건."""

    position_key: str
    equity: Decimal
    required_margin: Decimal
    deficit: Decimal
    currency: Currency
    as_of: datetime


def evaluate_margin_call(
    position: BorrowPosition,
    *,
    mark_price: Money,
    collateral: Money,
    maintenance_margin_rate: Decimal,
    as_of: datetime,
) -> MarginCallEvent | None:
    """숏 포지션의 유지증거금 위반을 판정한다.

    `equity = collateral - short_quantity × mark_price`(단순화 모델 —
    공매도 대금 재투자·이자는 범위 밖, docstring 서두 참고).
    `required_margin = maintenance_margin_rate × short_quantity ×
    mark_price`. `equity < required_margin`이면 부족분(`deficit`,
    항상 양수)과 함께 `MarginCallEvent`를 반환한다 — 경계값(`equity ==
    required_margin`)은 위반이 아니다(정확히 요건을 충족). 위반이
    아니면 `None`(알림 없음, 침묵 성공)이다.
    """
    if maintenance_margin_rate <= 0:
        raise NonPositiveQuantityError(
            f"maintenance_margin_rate는 0보다 커야 합니다: {maintenance_margin_rate}"
        )
    _require_currency(mark_price, position.currency)
    _require_currency(collateral, position.currency)

    market_value = position.short_quantity * mark_price.amount
    equity = collateral.amount - market_value
    required_margin = maintenance_margin_rate * market_value
    if equity >= required_margin:
        return None

    return MarginCallEvent(
        position_key=position.position_key,
        equity=equity,
        required_margin=required_margin,
        deficit=required_margin - equity,
        currency=position.currency,
        as_of=as_of,
    )
