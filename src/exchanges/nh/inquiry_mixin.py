"""BR-20b(task-7009) -- NHAdapter krstock/inquiry extension, part 1/2.

Spec: docs/design/NH_COVERAGE.md `/krstock/inquiry/*` not-started 10 --
DTOs live in `inquiry_dto.py` (see that module's docstring for field sources).
Covers assetStatus/buyableQuantity/dailyPnl/integratedMargin/realizedPnl;
the remaining 5 endpoints are in `inquiry_extra_mixin.py` (split to stay
under the mixin file's P6.line_cap).

These methods sit outside the `ExchangeAdapter` ABC contract (same call as
credit_reserved_mixin.py) -- they are NH-specific extension queries and are
not forced into the standard interface the way get_balance/get_positions
are. All of them are read-only inquiry endpoints (POST verb, but read-only
semantics), so `@require_paper_sandbox` is not applied (same rationale as
get_balance/get_positions -- an account inquiry has no dangerous side effect
even under LIVE credentials, unlike order placement).

Each method raises `FatalExchangeError` when an expected response field is
missing (never silently defaults to 0/None -- per PM assignment note (2),
same principle as get_balance).
"""

from __future__ import annotations

from typing import Any

from src.core.exceptions import FatalExchangeError
from src.exchanges.common.http_client import NHHTTPClient
from src.exchanges.nh.inquiry_dto import (
    AssetStatusHolding,
    AssetStatusRequest,
    AssetStatusResponse,
    BuyableQuantityRequest,
    BuyableQuantityResponse,
    DailyPnlRequest,
    DailyPnlResponse,
    DailyPnlRow,
    IntegratedMarginResponse,
    RealizedPnlHolding,
    RealizedPnlRequest,
    RealizedPnlResponse,
    _dec,
    _opt_dec,
)


def _output0(raw: dict[str, Any], path: str) -> dict[str, Any]:
    output = raw.get("Output_0")
    if not isinstance(output, dict):
        raise FatalExchangeError(
            f"NH {path} response missing Output_0 (required per official openapi.json)"
        )
    return output


