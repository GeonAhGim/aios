"""BR-20a(task-7008) — NHAdapter `/krstock/quote/*` not-started 9-endpoint
extension methods (currentExecution/currentInvestor/etfComponents/
etfCurrent/period group).

Spec: docs/design/NH_COVERAGE.md `/krstock/quote/*` not-started 9 endpoints —
DTOs live in `quote_extra_dto.py`; see that module's docstring for the source
of the request/response field names. The 4 after-hours endpoints
(afterHoursCurrent/afterHoursExpected/currentAfterHoursDaily/
currentAfterHoursExecution) live in `quote_after_hours_mixin.py`.

Split into two files, and split from `market_data_mixin.py` (get_ticker/
get_orderbook/get_ohlcv/subscribe_ticker_stream, the `ExchangeAdapter` ABC
contract methods), per §7 line-cap policy (P6.line_cap, 300-line guard cap)
— market_data_mixin.py was already 219 lines and these 9 endpoints would
push a single file well past the cap. These are NH-only read extension
methods outside the ABC contract, same split rationale as
`credit_reserved_mixin.py` vs `trading_mixin.py`. No
`@require_paper_sandbox` guard is needed — these are read-only quote
lookups, not order-placing calls.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.exchanges.common.http_client import NHHTTPClient
from src.exchanges.nh.quote_extra_dto import (
    CurrentExecutionTick,
    EtfComponentQuote,
    EtfCurrentQuote,
    InvestorTradingRow,
    PeriodBar,
    PeriodQuoteRequest,
)

_MARKET_CODE = "KRX"


def _opt_decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class NHQuoteExtraMixin:
    async def get_current_execution(
        self: NHHTTPClient,
        symbol: str,
        *,
        market_cd: str = _MARKET_CODE,
        view_main_yn: str = "Y",
        array_cnt: str = "30",
    ) -> list[CurrentExecutionTick]:
        """`POST /krstock/quote/v1/currentExecution` (intraday time-bucket executions).

        Required Input_0 fields per official openapi.json: market_cd,
        iem_cd, view_main_yn.
        """
        raw = await self._request(
            "POST",
            "/krstock/quote/v1/currentExecution",
            body={
                "iem_cd": symbol,
                "market_cd": market_cd,
                "view_main_yn": view_main_yn,
                "array_cnt": array_cnt,
            },
        )
        try:
            return [
                CurrentExecutionTick(
                    time=item["bsop_hour"],
                    changed_volume=Decimal(str(item["cntg_vol"])),
                    accumulated_volume=_opt_decimal(item.get("acml_vol")),
                    ask_price=_opt_decimal(item.get("askp")),
                    bid_price=_opt_decimal(item.get("bidp")),
                    strength=_opt_decimal(item.get("cttr")),
                )
                for item in raw.get("Output_0", [])
            ]
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH currentExecution response missing expected field "
                f"(bsop_hour/cntg_vol required per official openapi.json): {exc}"
            ) from exc

    async def get_current_investor(
        self: NHHTTPClient,
        symbol: str,
        *,
        market_cd: str = _MARKET_CODE,
        array_cnt: str = "1",
    ) -> list[InvestorTradingRow]:
        """`POST /krstock/quote/v1/currentInvestor` (investor trading breakdown).

        Required Input_0 fields per official openapi.json: market_cd,
        iem_cd, array_cnt.
        """
        raw = await self._request(
            "POST",
            "/krstock/quote/v1/currentInvestor",
            body={"iem_cd": symbol, "market_cd": market_cd, "array_cnt": array_cnt},
        )
        try:
            return [
                InvestorTradingRow(
                    trade_date=item["bsop_date1"],
                    accumulated_volume=_opt_decimal(item.get("acml_vol")),
                    individual_net_buy=_opt_decimal(item.get("person")),
                    foreign_net_buy=_opt_decimal(item.get("frgn_ntby_qty")),
                    institution_net_buy=_opt_decimal(item.get("gigwan")),
                )
                for item in raw.get("Output_0", [])
            ]
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH currentInvestor response missing expected field "
                f"(bsop_date1 required per official openapi.json): {exc}"
            ) from exc

    async def get_etf_components(self: NHHTTPClient, symbol: str) -> list[EtfComponentQuote]:
        """`POST /krstock/quote/v1/etfComponents` (ETF component-stock quotes)."""
        raw = await self._request(
            "POST", "/krstock/quote/v1/etfComponents", body={"iem_cd": symbol}
        )
        try:
            return [
                EtfComponentQuote(
                    symbol=item["iem_cd"],
                    name=item.get("iem_nm"),
                    price=Decimal(str(item["stck_prpr"])),
                    cu_unit=_opt_decimal(item.get("cu_unit")),
                    weight=_opt_decimal(item.get("vol")),
                )
                for item in raw.get("Output_0", [])
            ]
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH etfComponents response missing expected field "
                f"(iem_cd/stck_prpr required per official openapi.json): {exc}"
            ) from exc

    async def get_etf_current(self: NHHTTPClient, symbol: str) -> EtfCurrentQuote:
        """`POST /krstock/quote/v1/etfCurrent` (ETF/ETN current price).

        The official snapshot notes example responses carry undocumented
        extra blocks (Output_3/Output_4) — this method only reads the
        confirmed `Output_0` fields.
        """
        raw = await self._request("POST", "/krstock/quote/v1/etfCurrent", body={"iem_cd": symbol})
        try:
            output = raw["Output_0"]
            price = Decimal(str(output["stck_prpr"]))
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH etfCurrent response missing expected field "
                f"(stck_prpr required per official openapi.json): {exc}"
            ) from exc
        return EtfCurrentQuote(
            symbol=str(output.get("iem_cd", symbol)),
            name=output.get("iem_nm"),
            price=price,
            accumulated_volume=_opt_decimal(output.get("acml_vol")),
            ask_price1=_opt_decimal(output.get("askp1")),
            bid_price1=_opt_decimal(output.get("bidp1")),
        )

    async def get_period_quote(self: NHHTTPClient, request: PeriodQuoteRequest) -> list[PeriodBar]:
        """`POST /krstock/quote/v1/period` (periodic price series -- day/week/month/year).

        Required Input_0 fields per official openapi.json: market_cd,
        iem_cd, view_main_yn. Returns the `Output_1` historical-bar array
        (see module docstring of `quote_extra_dto.py`).
        """
        body: dict[str, Any] = {
            "iem_cd": request.symbol,
            "market_cd": request.market_cd,
            "view_main_yn": request.view_main_yn,
            "gubun": request.period_code,
        }
        if request.end_date is not None:
            body["edate"] = request.end_date
        if request.array_cnt is not None:
            body["array_cnt"] = request.array_cnt

        raw = await self._request("POST", "/krstock/quote/v1/period", body=body)
        try:
            return [
                PeriodBar(
                    date=item["bsop_date"],
                    open=Decimal(str(item["stck_oprc"])),
                    high=Decimal(str(item["stck_hgpr"])),
                    low=Decimal(str(item["stck_lwpr"])),
                    close=Decimal(str(item["stck_prpr"])),
                    volume=_opt_decimal(item.get("vol")),
                    amount=_opt_decimal(item.get("tr_pbmn")),
                )
                for item in raw.get("Output_1", [])
            ]
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH period response missing expected field (bsop_date/"
                f"stck_oprc/stck_hgpr/stck_lwpr/stck_prpr required per "
                f"official openapi.json): {exc}"
            ) from exc
