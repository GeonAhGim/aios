"""LB-15 — 세션 마감 기준 일별 NAV 산출·체인 검증·저장
(application/compute_daily_nav.py).

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§2.3, §9.3 LB-15.

이 함수는 LB-6 `domain/nav.compute_daily_nav`/`verify_chain`을 호출만 하고
NAV 수식을 재구현하지 않는다(task-714 DoD 1). 이 리프가 실제로 하는 일은
입력 조립과 저장 위임뿐이다.

대차대조 쪽(`cash`/`position_mvs`)은 라이브 소스에서 이 함수가 직접
채운다 — `cash`는 `CashSource`, 포지션 시가평가는 `SnapshotRepository.
list_open`이 돌려주는 `mark_price`(LB-14 `mark_positions`가 이미 채워 둔
값)를 `fx`로 기준통화 환산한다. 이 함수는 `MarkPriceSource`를 다시
호출하지 않는다(시그니처에 없음, §2.3 표 그대로) — "마크·환율은 LB-14
소스를 통해서만 얻는다"는 task-714 DoD 2는 이 캐시된 값이 LB-14 경로를
거쳐서만 채워진다는 사실로 이미 성립한다. 열린 포지션 중 하나라도
`mark_price`가 `None`(스테일 포함, task-654 decision)이면 그 계좌 전체
NAV 산출을 거부한다 — 나머지를 추정치로 메우지 않는다. `fx.rate`가 없거나
스테일해도 동일하게 거부한다(`domain/fx.FxRateMissingError`/
`FxRateStaleError`를 그대로 전파).

롤포워드 쪽(`realized`/`unrealized_delta`/`funding`/`fees`/`flows`)은
`cmd`가 이미 계산된 일별 값으로 받는다. 미검증: 이 값들을 저널에서 집계해
채우는 호출부(스케줄러, LB-17)는 이 리프 범위 밖이라 아직 없다 —
task-654처럼 다음 리프가 발명하지 않도록 여기 남겨 둔다.

전일 NAV(`opening_nav`)는 LA-3 `VenueCalendar`로 전 영업일을 판정한 뒤
그 날짜로 `nav_repo.get`을 조회한다(DoD 5). 전일 행이 없으면(계좌 첫 NAV)
`opening_nav=0`인 "genesis" 스냅샷을 만들어 `verify_chain`에 그대로
통과시킨다 — 첫날만 다른 코드 경로를 타지 않는다.

저장은 `nav_repo.insert`(LB-9 `PostgresNavRepository`)에 위임한다 —
`(account_id, nav_date)` UNIQUE + `source_hash` 비교로 재실행 멱등과 체인
위반 거부(다른 `source_hash`)를 어댑터가 이미 구현해 뒀다(task-714 DoD 3).
`verify_chain`은 그와 별개로 저장 *전에* 롤포워드 등식 자체(이번 계산이
내적으로 일관적인가)를 검증한다(DoD 4) — 어댑터의 `source_hash` 비교(이전
계산과 지금 계산이 같은가)와는 잡아내는 실패가 다르다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Protocol, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import AwareDatetime, BaseModel

from src.data.models.base import Currency, FXRate, Money
from src.foundation.market_data.domain.calendar.session_rules import VenueCalendar
from src.foundation.positions.contracts.v1 import NAVSnapshot, PositionSnapshotView
from src.foundation.positions.domain import fx as fx_calc
from src.foundation.positions.domain import nav
from src.foundation.positions.ports.fx_rate_source import FxRateSource
from src.foundation.positions.ports.nav_repository import NavRepository
from src.foundation.positions.ports.snapshot_repository import SnapshotRepository

__all__ = [
    "CashSource",
    "ComputeDailyNavCommand",
    "NavCashUnavailableError",
    "NavMarkUnavailableError",
    "compute_daily_nav",
]

_LOOKBACK_DAYS = 30


class NavCashUnavailableError(Exception):
    """`cash` 소스가 값을 못 주면(연결 끊김·미초기화) `0`으로 대체하지
    않고 NAV 산출 자체를 거부한다."""

    def __init__(self, account_id: UUID) -> None:
        super().__init__(f"{account_id}: 현금 잔고를 가져올 수 없습니다 — NAV 산출 거부")
        self.account_id = account_id


class NavMarkUnavailableError(Exception):
    """열린 포지션 중 하나라도 `mark_price`가 없으면(스테일 포함) 그
    포지션 하나 때문에 계좌 전체 NAV 산출을 거부한다(추정치 대입 금지,
    task-714 DoD 2)."""

    def __init__(self, position_key: str) -> None:
        super().__init__(
            f"{position_key}: mark_price가 없습니다(스테일/미수신) — NAV 산출 거부"
        )
        self.position_key = position_key


@runtime_checkable
class CashSource(Protocol):
    """계좌의 기준통화 표시 현금 잔고. 값이 없으면 `0`으로 대체하지 않고
    `None`을 돌려준다 — 다른 LB-14 계열 포트와 같은 계약. 미검증: 실제
    어댑터(거래소/원장 잔고를 래핑)는 아직 없다(이 리프 범위 밖)."""

    async def cash(self, account_id: UUID, at: AwareDatetime) -> Decimal | None: ...


class ComputeDailyNavCommand(BaseModel):
    """`compute_daily_nav`의 입력. `cash`/`position_mvs`는 라이브 소스에서
    이 함수가 직접 채우므로 커맨드에 없다 — 롤포워드 쪽(`realized`~
    `flows`)만 이미 계산된 일별 값으로 받는다(모듈독스트링 참고)."""

    tenant_id: UUID
    account_id: UUID
    base_currency: Currency
    at: AwareDatetime
    realized: Decimal
    unrealized_delta: Decimal
    funding: Decimal
    fees: Decimal
    flows: Decimal
    trace_id: UUID


def _previous_trading_day(calendar: VenueCalendar, day: date) -> date | None:
    for offset in range(1, _LOOKBACK_DAYS + 1):
        candidate = day - timedelta(days=offset)
        if calendar.sessions_for(candidate):
            return candidate
    return None


def _genesis(account_id: UUID, nav_date: date, base_currency: Currency) -> NAVSnapshot:
    """전일 NAV 행이 없을 때(계좌 첫 NAV) `verify_chain`에 넘길 자리
    표시자 — `closing_nav=0`이라 연속성 검사(`cur.opening_nav==prev.
    closing_nav`)가 `opening_nav=0`인 첫날과 그대로 맞는다."""

    zero = Decimal("0")
    return NAVSnapshot(
        account_id=account_id,
        nav_date=nav_date - timedelta(days=1),
        base_currency=base_currency,
        opening_nav=zero,
        cash=zero,
        positions_mv=zero,
        realized=zero,
        unrealized_delta=zero,
        funding=zero,
        fees=zero,
        flows=zero,
        closing_nav=zero,
        fx_rates=[],
        source_hash="genesis",
    )


@dataclass(frozen=True, slots=True)
class _PositionsMv:
    values: list[Decimal]
    fx_rates: list[FXRate]


async def _positions_mv(
    open_positions: list[PositionSnapshotView],
    base_currency: Currency,
    at: AwareDatetime,
    *,
    fx: FxRateSource,
) -> _PositionsMv:
    values: list[Decimal] = []
    fx_rates: list[FXRate] = []
    for snapshot in open_positions:
        if snapshot.mark_price is None:
            raise NavMarkUnavailableError(snapshot.position_key)
        gross = Money(
            amount=snapshot.quantity * snapshot.mark_price.amount,
            currency=snapshot.mark_price.currency,
        )
        rate: FXRate | None = None
        if gross.currency != base_currency:
            rate = await fx.rate(gross.currency, base_currency, at)
        converted = fx_calc.convert(gross, base_currency, rate, now=at)
        if converted.rate is not None:
            fx_rates.append(converted.rate)
        values.append(converted.amount)
    return _PositionsMv(values=values, fx_rates=fx_rates)


async def compute_daily_nav(
    cmd: ComputeDailyNavCommand,
    *,
    snapshots: SnapshotRepository,
    cash: CashSource,
    nav_repo: NavRepository,
    calendar: VenueCalendar,
    fx: FxRateSource,
    pool: asyncpg.Pool,
) -> NAVSnapshot:
    nav_date = calendar.trading_day_of(cmd.at)

    async with pool.acquire() as conn:
        open_positions = await snapshots.list_open(conn, cmd.tenant_id, cmd.account_id)
        prev_day = _previous_trading_day(calendar, nav_date)
        prev_nav = await nav_repo.get(conn, cmd.account_id, prev_day) if prev_day else None

    cash_balance = await cash.cash(cmd.account_id, cmd.at)
    if cash_balance is None:
        raise NavCashUnavailableError(cmd.account_id)

    mv = await _positions_mv(open_positions, cmd.base_currency, cmd.at, fx=fx)

    opening_nav = prev_nav.closing_nav if prev_nav is not None else Decimal("0")
    inputs = nav.NavInputs(
        account_id=cmd.account_id,
        nav_date=nav_date,
        base_currency=cmd.base_currency,
        opening_nav=opening_nav,
        cash=cash_balance,
        position_mvs=mv.values,
        realized=cmd.realized,
        unrealized_delta=cmd.unrealized_delta,
        funding=cmd.funding,
        fees=cmd.fees,
        flows=cmd.flows,
        fx_rates=mv.fx_rates,
    )
    candidate = nav.compute_daily_nav(inputs)
    nav.verify_chain(
        prev_nav if prev_nav is not None else _genesis(cmd.account_id, nav_date, cmd.base_currency),
        candidate,
    )

    async with pool.acquire() as conn:
        return await nav_repo.insert(conn, candidate)
