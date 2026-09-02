"""LB-6 — 일별 NAV 체인(nav).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§3.2, §3.4, §9 LB-6,
§4.3 "전일 NAV + 손익 + 자금흐름 = 당일 NAV", DB `CHECK(closing_nav = cash +
positions_mv)`.

두 개의 독립된 등식이 동시에 성립해야 하루치 NAV가 유효하다:

1. 대차대조 등식(잔고 기준) — `closing = cash + Σ position_mv`.
   [[compute_daily_nav]]가 `closing_nav`를 이 식으로 직접 계산하므로,
   반환된 `NAVSnapshot`은 이 등식을 구성상 항상 만족한다(별도 검증 불필요).
2. 롤포워드 등식(손익 기준) — `closing = opening + realized + Δunrealized
   + funding − fees + flows`이고, `opening`은 전일 `closing`과 같아야
   체인이 이어진다. [[verify_chain]]이 이틀치 스냅샷을 받아 이 두 조건을
   확인한다.

두 등식이 어긋나면(반올림으로 흡수하지 않고 정확한 `Decimal` 등호 비교)
`POS_NAV_CHAIN_BROKEN`(운영 개입 전까지 재시도 불가) — 저장을 거부해야
하는 신호다. 순수 함수만 — I/O·시계 직접 호출 금지.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from uuid import UUID

from src.data.models.base import Currency, FXRate
from src.foundation.positions.contracts.v1 import NAVSnapshot, PositionErrorCode


class NavChainBrokenError(Exception):
    """`POS_NAV_CHAIN_BROKEN` — NAV 체인 등식이 성립하지 않는다(불가능한
    상태, 운영 개입 필요). 허용오차 0 — 값을 반올림해 등식을 억지로 맞추지
    않는다."""

    code = PositionErrorCode.NAV_CHAIN_BROKEN

    def __init__(self, message: str) -> None:
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class NavInputs:
    """[[compute_daily_nav]] 입력. `position_mvs`는 그날 열린 포지션 각각의
    기준통화 시가평가액이고, `Σ position_mvs`가 대차대조 등식의 우변을
    이룬다."""

    account_id: UUID
    nav_date: date
    base_currency: Currency
    opening_nav: Decimal
    cash: Decimal
    position_mvs: Sequence[Decimal]
    realized: Decimal
    unrealized_delta: Decimal
    funding: Decimal
    fees: Decimal
    flows: Decimal
    fx_rates: Sequence[FXRate] = field(default_factory=tuple)


def compute_daily_nav(inputs: NavInputs) -> NAVSnapshot:
    """대차대조 등식으로 `closing_nav`를 계산해 `NAVSnapshot`을 만든다.

    `source_hash`는 입력 구성요소 전체의 결정론적 해시다 — `(account_id,
    nav_date)`로 이미 저장된 행과 재계산 결과의 `source_hash`가 다르면
    호출자(LB-15 `compute_daily_nav` application)가 덮어쓰기를 거부하는
    근거가 된다(§8 "기존 행과 source_hash 다르면 POS_NAV_CHAIN_BROKEN").
    """
    positions_mv = sum(inputs.position_mvs, Decimal("0"))
    closing_nav = inputs.cash + positions_mv

    return NAVSnapshot(
        account_id=inputs.account_id,
        nav_date=inputs.nav_date,
        base_currency=inputs.base_currency,
        opening_nav=inputs.opening_nav,
        cash=inputs.cash,
        positions_mv=positions_mv,
        realized=inputs.realized,
        unrealized_delta=inputs.unrealized_delta,
        funding=inputs.funding,
        fees=inputs.fees,
        flows=inputs.flows,
        closing_nav=closing_nav,
        fx_rates=list(inputs.fx_rates),
        source_hash=_source_hash(inputs, positions_mv=positions_mv, closing_nav=closing_nav),
    )


def verify_chain(prev: NAVSnapshot, cur: NAVSnapshot) -> None:
    """`prev`(전일) → `cur`(당일) 롤포워드 등식을 허용오차 0으로 검증한다.

    - 연속성: `cur.opening_nav == prev.closing_nav`.
    - 롤포워드: `cur.closing_nav == cur.opening_nav + cur.realized +
      cur.unrealized_delta + cur.funding − cur.fees + cur.flows`.

    둘 중 하나라도 어긋나면 `NavChainBrokenError`를 던진다 — 근사 비교나
    quantize로 차이를 흡수하지 않는다.
    """
    if cur.opening_nav != prev.closing_nav:
        raise NavChainBrokenError(
            f"{cur.account_id}/{cur.nav_date}: opening_nav={cur.opening_nav} != "
            f"prev.closing_nav={prev.closing_nav}"
        )

    expected_closing = (
        cur.opening_nav
        + cur.realized
        + cur.unrealized_delta
        + cur.funding
        - cur.fees
        + cur.flows
    )
    if cur.closing_nav != expected_closing:
        raise NavChainBrokenError(
            f"{cur.account_id}/{cur.nav_date}: closing_nav={cur.closing_nav} != "
            f"expected(opening+realized+Δunrealized+funding-fees+flows)={expected_closing}"
        )


def _source_hash(inputs: NavInputs, *, positions_mv: Decimal, closing_nav: Decimal) -> str:
    payload = json.dumps(
        {
            "account_id": str(inputs.account_id),
            "nav_date": inputs.nav_date.isoformat(),
            "base_currency": inputs.base_currency.value,
            "opening_nav": str(inputs.opening_nav),
            "cash": str(inputs.cash),
            "positions_mv": str(positions_mv),
            "realized": str(inputs.realized),
            "unrealized_delta": str(inputs.unrealized_delta),
            "funding": str(inputs.funding),
            "fees": str(inputs.fees),
            "flows": str(inputs.flows),
            "closing_nav": str(closing_nav),
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
