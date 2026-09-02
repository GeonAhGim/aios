"""LC-4 — 사건 → 분개행 매핑(posting rules).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§4.4, §9 LC-4.

`lines_for`가 "누가 차변·누가 대변"을 아는 유일한 곳이다(§4.4). LC-2
(`chart_of_accounts`)로 계정코드를 만들고 유형을 검증하며, LC-3
(`balance_rules.check_balanced`)로 반환 직전 Σ차변=Σ대변·단일통화를
재확인한다 — 둘 다 재구현하지 않는다. 순수 함수만: I/O·시계 직접 호출
금지, 필요한 값은 전부 `LedgerEvent`(parties/extra)로 받는다.

`LedgerEvent.extra`는 `dict[str, Decimal | str]`만 담을 수 있어 사건별로
아래 키를 규약으로 쓴다(§4.4 "필수 parties/extra" 열):

- HOLD_CAPTURED, REFUND: `commission_rate`(Decimal) — `rounding.split_commission`
  으로 `commission`·`payout`을 산출(합은 항상 `price`와 정확히 일치).
- REFUND: `refund_case`(str, "R1"|"R2"|"R3") — 정산 창 경과·판매자 잔액
  상태는 I/O가 필요한 판정이므로 호출자(application 계층)가 결정해
  전달한다. R3만 `seller_available_amount`(Decimal) 추가.
- CHARGEBACK: `user_available_amount`(Decimal) — 가용분/부족분 분할.
- PAYOUT_PAID: `external_ref`(str) — 존재만 검증(뷰 필드), 분개 금액에는
  쓰이지 않는다.
- MANUAL_ADJUSTMENT: `debit_account`·`credit_account`(str, 계정코드),
  선택적으로 `debit_currency`·`credit_currency`(str — 생략 시
  `event.currency`). "명시된 행"을 이 두 키로 표현하고 `event.amount`를
  양쪽에 그대로 건다 — 통화가 어긋나면 `check_balanced`(재사용)가 거부한다.

가용분/부족분 분할(`_split_by_available`)에서 부족분이 0이면 그 행은 만들지
않는다 — `PostingLine.amount`는 항상 > 0이어야 하기 때문(§3.3). 분할·생략
후에도 Σ가 어긋나면(코드 버그) `_finalize`의 `check_balanced` 호출이
fail-closed로 막는다.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from src.data.models.base import Currency
from src.foundation.ledger.contracts.v1 import (
    LedgerEvent,
    LedgerEventType,
    PostingLine,
    Side,
    UserSub,
)
from src.foundation.ledger.domain import balance_rules
from src.foundation.ledger.domain.chart_of_accounts import (
    PLATFORM_CASH_CLEARING,
    PLATFORM_COMMISSION_REVENUE,
    PLATFORM_PAYOUT_CLEARING,
    account_type,
)
from src.foundation.ledger.domain.chart_of_accounts import user_account as ua
from src.foundation.ledger.domain.rounding import split_commission


class MissingPartyError(ValueError):
    """필수 `parties` 키가 사건에 없다."""


class MissingExtraFieldError(ValueError):
    """필수 `extra` 키가 사건에 없거나 타입·값이 맞지 않다."""


class UnsupportedEventTypeError(ValueError):
    """`lines_for`가 아는 9종 사건(§9 LC-4)에 속하지 않는다."""


def _party(event: LedgerEvent, key: str) -> UUID:
    try:
        return event.parties[key]
    except KeyError as exc:
        raise MissingPartyError(f"{event.event_type}: 필수 parties 키 {key!r}가 없음") from exc


def _missing_extra(event: LedgerEvent, key: str) -> MissingExtraFieldError:
    return MissingExtraFieldError(f"{event.event_type}: 필수 extra 키 {key!r}가 없음")


def _extra_decimal(event: LedgerEvent, key: str) -> Decimal:
    try:
        value = event.extra[key]
    except KeyError as exc:
        raise _missing_extra(event, key) from exc
    if not isinstance(value, Decimal):
        raise MissingExtraFieldError(f"{event.event_type}: extra[{key!r}]는 Decimal: {value!r}")
    return value


def _extra_str(event: LedgerEvent, key: str) -> str:
    try:
        value = event.extra[key]
    except KeyError as exc:
        raise _missing_extra(event, key) from exc
    if not isinstance(value, str):
        raise MissingExtraFieldError(f"{event.event_type}: extra[{key!r}]는 str: {value!r}")
    return value


def _split_by_available(total: Decimal, available: Decimal) -> tuple[Decimal, Decimal]:
    """`total`을 (가용분, 부족분)으로 나눈다. 가용분은 `[0, total]`로 클램프."""
    covered = min(max(available, Decimal("0")), total)
    return covered, total - covered


def _line(no: int, account: str, side: Side, amount: Decimal, currency: Currency) -> PostingLine:
    return PostingLine(
        line_no=no, account_code=account, side=side, amount=amount, currency=currency
    )


def _finalize(lines: list[PostingLine]) -> list[PostingLine]:
    balance_rules.check_balanced(lines)
    return lines


def _pair(event: LedgerEvent, debit_account: str, credit_account: str) -> list[PostingLine]:
    """가장 흔한 모양(단일 차변 행 + 단일 대변 행, `event.amount`를 그대로)을 위한 지름길."""
    amount, cur = event.amount, event.currency
    return _finalize(
        [
            _line(1, debit_account, Side.DEBIT, amount, cur),
            _line(2, credit_account, Side.CREDIT, amount, cur),
        ]
    )


def _topup_confirmed(event: LedgerEvent) -> list[PostingLine]:
    user_id = _party(event, "user")
    return _pair(event, PLATFORM_CASH_CLEARING, ua(user_id, UserSub.AVAILABLE))


def _hold_placed(event: LedgerEvent) -> list[PostingLine]:
    buyer_id = _party(event, "buyer")
    return _pair(event, ua(buyer_id, UserSub.AVAILABLE), ua(buyer_id, UserSub.HELD))


def _hold_released(event: LedgerEvent) -> list[PostingLine]:
    buyer_id = _party(event, "buyer")
    return _pair(event, ua(buyer_id, UserSub.HELD), ua(buyer_id, UserSub.AVAILABLE))


def _payout_release(event: LedgerEvent) -> list[PostingLine]:
    seller_id = _party(event, "seller")
    return _pair(event, ua(seller_id, UserSub.PENDING_PAYOUT), ua(seller_id, UserSub.AVAILABLE))


def _payout_paid(event: LedgerEvent) -> list[PostingLine]:
    seller_id = _party(event, "seller")
    _extra_str(event, "external_ref")
    return _pair(event, ua(seller_id, UserSub.AVAILABLE), PLATFORM_PAYOUT_CLEARING)


def _hold_captured(event: LedgerEvent) -> list[PostingLine]:
    buyer_id = _party(event, "buyer")
    seller_id = _party(event, "seller")
    rate = _extra_decimal(event, "commission_rate")
    cur = event.currency
    commission, payout = split_commission(event.amount, rate)

    lines = [_line(1, ua(buyer_id, UserSub.HELD), Side.DEBIT, event.amount, cur)]
    no = 2
    if payout > 0:
        lines.append(_line(no, ua(seller_id, UserSub.PENDING_PAYOUT), Side.CREDIT, payout, cur))
        no += 1
    if commission > 0:
        lines.append(_line(no, PLATFORM_COMMISSION_REVENUE, Side.CREDIT, commission, cur))
    return _finalize(lines)


_REFUND_CASES = frozenset({"R1", "R2", "R3"})


def _refund(event: LedgerEvent) -> list[PostingLine]:
    buyer_id = _party(event, "buyer")
    seller_id = _party(event, "seller")
    rate = _extra_decimal(event, "commission_rate")
    case = _extra_str(event, "refund_case")
    if case not in _REFUND_CASES:
        raise MissingExtraFieldError(f"REFUND: refund_case는 'R1'|'R2'|'R3'여야 함: {case!r}")
    cur = event.currency
    price = event.amount
    commission, payout = split_commission(price, rate)

    lines: list[PostingLine] = []
    no = 1
    seller_debit_account: str | None
    if case == "R1":
        seller_debit_account = ua(seller_id, UserSub.PENDING_PAYOUT)
    elif case == "R2":
        seller_debit_account = ua(seller_id, UserSub.AVAILABLE)
    else:  # R3 — 판매자 AVAILABLE 부족분은 RECEIVABLE(유일 음수허용 계정)로.
        seller_available = _extra_decimal(event, "seller_available_amount")
        covered, shortfall = _split_by_available(payout, seller_available)
        if covered > 0:
            lines.append(_line(no, ua(seller_id, UserSub.AVAILABLE), Side.DEBIT, covered, cur))
            no += 1
        if shortfall > 0:
            lines.append(_line(no, ua(seller_id, UserSub.RECEIVABLE), Side.DEBIT, shortfall, cur))
            no += 1
        seller_debit_account = None

    if seller_debit_account is not None and payout > 0:
        lines.append(_line(no, seller_debit_account, Side.DEBIT, payout, cur))
        no += 1
    if commission > 0:
        lines.append(_line(no, PLATFORM_COMMISSION_REVENUE, Side.DEBIT, commission, cur))
        no += 1
    lines.append(_line(no, ua(buyer_id, UserSub.AVAILABLE), Side.CREDIT, price, cur))
    return _finalize(lines)


def _chargeback(event: LedgerEvent) -> list[PostingLine]:
    user_id = _party(event, "user")
    available = _extra_decimal(event, "user_available_amount")
    cur = event.currency
    covered, shortfall = _split_by_available(event.amount, available)

    lines: list[PostingLine] = []
    no = 1
    if covered > 0:
        lines.append(_line(no, ua(user_id, UserSub.AVAILABLE), Side.DEBIT, covered, cur))
        no += 1
    if shortfall > 0:
        lines.append(_line(no, ua(user_id, UserSub.RECEIVABLE), Side.DEBIT, shortfall, cur))
        no += 1
    lines.append(_line(no, PLATFORM_CASH_CLEARING, Side.CREDIT, event.amount, cur))
    return _finalize(lines)


def _manual_adjustment(event: LedgerEvent) -> list[PostingLine]:
    debit_account = _extra_str(event, "debit_account")
    credit_account = _extra_str(event, "credit_account")
    # 계정코드 형식·유형 검증은 LC-2에 위임한다(재구현하지 않음).
    account_type(debit_account)
    account_type(credit_account)
    debit_currency = Currency(event.extra.get("debit_currency", event.currency.value))
    credit_currency = Currency(event.extra.get("credit_currency", event.currency.value))
    return _finalize(
        [
            _line(1, debit_account, Side.DEBIT, event.amount, debit_currency),
            _line(2, credit_account, Side.CREDIT, event.amount, credit_currency),
        ]
    )


_HANDLERS = {
    LedgerEventType.TOPUP_CONFIRMED: _topup_confirmed,
    LedgerEventType.HOLD_PLACED: _hold_placed,
    LedgerEventType.HOLD_CAPTURED: _hold_captured,
    LedgerEventType.HOLD_RELEASED: _hold_released,
    LedgerEventType.REFUND: _refund,
    LedgerEventType.CHARGEBACK: _chargeback,
    LedgerEventType.PAYOUT_RELEASE: _payout_release,
    LedgerEventType.PAYOUT_PAID: _payout_paid,
    LedgerEventType.MANUAL_ADJUSTMENT: _manual_adjustment,
}


def lines_for(event: LedgerEvent) -> list[PostingLine]:
    """사건 9종(§4.4) → 분개행. 반환 직전 `balance_rules.check_balanced`로
    Σ차변=Σ대변·단일통화를 재확인한다(fail-closed)."""
    handler = _HANDLERS.get(event.event_type)
    if handler is None:
        raise UnsupportedEventTypeError(f"지원하지 않는 event_type: {event.event_type!r}")
    return handler(event)