class NHInquiryMixin:
    async def get_asset_status(
        self: NHHTTPClient, request: AssetStatusRequest
    ) -> AssetStatusResponse:
        """`POST /krstock/inquiry/v1/assetStatus` (investment account asset status)."""
        body = {
            "act_no": self._act_no,
            "aet_bse": request.net_or_gross_code,
            "eal_aly_cd": request.valuation_apply_code,
            "qut_dit_cd": request.quote_division_code,
            "aly_qut_cd": request.applied_quote_code,
        }
        raw = await self._request("POST", "/krstock/inquiry/v1/assetStatus", body=body)
        try:
            summary = _output0(raw, "assetStatus")
            deposit = _dec(summary["dca"])
            total_asset = _dec(summary["tot_aet_amt"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH assetStatus response missing expected field (dca/tot_aet_amt required): {exc}"
            ) from exc
        holdings = [
            AssetStatusHolding(
                symbol=row["iem_cd"],
                name=row.get("iem_nm"),
                quantity=_dec(row["itg_bnc_qty"]),
                eval_amount=_opt_dec(row.get("eal_amt")),
                eval_profit_amount=_opt_dec(row.get("eal_pls_amt")),
                raw=row,
            )
            for row in raw.get("Output_1", [])
        ]
        return AssetStatusResponse(
            deposit=deposit,
            total_asset_amount=total_asset,
            total_eval_amount=_opt_dec(summary.get("tot_eal_amt")),
            total_eval_profit_amount=_opt_dec(summary.get("tot_eal_pls_amt")),
            holdings=holdings,
            raw=summary,
        )

    async def get_buyable_quantity(
        self: NHHTTPClient, request: BuyableQuantityRequest
    ) -> BuyableQuantityResponse:
        """`POST /krstock/inquiry/v1/buyableQuantity` (buyable quantity inquiry)."""
        body: dict[str, Any] = {
            "act_no": self._act_no,
            "iem_cd": request.symbol,
            "ost_dit_cd": request.division_code,
            "nmn_pr_tp_cd": request.order_price_type_code,
        }
        if request.order_price is not None:
            body["orr_pr"] = str(request.order_price)
        if request.credit_loan_code is not None:
            body["cfd_lon_cd"] = request.credit_loan_code
        if request.loan_date is not None:
            body["lon_dt"] = request.loan_date

        raw = await self._request("POST", "/krstock/inquiry/v1/buyableQuantity", body=body)
        try:
            output = _output0(raw, "buyableQuantity")
            quantity = _dec(output["csh_orr_pbl_qty"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH buyableQuantity response missing expected field "
                f"(csh_orr_pbl_qty required): {exc}"
            ) from exc
        return BuyableQuantityResponse(
            cash_buyable_quantity=quantity,
            cash_buyable_amount=_opt_dec(output.get("csh_orr_pbl_amt")),
            max_buyable_quantity=_opt_dec(output.get("max_pbl_qty")),
            raw=output,
        )

    async def get_daily_pnl(self: NHHTTPClient, request: DailyPnlRequest) -> DailyPnlResponse:
        """`POST /krstock/inquiry/v1/dailyPnl` (realized P&L daily aggregate)."""
        body: dict[str, Any] = {
            "act_no": self._act_no,
            "iqr_sta_dt": request.start_date,
            "iqr_end_dt": request.end_date,
        }
        if request.symbol is not None:
            body["iem_cd"] = request.symbol

        raw = await self._request("POST", "/krstock/inquiry/v1/dailyPnl", body=body)
        try:
            summary = _output0(raw, "dailyPnl")
            total_profit = _dec(summary["pls_amt_sum"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH dailyPnl response missing expected field (pls_amt_sum required): {exc}"
            ) from exc
        rows = [
            DailyPnlRow(
                trade_date=row["sby_dt"],
                buy_amount=_opt_dec(row.get("byn_amt")),
                sell_amount=_opt_dec(row.get("sll_amt")),
                profit_amount=_opt_dec(row.get("pls_amt")),
                raw=row,
            )
            for row in raw.get("Output_1", [])
        ]
        return DailyPnlResponse(
            total_profit_amount=total_profit,
            total_buy_cost=_opt_dec(summary.get("byn_cst_sum")),
            total_sell_cost=_opt_dec(summary.get("sll_cst_sum")),
            rows=rows,
            raw=summary,
        )

    async def get_integrated_margin(self: NHHTTPClient) -> IntegratedMarginResponse:
        """`POST /krstock/inquiry/v1/integratedMargin` (integrated margin status).
        The only request field is act_no, so no dedicated Request DTO is needed."""
        raw = await self._request(
            "POST", "/krstock/inquiry/v1/integratedMargin", body={"act_no": self._act_no}
        )
        try:
            output = _output0(raw, "integratedMargin")
            limit_amount = _dec(output["lmt_amt"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH integratedMargin response missing expected field (lmt_amt required): {exc}"
            ) from exc
        return IntegratedMarginResponse(
            limit_amount=limit_amount,
            limit_used_amount=_opt_dec(output.get("lmt_use_amt")),
            remaining_limit_amount=_opt_dec(output.get("rmn_lmt_amt")),
            raw=output,
        )

    async def get_realized_pnl(
        self: NHHTTPClient, request: RealizedPnlRequest
    ) -> RealizedPnlResponse:
        """`POST /krstock/inquiry/v1/realizedPnl` (stock balance with realized P&L)."""
        body = {
            "act_no": self._act_no,
            "iqr_dit_cd1": request.inquiry_division_code,
            "fee_dit_cd": request.fee_division_code,
            "qut_dit_cd": request.quote_division_code,
            "aly_qut_cd": request.applied_quote_code,
        }
        raw = await self._request("POST", "/krstock/inquiry/v1/realizedPnl", body=body)
        try:
            summary = _output0(raw, "realizedPnl")
            eval_sum = _dec(summary["eal_amt_sum"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH realizedPnl response missing expected field (eal_amt_sum required): {exc}"
            ) from exc
        holdings = [
            RealizedPnlHolding(
                symbol=row["iem_cd"],
                name=row.get("iem_nm"),
                quantity=_dec(row["itg_bnc_qty"]),
                realized_profit_amount=_opt_dec(row.get("rzt_pls_amt")),
                raw=row,
            )
            for row in raw.get("Output_1", [])
        ]
        return RealizedPnlResponse(
            eval_amount_sum=eval_sum,
            eval_profit_amount=_opt_dec(summary.get("eal_pls_amt")),
            holdings=holdings,
            raw=summary,
        )
