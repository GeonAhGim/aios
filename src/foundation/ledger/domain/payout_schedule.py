"""LC-5 — 정산 스케줄 산출(payout schedule).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.3 `PayoutBatchView`,
§4.4 PAYOUT_RELEASE·PAYOUT_PAID, §9 LC-5.

`HOLD_CAPTURED`로 판매자 `PENDING_PAYOUT`에 쌓인 정산액을, 기간 경계
(`period_start`~`period_end`)와 정산창(`settlement_window`)이 지난 것만
판매자별로 묶어 `PayoutScheduleItem` 목록을 만든다. 실제 `PAYOUT_RELEASE`
분개(LC-4)는 이 결과를 바탕으로 호출자가 만든다 — 이 모듈은 "무엇을 얼마나
묶을지"만 순수하게 계산한다. `batch_key`가 LC-7 `ledger_payout_batch`의
`UNIQUE(seller_user_id, period_end)`와 같은 모양의 중복 지급 방지 키다.
순수 함수만: I/O·시계·랜덤 직접 호출 금지, `now`는 인자로 받는다.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from src.data.models.base import Currency


class InvalidCaptureAmountError(ValueError):
    """정산 대상 캡처 금액이 0 이하다."""


class MixedCurrencyBatchError(ValueError):
    """한 판매자의 정산 배치에 통화가 둘 이상 섞였다(§4.4 "통화 단일")."""


class DuplicateCaptureError(ValueError):
    """같은 `capture_entry_id`가 입력에 두 번 이상 등장했다 — 이중 지급 위험
    (LC-7 `ledger_payout_item.capture_entry_id UNIQUE`와 동일 불변을 코드로 선반영)."""


@dataclass(frozen=True, slots=True)
class CaptureRecord:
    """정산 대상 `HOLD_CAPTURED` 분개 하나의 뷰. `captured_at`은 정산창 판정
    기준 시각(분개 `posted_at`)이다."""

    entry_id: UUID
    seller_user_id: UUID
    amount: Decimal
    currency: Currency
    captured_at: datetime

    def __post_init__(self) -> None:
        if self.amount <= 0:
            raise InvalidCaptureAmountError(
                f"capture amount는 0보다 커야 함: entry_id={self.entry_id} amount={self.amount}"
            )


@dataclass(frozen=True, slots=True)
class PayoutScheduleItem:
    seller_user_id: UUID
    period_start: date
    period_end: date
    amount: Decimal
    currency: Currency
    capture_entry_ids: list[UUID] = field(default_factory=list)

    @property
    def batch_key(self) -> str:
        return batch_key(self.seller_user_id, self.period_end)


def batch_key(seller_user_id: UUID, period_end: date) -> str:
    """`(seller_user_id, period_end)` 중복 지급 방지 키(LC-7 UNIQUE와 동형)."""
    return f"{seller_user_id}:{period_end.isoformat()}"


def _is_in_period(captured_at: datetime, period_start: date, period_end: date) -> bool:
    d = captured_at.date()
    return period_start <= d < period_end


def _is_window_elapsed(captured_at: datetime, settlement_window: timedelta, now: datetime) -> bool:
    return now >= captured_at + settlement_window


def schedule_payouts(
    captures: Sequence[CaptureRecord],
    *,
    period_start: date,
    period_end: date,
    settlement_window: timedelta,
    now: datetime,
) -> list[PayoutScheduleItem]:
    """기간 경계 안에서 캡처됐고 정산창이 지난 것만 판매자별로 합산한다.
    기간 밖이거나 창 미경과인 캡처는 조용히 제외된다(다음 스케줄 실행에서
    다시 후보가 됨) — 이 함수 자체는 상태를 남기지 않는다."""
    seen_entry_ids: set[UUID] = set()
    for capture in captures:
        if capture.entry_id in seen_entry_ids:
            raise DuplicateCaptureError(f"capture_entry_id 중복: {capture.entry_id}")
        seen_entry_ids.add(capture.entry_id)

    eligible = [
        c
        for c in captures
        if _is_in_period(c.captured_at, period_start, period_end)
        and _is_window_elapsed(c.captured_at, settlement_window, now)
    ]

    by_seller: dict[UUID, list[CaptureRecord]] = {}
    for capture in eligible:
        by_seller.setdefault(capture.seller_user_id, []).append(capture)

    items: list[PayoutScheduleItem] = []
    for seller_user_id, seller_captures in by_seller.items():
        currencies = {c.currency for c in seller_captures}
        if len(currencies) > 1:
            codes = sorted(c.value for c in currencies)
            raise MixedCurrencyBatchError(
                f"seller={seller_user_id}: 정산 배치에 통화가 섞였습니다: {codes}"
            )
        total = sum((c.amount for c in seller_captures), Decimal("0"))
        items.append(
            PayoutScheduleItem(
                seller_user_id=seller_user_id,
                period_start=period_start,
                period_end=period_end,
                amount=total,
                currency=seller_captures[0].currency,
                capture_entry_ids=[c.entry_id for c in seller_captures],
            )
        )
    return items
