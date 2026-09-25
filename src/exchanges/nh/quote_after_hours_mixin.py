"""BR-20a(task-7008) — NHAdapter after-hours quote endpoints.

Spec: docs/design/NH_COVERAGE.md `/krstock/quote/*` not-started 9 endpoints —
the 4 after-hours endpoints (afterHoursCurrent/afterHoursExpected/
currentAfterHoursDaily/currentAfterHoursExecution). DTOs live in
`quote_extra_dto.py`; see that module's docstring for the source of the
request/response field names. The remaining 5 endpoints
(currentExecution/currentInvestor/etfComponents/etfCurrent/period) live in
`quote_extra_mixin.py` — split across two files (P6.line_cap, 300-line
guard cap) rather than one, same rationale as `credit_reserved_mixin.py`
vs `trading_mixin.py`. No `@require_paper_sandbox` guard is needed — these
are read-only quote lookups, not order-placing calls.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.core.exceptions import FatalExchangeError
from src.exchanges.common.http_client import NHHTTPClient
from src.exchanges.nh.quote_extra_dto import (
    AfterHoursCurrentQuote,
    AfterHoursDailyBar,
    AfterHoursExecutionTick,
    AfterHoursExpectedTick,
)


def _opt_decimal(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


class NHQuoteAfterHoursMixin:
    async def get_after_hours_current(self: NHHTTPClient, symbol: str) -> AfterHoursCurrentQuote:
        """`POST /krstock/quote/v1/afterHoursCurrent` (after-hours current price)."""
        raw = await self._request(
            "POST", "/krstock/quote/v1/afterHoursCurrent", body={"iem_cd": symbol}
        )
        try:
            output = raw["Output_0"]
            price = Decimal(str(output["ovtm_untp_prpr"]))
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH afterHoursCurrent response missing expected field "
                f"(ovtm_untp_prpr required per official openapi.json): {exc}"
            ) from exc
        return AfterHoursCurrentQuote(
            symbol=str(output.get("iem_cd", symbol)),
            name=output.get("iem_nm"),
            price=price,
            volume=_opt_decimal(output.get("ovtm_untp_vol")),
            change=_opt_decimal(output.get("ovtm_prdy_vrss")),
            change_rate=_opt_decimal(output.get("ovtm_prdy_ctrt")),
            change_sign=output.get("prdy_vrss_sign"),
        )

    async def get_after_hours_expected(
        self: NHHTTPClient, symbol: str
    ) -> list[AfterHoursExpectedTick]:
        """`POST /krstock/quote/v1/afterHoursExpected` (after-hours expected price by hour)."""
        raw = await self._request(
            "POST", "/krstock/quote/v1/afterHoursExpected", body={"iem_cd": symbol}
        )
        try:
            return [
                AfterHoursExpectedTick(
                    symbol=str(item.get("iem_cd", symbol)),
                    time=item["bsop_hour"],
                    price=Decimal(str(item["stck_prpr"])),
                    volume=Decimal(str(item["cntg_vol"])),
                    ask_price1=_opt_decimal(item.get("askp1")),
                    bid_price1=_opt_decimal(item.get("bidp1")),
                )
                for item in raw.get("Output_0", [])
            ]
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH afterHoursExpected response missing expected field "
                f"(bsop_hour/stck_prpr/cntg_vol required per official "
                f"openapi.json): {exc}"
            ) from exc

    async def get_after_hours_daily(
        self: NHHTTPClient,
        symbol: str,
        *,
        date: str,
        array_cnt: str = "30",
        maxavg: str = "0",
        gubun: str = "1",
    ) -> list[AfterHoursDailyBar]:
        """`POST /krstock/quote/v1/currentAfterHoursDaily` (after-hours daily price series).

        Required Input_0 fields per official openapi.json: iem_cd, date,
        array_cnt, maxavg, gubun. The response zips `Output_0[i]`/
        `Output_1[i]` (see module docstring of `quote_extra_dto.py`).
        """
        raw = await self._request(
            "POST",
            "/krstock/quote/v1/currentAfterHoursDaily",
            body={
                "iem_cd": symbol,
                "date": date,
                "array_cnt": array_cnt,
                "maxavg": maxavg,
                "gubun": gubun,
            },
        )
        names = raw.get("Output_0", [])
        stats = raw.get("Output_1", [])
        if len(names) != len(stats):
            raise FatalExchangeError(
                f"NH currentAfterHoursDaily Output_0/Output_1 length mismatch: "
                f"{len(names)} vs {len(stats)}"
            )
        try:
            return [
                AfterHoursDailyBar(
                    date=name_row["qry_date"],
                    time=name_row.get("qry_time"),
                    price=name_row.get("stck_prpr"),
                    name=name_row.get("hts_kor_isnm"),
                    volume=_opt_decimal(stat_row.get("acml_vol")),
                    amount=_opt_decimal(stat_row.get("acml_tr_pbmn")),
                    change_rate=_opt_decimal(stat_row.get("prdy_ctrt")),
                )
                for name_row, stat_row in zip(names, stats, strict=True)
            ]
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH currentAfterHoursDaily response missing expected field "
                f"(qry_date required per official openapi.json): {exc}"
            ) from exc

    async def get_after_hours_execution(
        self: NHHTTPClient, symbol: str
    ) -> list[AfterHoursExecutionTick]:
        """`POST /krstock/quote/v1/currentAfterHoursExecution` (after-hours execution by hour)."""
        raw = await self._request(
            "POST",
            "/krstock/quote/v1/currentAfterHoursExecution",
            body={"iem_cd": symbol},
        )
        try:
            return [
                AfterHoursExecutionTick(
                    symbol=str(item.get("iem_cd", symbol)),
                    time=item["bsop_hour"],
                    price=Decimal(str(item["stck_prpr"])),
                    volume=Decimal(str(item["cntg_vol"])),
                    accumulated_volume=_opt_decimal(item.get("acml_vol")),
                    ask_price1=_opt_decimal(item.get("askp1")),
                    bid_price1=_opt_decimal(item.get("bidp1")),
                )
                for item in raw.get("Output_0", [])
            ]
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH currentAfterHoursExecution response missing expected field "
                f"(bsop_hour/stck_prpr/cntg_vol required per official "
                f"openapi.json): {exc}"
            ) from exc
