"""BR-20b(task-7009) -- NHAdapter krstock/inquiry extension, part 2/2.

Spec: docs/design/NH_COVERAGE.md `/krstock/inquiry/*` not-started 10 --
DTOs live in `inquiry_dto.py` (see that module's docstring for field sources).
Covers reservedInquiry/rightsHeld/rightsScheduled/sellableQuantity/tradingPnl;
the first 5 endpoints are in `inquiry_mixin.py` (split to stay under the
mixin file's P6.line_cap).

Same design as `inquiry_mixin.py`: these methods sit outside the
`ExchangeAdapter` ABC contract, no `@require_paper_sandbox` (read-only), and
raise `FatalExchangeError` on a missing required response field instead of
silently defaulting.

For `rightsHeld`/`rightsScheduled`, the official spec labels the
account-number field "rights account number" separately from the ordinary
account number (whether it shares the same digit format can only be
confirmed against a live account) -- this adapter uses `self._act_no` here
just like every other method; the possibility that the value uses a
different scheme is left **unverified**.
"""

from __future__ import annotations

from typing import Any

from src.core.exceptions import FatalExchangeError
from src.exchanges.common.http_client import NHHTTPClient
from src.exchanges.nh.inquiry_dto import (
    ReservedInquiryRequest,
    ReservedInquiryRow,
    RightsHeldRequest,
    RightsHeldRow,
    RightsScheduledRow,
    SellableQuantityRequest,
    SellableQuantityResponse,
    TradingPnlRequest,
    TradingPnlResponse,
    TradingPnlRow,
    _dec,
    _opt_dec,
)
from src.exchanges.nh.inquiry_mixin import NHInquiryMixin, _output0


