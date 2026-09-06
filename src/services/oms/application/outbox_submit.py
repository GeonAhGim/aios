"""L4-14/L4-31 — outbox SUBMIT의 거래소 호출 판정(`OutboxDispatcher._send_submit` 본체).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §5.4(재시도 전 역조회),
§6 F14(DUPLICATE_CLIENT_ID), §4.4(outbox 상태기계 상호참조 — 이 함수의 반환값
`SendOutcome.kind`가 §4.4 `SENDING → DONE|RETRY|DEAD` 전이를 결정한다).
§2-C/§9 L4-31: 이 파일은 `outbox_dispatcher.py`(L4-14)에서 분리된 모듈로,
명세에 행이 없던 것을 소급 등재했다(ADR-2026-09-06-G §10).

거래소 호출 자체(§5.1 tx 밖)만 여기서 한다 — 트랜잭션·outbox 펜스·주문 전이는
`outbox_dispatcher.py`가 맡는다(I8 유일 호출 지점은 그대로 유지된다).
"""
from __future__ import annotations

from src.data.models.trading import Order
from src.exchanges.common.adapter import ExchangeAdapter, UnsupportedCapabilityError
from src.services.oms.application.dispatch_outcome import (
    OutcomeKind,
    SendOutcome,
    classify_lookup_failure,
    classify_submit_failure,
    classify_submit_response,
)
from src.services.oms.application.outbox_writes import adopt


async def call_submit(
    adapter: ExchangeAdapter, venue_order: Order, verify_first: bool
) -> SendOutcome:
    if verify_first:  # §5.4 재시도 전 반드시 역조회
        try:
            existing = await adapter.find_order_by_client_id(venue_order.client_order_id)
        except UnsupportedCapabilityError:
            return SendOutcome(OutcomeKind.UNKNOWN, "RESEND_UNVERIFIABLE")
        except Exception as exc:  # noqa: BLE001 — 분류는 dispatch_outcome 책임
            return classify_lookup_failure(exc)
        if existing is not None:
            return adopt(existing, "RESEND_ADOPTED")
    try:
        submitted = await adapter.place_order(venue_order)
    except Exception as exc:  # noqa: BLE001 — 분류는 dispatch_outcome 책임
        outcome = classify_submit_failure(exc)
        if outcome.kind is not OutcomeKind.ADOPT:
            return outcome
    else:
        return classify_submit_response(submitted)
    # §6 F14 — DUPLICATE_CLIENT_ID: 기존 주문 채택, 못 찾으면 UNKNOWN(resolver).
    try:
        existing = await adapter.find_order_by_client_id(venue_order.client_order_id)
    except Exception:  # noqa: BLE001
        return SendOutcome(OutcomeKind.UNKNOWN, "DUPLICATE_CLIENT_ID_LOOKUP_FAILED")
    if existing is None:
        return SendOutcome(OutcomeKind.UNKNOWN, "DUPLICATE_CLIENT_ID_NOT_FOUND")
    return adopt(existing, "DUPLICATE_ADOPTED")
