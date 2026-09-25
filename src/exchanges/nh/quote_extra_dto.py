"""BR-20a(task-7008) — NH `/krstock/quote/*` not-started 9-endpoint request/
response DTOs.

Spec: docs/design/NH_COVERAGE.md `/krstock/quote/*` not-started 9 endpoints —
afterHoursCurrent/afterHoursExpected/currentAfterHoursDaily/
currentAfterHoursExecution/currentExecution/currentInvestor/etfComponents/
etfCurrent/period. Field names and required flags are copied verbatim from
each endpoint's `request_params[0].schema.properties.Input_0` /
`response_schema.properties` in `docs/design/nh_openapi_reference.json`
(official openapi.json snapshot, same source as market_data_mixin.py's
currentPrice/currentDaily).

These 9 endpoints have no room in the shared `Ticker`/`Candle`/`OrderBook`
domain models (per-endpoint fields like ETF CU unit size, after-hours
expected execution, investor net-buy have no cross-venue equivalent) —
hence dedicated DTOs, same pattern
as `credit_reserved_dto.py`.

`currentAfterHoursDaily`'s response is unusual: the official schema splits
one logical row across two parallel arrays (`Output_0`/`Output_1`, same
index) — `get_after_hours_daily` zips them by index.

`period`'s response has a single-object `Output_0` (current snapshot,
mirrors currentPrice) plus an `Output_1` array of historical bars (mirrors
currentDaily) — `get_period_quote` returns the `Output_1` bars, since the
periodic-series data is this endpoint's distinguishing feature over the
already-implemented currentPrice/currentDaily.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class AfterHoursCurrentQuote:
    """`afterHoursCurrent` response (Output_0, single object)."""

    symbol: str  # iem_cd
    name: str | None  # iem_nm
    price: Decimal  # ovtm_untp_prpr -- after-hours single-price current price
    volume: Decimal | None  # ovtm_untp_vol
    change: Decimal | None  # ovtm_prdy_vrss
    change_rate: Decimal | None  # ovtm_prdy_ctrt
    change_sign: str | None  # prdy_vrss_sign


@dataclass(frozen=True)
class AfterHoursExpectedTick:
    """`afterHoursExpected` response (Output_0 array item)."""

    symbol: str  # iem_cd
    time: str  # bsop_hour
    price: Decimal  # stck_prpr
    volume: Decimal  # cntg_vol
    ask_price1: Decimal | None  # askp1
    bid_price1: Decimal | None  # bidp1


@dataclass(frozen=True)
class AfterHoursDailyBar:
    """`currentAfterHoursDaily` response — Output_0[i]/Output_1[i] zipped."""

    date: str  # qry_date
    time: str | None  # qry_time
    price: str | None  # stck_prpr (string, per schema)
    name: str | None  # hts_kor_isnm
    volume: Decimal | None  # acml_vol
    amount: Decimal | None  # acml_tr_pbmn
    change_rate: Decimal | None  # prdy_ctrt


@dataclass(frozen=True)
class AfterHoursExecutionTick:
    """`currentAfterHoursExecution` response (Output_0 array item)."""

    symbol: str  # iem_cd
    time: str  # bsop_hour
    price: Decimal  # stck_prpr
    volume: Decimal  # cntg_vol
    accumulated_volume: Decimal | None  # acml_vol
    ask_price1: Decimal | None  # askp1
    bid_price1: Decimal | None  # bidp1


@dataclass(frozen=True)
class CurrentExecutionTick:
    """`currentExecution` response (Output_0 array item)."""

    time: str  # bsop_hour
    changed_volume: Decimal  # cntg_vol
    accumulated_volume: Decimal | None  # acml_vol
    ask_price: Decimal | None  # askp
    bid_price: Decimal | None  # bidp
    strength: Decimal | None  # cttr


@dataclass(frozen=True)
class InvestorTradingRow:
    """`currentInvestor` response (Output_0 array item)."""

    trade_date: str  # bsop_date1
    accumulated_volume: Decimal | None  # acml_vol
    individual_net_buy: Decimal | None  # person
    foreign_net_buy: Decimal | None  # frgn_ntby_qty
    institution_net_buy: Decimal | None  # gigwan


@dataclass(frozen=True)
class EtfComponentQuote:
    """`etfComponents` response (Output_0 array item)."""

    symbol: str  # iem_cd
    name: str | None  # iem_nm
    price: Decimal  # stck_prpr
    cu_unit: Decimal | None  # cu_unit
    weight: Decimal | None  # vol -- weight in the ETF basket


@dataclass(frozen=True)
class EtfCurrentQuote:
    """`etfCurrent` response (Output_0, single object)."""

    symbol: str  # iem_cd
    name: str | None  # iem_nm
    price: Decimal  # stck_prpr
    accumulated_volume: Decimal | None  # acml_vol
    ask_price1: Decimal | None  # askp1
    bid_price1: Decimal | None  # bidp1


@dataclass(frozen=True)
class PeriodQuoteRequest:
    """`period` request (Input_0) — only market_cd/iem_cd/view_main_yn are
    required per schema; `period_code` selects day/week/month/year (gubun 1-4)."""

    symbol: str  # iem_cd
    market_cd: str = "KRX"
    view_main_yn: str = "Y"
    period_code: str = "1"  # gubun -- 1.day 2.week 3.month 4.year
    end_date: str | None = None  # edate
    array_cnt: str | None = None  # array_cnt


@dataclass(frozen=True)
class PeriodBar:
    """`period` response Output_1 array item (historical bar series)."""

    date: str  # bsop_date
    open: Decimal  # stck_oprc
    high: Decimal  # stck_hgpr
    low: Decimal  # stck_lwpr
    close: Decimal  # stck_prpr
    volume: Decimal | None  # vol
    amount: Decimal | None  # tr_pbmn