class NHInquiryExtraMixin(NHInquiryMixin):
    async def get_reserved_orders(
        self: NHHTTPClient, request: ReservedInquiryRequest
    ) -> list[ReservedInquiryRow]:
        """`POST /krstock/inquiry/v1/reservedInquiry` (reserved order inquiry)."""
        body: dict[str, Any] = {
            "act_no": self._act_no,
            "sby_dit_cd": request.side_code,
            "bkg_orr_tp_cd": request.reserved_order_type_code,
        }
        if request.symbol is not None:
            body["iem_cd"] = request.symbol
        if request.credit_loan_code is not None:
            body["cfd_lon_cd"] = request.credit_loan_code
        if request.cancel_division_code is not None:
            body["bkg_orr_can_dit_cd"] = request.cancel_division_code
        if request.reserved_receipt_date is not None:
            body["bkg_orr_rtn_dt"] = request.reserved_receipt_date

        raw = await self._request("POST", "/krstock/inquiry/v1/reservedInquiry", body=body)
        rows = []
        try:
            for row in raw.get("Output_1", []):
                rows.append(
                    ReservedInquiryRow(
                        reserved_order_no=str(row["bkg_rtn_orr_no"]),
                        symbol=row["iem_cd"],
                        side_code=row.get("sby_dit_cd"),
                        quantity=_opt_dec(row.get("orr_qty")),
                        price=_opt_dec(row.get("orr_pr")),
                        raw=row,
                    )
                )
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH reservedInquiry response missing expected field "
                f"(bkg_rtn_orr_no/iem_cd required): {exc}"
            ) from exc
        return rows

    async def get_rights_held(
        self: NHHTTPClient, request: RightsHeldRequest
    ) -> list[RightsHeldRow]:
        """`POST /krstock/inquiry/v1/rightsHeld` (held corporate-action rights over a period)."""
        body: dict[str, Any] = {"act_no": self._act_no}
        if request.rights_type_code is not None:
            body["rit_tp_cd"] = request.rights_type_code
        if request.start_date is not None:
            body["sta_dt"] = request.start_date

        raw = await self._request("POST", "/krstock/inquiry/v1/rightsHeld", body=body)
        rows = []
        try:
            for row in raw.get("Output_1", []):
                rows.append(
                    RightsHeldRow(
                        symbol=row["iem_cd"],
                        name=row.get("iem_nm"),
                        rights_type_code=row.get("rit_tp_cd"),
                        held_quantity=_opt_dec(row.get("hld_qty")),
                        allocated_quantity=_opt_dec(row.get("aloc_qty")),
                        raw=row,
                    )
                )
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH rightsHeld response missing expected field (iem_cd required): {exc}"
            ) from exc
        return rows

    async def get_rights_scheduled(self: NHHTTPClient) -> list[RightsScheduledRow]:
        """`POST /krstock/inquiry/v1/rightsScheduled` (scheduled corporate-action rights).
        The only request field is act_no, so no dedicated Request DTO is needed. Unlike
        the other 9 endpoints, the response's `Output_0` is itself an array (see
        `RightsScheduledRow` in inquiry_dto.py)."""
        raw = await self._request(
            "POST", "/krstock/inquiry/v1/rightsScheduled", body={"act_no": self._act_no}
        )
        output = raw.get("Output_0", [])
        if not isinstance(output, list):
            raise FatalExchangeError(
                f"NH rightsScheduled response Output_0 is not an array (must be an array per "
                f"official openapi.json): {type(output)}"
            )
        rows = []
        try:
            for row in output:
                rows.append(
                    RightsScheduledRow(
                        symbol=row["iem_cd"],
                        name=row.get("iem_nm"),
                        rights_type_code=row.get("rit_tp_cd"),
                        allocated_quantity=_opt_dec(row.get("aloc_qty")),
                        base_date=row.get("bse_dt"),
                        raw=row,
                    )
                )
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH rightsScheduled response missing expected field (iem_cd required): {exc}"
            ) from exc
        return rows

    async def get_sellable_quantity(
        self: NHHTTPClient, request: SellableQuantityRequest
    ) -> SellableQuantityResponse:
        """`POST /krstock/inquiry/v1/sellableQuantity` (sellable quantity inquiry)."""
        body: dict[str, Any] = {
            "act_no": self._act_no,
            "iem_cd": request.symbol,
            "cfd_lon_cd": request.credit_loan_code,
        }
        if request.loan_date is not None:
            body["lon_dt"] = request.loan_date

        raw = await self._request("POST", "/krstock/inquiry/v1/sellableQuantity", body=body)
        try:
            output = _output0(raw, "sellableQuantity")
            quantity = _dec(output["sll_pbl_qty"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH sellableQuantity response missing expected field (sll_pbl_qty required): {exc}"
            ) from exc
        return SellableQuantityResponse(
            sellable_quantity=quantity,
            balance_quantity=_opt_dec(output.get("bnc_qty")),
            purchase_unit_price=_opt_dec(output.get("phs_uit_pr")),
            raw=output,
        )

    async def get_trading_pnl(self: NHHTTPClient, request: TradingPnlRequest) -> TradingPnlResponse:
        """`POST /krstock/inquiry/v1/tradingPnl` (per-symbol realized P&L status)."""
        body = {
            "act_no": self._act_no,
            "iqr_sta_dt": request.start_date,
            "iqr_end_dt": request.end_date,
        }
        raw = await self._request("POST", "/krstock/inquiry/v1/tradingPnl", body=body)
        try:
            summary = _output0(raw, "tradingPnl")
            total_profit = _dec(summary["pls_amt"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH tradingPnl response missing expected field (pls_amt required): {exc}"
            ) from exc
        rows = [
            TradingPnlRow(
                symbol=row["iem_cd"],
                name=row.get("iem_nm"),
                profit_amount=_opt_dec(row.get("pls_amt")),
                profit_rate=_opt_dec(row.get("pft_rt")),
                raw=row,
            )
            for row in raw.get("Output_1", [])
        ]
        return TradingPnlResponse(
            total_profit_amount=total_profit,
            total_fee_sum=_opt_dec(summary.get("fee_sum")),
            rows=rows,
            raw=summary,
        )
