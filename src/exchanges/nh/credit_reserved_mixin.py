"""BR-20c(task-7010) — NHAdapter credit-order/reserved-order extension methods.

Spec: docs/design/NH_COVERAGE.md `/krstock/order/*` not-started 4 endpoints —
DTOs live in `credit_reserved_dto.py`; see that module's docstring for the
source of the request/response field names.

Split into a separate file from `trading_mixin.py` (place_order/cancel_order/
modify_order, the 3 cash-order endpoints) not for the §7 line-cap policy but
because these 4 endpoints are an NH-only extension outside the
`Order`/`ExchangeAdapter` ABC contract (credit-loan/reserved-order fields are
not shared with other exchange adapters). Like place_order etc., the LIVE
hard guard is kept via `@require_paper_sandbox` (red-team #2026-09-02-32 --
for extension methods that bypass the Executor, this decorator is the only
line of defense).
"""

from __future__ import annotations

from typing import Any

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import OrderSide
from src.exchanges.common.http_client import NHHTTPClient
from src.exchanges.common.live_guard import require_paper_sandbox
from src.exchanges.nh.credit_reserved_dto import (
    CreditOrderRequest,
    CreditOrderResponse,
    ReservedCancelRequest,
    ReservedCancelResponse,
    ReservedOrderRequest,
    ReservedOrderResponse,
)
from src.exchanges.nh.trading_mixin import _MARKET_CODE, _order_division

# Reserved-order type code (bkg_orr_tp_cd) 2/3 requires a date-range field
# (YYYYMMDD range) -- see openapi.json Input_0 description "when
# reserved-order type code is '2' or '3'".
_RESERVED_TYPES_REQUIRING_DATE_RANGE = {"2", "3"}
# Credit loan code (cfd_lon_cd) 03(margin short sale)/04(own-stock short
# sale) requires a loan date.
_CREDIT_LOAN_CODES_REQUIRING_LOAN_DATE = {"03", "04"}


def _sby_dit_cd(side: OrderSide) -> str:
    """Buy/sell division code -- 1.sell 2.buy (unlike the cash-order
    cashBuy/cashSell split-endpoint path, reserved-order/reserved-cancel use
    a single endpoint that takes the side as a field)."""
    return "2" if side == OrderSide.BUY else "1"


