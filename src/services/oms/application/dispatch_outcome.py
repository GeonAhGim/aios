"""L4-14/L4-31 — outbox 디스패처의 거래소 응답/예외 → 결과 분류(순수, I/O 없음).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §3.4, §5.4, §6 F3/F4/F14,
§4.2/§4.4(outbox 상태기계 표 — 이 모듈의 `OutcomeKind`가 §4.4 outbox 행 상태
전이와 §4.2 주문 상태 전이의 트리거를 함께 판정한다). §2-C/§9 L4-31: 이 파일은
`outbox_dispatcher.py`(L4-14)에서 분리된 순수 판정 모듈로, 명세에 행이 없던
것을 소급 등재했다(ADR-2026-09-06-G §10).

`outbox_dispatcher.py`가 "거래소가 무엇을 돌려줬는가"를 판정하는 규칙만 여기
모은다 — DB·네트워크를 모르므로 단위 테스트가 예외 종류별 분기를 전수로
고정할 수 있다.

fail-closed 원칙(I10): 주문 제출 뒤에 난 예외 중 "거래소가 이 주문을 받지
않았다"고 **확정**할 수 없는 것은 전부 `UNKNOWN`이다. 재시도 가능(`RETRY`)은
`ExchangeError.retryable`이 참인 분류된 오류뿐이고, 그마저 재전송 전에
`find_order_by_client_id` 역조회를 거친다(§5.4, 디스패처 책임). 기존 어댑터가
아직 던지는 레거시 `RetryableExchangeError`/`FatalExchangeError`(kind 없음)는
분류 불가 → `UNKNOWN`으로 보낸다 — 잔고 부족을 UNKNOWN으로 두는 비용보다
응답 유실을 재전송하는 비용(중복 주문)이 훨씬 크다.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.data.models.trading import Order, OrderStatus
from src.exchanges.common.error_taxonomy import (
    ExchangeError,
    ExchangeErrorKind,
    SentUnknownError,
)


class OutcomeKind(str, Enum):
    ACK = "ACK"  # 거래소 수락 → ACKNOWLEDGED
    REJECTED = "REJECTED"  # 거래소 거부(확정) → REJECTED
    UNKNOWN = "UNKNOWN"  # 응답 유실/미분류 → UNKNOWN, outbox DONE(재전송 금지)
    RETRY = "RETRY"  # 분류된 일시 오류 → outbox 재시도(attempt+1, 백오프)
    DEFER = "DEFER"  # 회로 OPEN(전송 전 확정) → not_before 연기, attempt 불변
    ADOPT = "ADOPT"  # DUPLICATE_CLIENT_ID → 역조회로 기존 주문 채택(F14)
    DONE = "DONE"  # CANCEL/MODIFY: 명령 소진, 주문 전이 없음(inbox/대사가 반영)
    DEAD = "DEAD"  # CANCEL/MODIFY: 재시도 불가 확정 오류 → outbox DEAD


# 주문 → REJECTED(reason=kind)로 확정할 수 있는 분류(§3.4). AUTH는 CA
# 2026-09-06 결정으로 REJECTED(reason=AUTH) 확정 — 어댑터 호출 직전에 이미
# SUBMITTED이므로 §4.2 VENUE_REJECTED 경로를 쓴다(FAILED 직행은 전이표 밖).
_VENUE_REJECT_KINDS = frozenset(
    {
        ExchangeErrorKind.INSUFFICIENT_FUNDS,
        ExchangeErrorKind.INVALID_ORDER,
        ExchangeErrorKind.MARKET_CLOSED,
        ExchangeErrorKind.AUTH,
    }
)


@dataclass(frozen=True)
class SendOutcome:
    kind: OutcomeKind
    reason: str
    exchange_order_id: str | None = None
    # 거래소에 도달하지 않았음이 확정된 실패(회로 OPEN·역조회 단계 실패).
    # 디스패처는 이 플래그를 outbox `last_error` 접두어로 남겨, 재클레임 시
    # 역조회를 건너뛰어도 안전한지 판단한다.
    not_sent: bool = False
    retry_after_sec: float | None = None


def classify_submit_response(submitted: Order) -> SendOutcome:
    """`place_order`가 정상 반환한 경우. REJECTED는 예외가 아니라 정상 흐름
    (기존 submit.py 계약). ACK인데 exchange_order_id가 없으면 이후 취소·조회가
    불가능하므로 UNKNOWN(역조회로 채움)."""
    if submitted.status is OrderStatus.REJECTED:
        return SendOutcome(OutcomeKind.REJECTED, "VENUE_REJECTED")
    if not submitted.exchange_order_id:
        return SendOutcome(OutcomeKind.UNKNOWN, "ACK_WITHOUT_EXCHANGE_ORDER_ID")
    return SendOutcome(
        OutcomeKind.ACK, "VENUE_ACK", exchange_order_id=submitted.exchange_order_id
    )


def classify_submit_failure(exc: BaseException) -> SendOutcome:
    """`place_order`가 예외를 낸 경우(§3.4 표의 '주문' 열)."""
    if isinstance(exc, SentUnknownError):
        return SendOutcome(OutcomeKind.UNKNOWN, "SENT_UNKNOWN")
    if isinstance(exc, ExchangeError):
        if exc.circuit_open:
            return SendOutcome(
                OutcomeKind.DEFER,
                "CIRCUIT_OPEN",
                not_sent=True,
                retry_after_sec=exc.retry_after_sec,
            )
        if exc.kind is ExchangeErrorKind.DUPLICATE_CLIENT_ID:
            return SendOutcome(OutcomeKind.ADOPT, "DUPLICATE_CLIENT_ID")
        if exc.kind in _VENUE_REJECT_KINDS:
            return SendOutcome(OutcomeKind.REJECTED, exc.kind.value)
        if exc.kind is ExchangeErrorKind.CLOCK_SKEW or exc.retryable:
            return SendOutcome(
                OutcomeKind.RETRY, exc.kind.value, retry_after_sec=exc.retry_after_sec
            )
        # UNKNOWN_RESPONSE / ORDER_NOT_FOUND / 그 외 재시도 불가 — 주문은 승격(§3.4)
        return SendOutcome(OutcomeKind.UNKNOWN, exc.kind.value)
    # 레거시 Retryable/FatalExchangeError, httpx, asyncio.TimeoutError 등 —
    # 전송 여부를 모른다 = 응답 유실로 취급(I10).
    return SendOutcome(OutcomeKind.UNKNOWN, type(exc).__name__)


def classify_lookup_failure(exc: BaseException) -> SendOutcome:
    """재전송 전 역조회(`find_order_by_client_id`) 자체가 실패한 경우 — 아직
    아무것도 보내지 않았으므로 항상 not_sent."""
    if isinstance(exc, ExchangeError) and exc.circuit_open:
        return SendOutcome(OutcomeKind.DEFER, "CIRCUIT_OPEN", not_sent=True)
    return SendOutcome(
        OutcomeKind.RETRY, f"LOOKUP_FAILED:{type(exc).__name__}", not_sent=True
    )


def classify_idempotent_failure(exc: BaseException) -> SendOutcome:
    """CANCEL/MODIFY(멱등 계열, §5.4) 예외. 응답 유실은 같은 명령을 다시 보내도
    안전하므로 RETRY. ORDER_NOT_FOUND는 명령 소진(대사가 판정, §3.4)."""
    if isinstance(exc, ExchangeError):
        if exc.circuit_open:
            return SendOutcome(OutcomeKind.DEFER, "CIRCUIT_OPEN", not_sent=True)
        if exc.kind is ExchangeErrorKind.ORDER_NOT_FOUND:
            return SendOutcome(OutcomeKind.DONE, "ORDER_NOT_FOUND")
        if exc.retryable or exc.kind in (
            ExchangeErrorKind.UNKNOWN_RESPONSE,
            ExchangeErrorKind.CLOCK_SKEW,
        ):
            return SendOutcome(
                OutcomeKind.RETRY, exc.kind.value, retry_after_sec=exc.retry_after_sec
            )
        return SendOutcome(OutcomeKind.DEAD, exc.kind.value)
    return SendOutcome(OutcomeKind.RETRY, type(exc).__name__)
