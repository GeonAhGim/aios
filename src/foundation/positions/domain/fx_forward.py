"""LB-20 — 선물환·헤지 손익(fx_forward).

Spec: docs/design/ADR-2026-09-06-G-second-audit-corrections.md §9
("FX가 현물 환산뿐, 기관 고시환율·선물환·헤지 손익 없음"),
docs/specs/L4_market_data_positions_ledger_v1.0.md §9.

[[fx.convert]](LB-4)는 현물 환산만 한다. 이 모듈은 그 위에 (1) 선물환 계약의
미실현 손익을 자산 자체의 미실현 FX 손익과 분리해 계산하고, (2) 미헤지
잔여 익스포저를 산출하며, (3) 사용한 고시의 출처(`source`)와 확정 시각
(`known_at`)을 결과에 동봉해 재현 가능하게 만든다. `fx.py`와 동일하게
삼각환산·테너 불일치를 조용히 흡수하지 않는다. 순수 함수만 — I/O·시계
직접 호출 금지.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal

from src.data.models.base import Currency, Money


class ForwardRateMismatchError(Exception):
    """요청한 (통화쌍, 결제일)과 다른 선물환 고시로 재평가하려 했다 —
    다른 테너·통화쌍의 고시로 대체(보간)하지 않는다. `fx.py`의 삼각환산
    금지 원칙과 동일한 결을 따른다."""

    def __init__(self, expected: tuple[Currency, Currency, date], got: ForwardQuote) -> None:
        exp_base, exp_quote, exp_settlement = expected
        super().__init__(
            f"기대: {exp_base.value}/{exp_quote.value} 결제 {exp_settlement.isoformat()}, "
            f"실제: {got.base.value}/{got.quote.value} 결제 {got.settlement_date.isoformat()} "
            f"(source={got.source}, known_at={got.known_at.isoformat()})"
        )
        self.expected = expected
        self.got = got


class HedgeQuoteCurrencyMismatchError(Exception):
    """헤지 손익을 자산 미실현 FX 손익과 합산하려는데 표시통화(quote)가
    서로 다르다 — 암묵적 재환산 없이 거부한다."""

    def __init__(self, currencies: set[Currency]) -> None:
        super().__init__(f"단일 표시통화가 아닙니다: {sorted(c.value for c in currencies)}")
        self.currencies = currencies


@dataclass(frozen=True, slots=True)
class ForwardQuote:
    """선물환 고시 한 건.

    현물 `FXRate`(fx.py)와 달리 `settlement_date`(결제일)를 갖는다.
    `known_at`은 이 고시가 확정·기록된 시각이다 — 감사 시 "그 시점에 어떤
    환율을 근거로 계산했는가"를 재현하기 위해 별도 필드로 보관한다
    (DoD: "환율 출처가 known_at과 함께 기록돼 재현 가능").
    """

    base: Currency
    quote: Currency
    rate: Decimal
    settlement_date: date
    known_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class FxHedgeContract:
    """헤지용 선물환 계약 한 건.

    `notional`의 부호가 방향을 정한다: 양수는 `base` 통화 롱 익스포저를
    상쇄하기 위한 선물 매도(결제일에 base 인도, quote 수령), 음수는 그
    반대(선물 매수)다. 부호를 이렇게 통일하면 두 방향 모두
    `unrealized = notional * (contract_rate - market_rate)`로 계산된다.
    """

    contract_id: str
    base: Currency
    quote: Currency
    notional: Decimal
    contract_rate: Decimal
    settlement_date: date
    known_at: datetime
    source: str


@dataclass(frozen=True, slots=True)
class HedgePnl:
    """[[hedge_unrealized_pnl]] 결과. `rate_source`·`rate_known_at`을
    동봉해 어떤 고시로 계산했는지 재현 가능하게 한다."""

    contract_id: str
    unrealized: Money
    rate_source: str
    rate_known_at: datetime


def hedge_unrealized_pnl(contract: FxHedgeContract, market: ForwardQuote) -> HedgePnl:
    """`contract`를 같은 결제일의 현재 시장 선물환(`market`)으로 재평가한다.

    `market`의 `(base, quote, settlement_date)`가 `contract`와 정확히
    일치해야 한다. 일치하지 않으면 `ForwardRateMismatchError`를 던진다 —
    가장 가까운 테너로 대체하는 보간을 하지 않는다.
    """
    expected = (contract.base, contract.quote, contract.settlement_date)
    got = (market.base, market.quote, market.settlement_date)
    if got != expected:
        raise ForwardRateMismatchError(expected, market)

    unrealized = contract.notional * (contract.contract_rate - market.rate)
    return HedgePnl(
        contract_id=contract.contract_id,
        unrealized=Money(amount=unrealized, currency=contract.quote),
        rate_source=market.source,
        rate_known_at=market.known_at,
    )


@dataclass(frozen=True, slots=True)
class FxPnlBreakdown:
    """NAV 분해용 — 자산 자체의 미실현 FX 손익과 헤지 미실현 손익을 분리해
    보관한다. 둘을 합친 `net`은 참고값일 뿐, NAV 표시 시 두 성분은 항상
    구분해 노출해야 한다(DoD)."""

    currency: Currency
    asset_unrealized_fx: Decimal
    hedge_unrealized_fx: Decimal

    @property
    def net(self) -> Decimal:
        return self.asset_unrealized_fx + self.hedge_unrealized_fx


def decompose_fx_pnl(asset_unrealized_fx: Money, hedge_pnls: Sequence[HedgePnl]) -> FxPnlBreakdown:
    """자산 자체의 미실현 FX 손익과 헤지 미실현 손익을 같은 표시통화
    기준으로 분리 집계한다.

    `hedge_pnls`의 표시통화가 서로 다르거나 `asset_unrealized_fx.currency`
    와 다르면 `HedgeQuoteCurrencyMismatchError`를 던진다 — 암묵적
    재환산 없이 거부한다.
    """
    currencies = {p.unrealized.currency for p in hedge_pnls} | {asset_unrealized_fx.currency}
    if len(currencies) > 1:
        raise HedgeQuoteCurrencyMismatchError(currencies)

    hedge_total = sum((p.unrealized.amount for p in hedge_pnls), Decimal("0"))
    return FxPnlBreakdown(
        currency=asset_unrealized_fx.currency,
        asset_unrealized_fx=asset_unrealized_fx.amount,
        hedge_unrealized_fx=hedge_total,
    )


def unhedged_exposure(
    gross_exposure: Decimal,
    hedges: Sequence[FxHedgeContract],
    *,
    base: Currency,
    quote: Currency,
    quantize_to: int | None = None,
) -> Decimal:
    """`base`/`quote` 익스포저 중 헤지되지 않은 잔여분을 계산한다.

    `hedges`는 `(base, quote)`가 정확히 일치하는 계약만 상쇄에 반영한다
    — 다른 통화쌍의 계약이 섞여 있어도 조용히 무시할 뿐 합산하지 않는다.
    `quantize_to`가 주어지면 소수 n자리로 반올림(ROUND_HALF_EVEN)하고,
    생략하면 원 정밀도를 그대로 반환한다 — 암묵적 반올림 금지.
    """
    hedged = sum(
        (h.notional for h in hedges if h.base == base and h.quote == quote),
        Decimal("0"),
    )
    result = gross_exposure - hedged
    if quantize_to is None:
        return result
    exponent = Decimal("1").scaleb(-quantize_to)
    return result.quantize(exponent, rounding=ROUND_HALF_EVEN)