class NHCreditReservedMixin:
    @require_paper_sandbox
    async def place_credit_order(
        self: NHHTTPClient, request: CreditOrderRequest
    ) -> CreditOrderResponse:
        """`POST /krstock/order/v1/creditBuy`|`creditSell` -- branches on
        `request.side` (verified against the official openapi.json, see
        module docstring)."""
        if (
            request.credit_loan_code in _CREDIT_LOAN_CODES_REQUIRING_LOAN_DATE
            and request.loan_date is None
        ):
            raise FatalExchangeError(
                f"NH credit order: credit_loan_code={request.credit_loan_code} "
                "requires loan_date (per official openapi.json Input_0 description)"
            )
        path = (
            "/krstock/order/v1/creditBuy"
            if request.side == OrderSide.BUY
            else "/krstock/order/v1/creditSell"
        )
        body: dict[str, Any] = {
            "act_no": self._act_no,
            "iem_cd": request.symbol,
            "orr_qty": str(request.quantity),
            "nmn_pr_tp_cd": _order_division(request.order_type),
            "orr_cnd_dit_cd": request.order_condition_code,
            "cfd_lon_cd": request.credit_loan_code,
            "rmt_mkt_cd": _MARKET_CODE,
            "sor_mkt_sli_yn": "N",
        }
        if request.price is not None:
            body["orr_pr"] = str(request.price)
        if request.order_amount is not None:
            body["orr_amt"] = str(request.order_amount)
        if request.loan_date is not None:
            body["lon_dt"] = request.loan_date
        if request.stop_condition_price is not None:
            body["sop_cnd_pr"] = str(request.stop_condition_price)

        raw = await self._request("POST", path, body=body)
        try:
            output = raw["Output_0"]
            mkt_orr_no = str(output["mkt_orr_no"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH credit order response missing expected field "
                f"(mkt_orr_no required per official openapi.json): {exc}"
            ) from exc
        return CreditOrderResponse(
            mkt_orr_no=mkt_orr_no,
            krx_order_no=_opt_str(output.get("anw_cld_mkt_orr_no1")),
            nxt_order_no=_opt_str(output.get("anw_cld_mkt_orr_no2")),
            order_group_code=_opt_str(output.get("orr_gno_tab_cd")),
        )

    @require_paper_sandbox
    async def place_reserved_order(
        self: NHHTTPClient, request: ReservedOrderRequest
    ) -> ReservedOrderResponse:
        """`POST /krstock/order/v1/reservedOrder` (verified against the
        official openapi.json, see module docstring). The response's
        `bkg_orr_no` (reserved-order number) is a different identifier space
        from cash orders' `mkt_orr_no` -- `cancel_reserved_order()` requires
        this value."""
        if request.reserved_order_type_code in _RESERVED_TYPES_REQUIRING_DATE_RANGE and (
            request.start_date is None or request.end_date is None
        ):
            raise FatalExchangeError(
                f"NH reserved order: reserved_order_type_code="
                f"{request.reserved_order_type_code} requires start_date/end_date "
                "(per official openapi.json Input_0 description)"
            )
        body: dict[str, Any] = {
            "act_no": self._act_no,
            "iem_cd": request.symbol,
            "sby_dit_cd": _sby_dit_cd(request.side),
            "frs_sba_orr_yn": "Y" if request.futures_substitute_order else "N",
            "nmn_pr_tp_cd": _order_division(request.order_type),
            "cfd_lon_cd": request.credit_loan_code,
            "orr_qty": str(request.quantity),
            "bkg_orr_tp_cd": request.reserved_order_type_code,
            "bkg_orr_enf_tp_cd": request.execution_type_code,
            "rmt_mkt_cd": _MARKET_CODE,
        }
        if request.price is not None:
            body["orr_uit_pr"] = str(request.price)
        if request.start_date is not None:
            body["bkg_orr_sta_dt"] = request.start_date
        if request.end_date is not None:
            body["bkg_orr_end_dt"] = request.end_date
        if request.price_range_upper is not None:
            body["orr_pr_rge_hlm_pr"] = str(request.price_range_upper)
        if request.price_range_lower is not None:
            body["orr_pr_rge_llm_pr"] = str(request.price_range_lower)
        if request.close_price_diff_amount is not None:
            body["end_pr_cmp_ftw_amt"] = str(request.close_price_diff_amount)

        raw = await self._request("POST", "/krstock/order/v1/reservedOrder", body=body)
        try:
            output = raw["Output_0"]
            reserved_order_no = str(output["bkg_orr_no"])
        except KeyError as exc:
            raise FatalExchangeError(
                f"NH reserved order response missing expected field "
                f"(bkg_orr_no required per official openapi.json): {exc}"
            ) from exc
        return ReservedOrderResponse(
            reserved_order_no=reserved_order_no,
            symbol=request.symbol,
            side=request.side,
        )

    @require_paper_sandbox
    async def cancel_reserved_order(
        self: NHHTTPClient, request: ReservedCancelRequest
    ) -> ReservedCancelResponse:
        """`POST /krstock/order/v1/reservedCancel` (verified against the
        official openapi.json, see module docstring)."""
        if (
            request.reserved_order_type_code in _RESERVED_TYPES_REQUIRING_DATE_RANGE
            and request.reserved_receipt_date is None
        ):
            raise FatalExchangeError(
                f"NH reserved cancel: reserved_order_type_code="
                f"{request.reserved_order_type_code} requires reserved_receipt_date "
                "(per official openapi.json Input_0 description)"
            )
        body: dict[str, Any] = {
            "act_no": self._act_no,
            "sby_dit_cd": _sby_dit_cd(request.side),
            "iem_cd": request.symbol,
            "bkg_orr_no": _parse_bkg_orr_no(request.reserved_order_no),
            "bkg_orr_tp_cd": request.reserved_order_type_code,
            "rmt_mkt_cd": _MARKET_CODE,
        }
        if request.reserved_receipt_date is not None:
            body["bkg_rtn_dt"] = request.reserved_receipt_date

        await self._request("POST", "/krstock/order/v1/reservedCancel", body=body)
        return ReservedCancelResponse(
            reserved_order_no=request.reserved_order_no,
            symbol=request.symbol,
            side=request.side,
        )


def _opt_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _parse_bkg_orr_no(reserved_order_no: str) -> int:
    """Validates the integer conversion eagerly, for the same reason as
    `org_mkt_orr_no` in cancel_order (see trading_mixin.py's
    `_parse_mkt_orr_no`) -- a corrupted identifier must not silently touch
    the wrong reserved order."""
    try:
        return int(reserved_order_no)
    except ValueError as exc:
        raise FatalExchangeError(f"NH bkg_orr_no is not an integer: {reserved_order_no!r}") from exc
