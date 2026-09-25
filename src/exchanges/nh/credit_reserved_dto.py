"""BR-20c(task-7010) — NH credit-order/reserved-order request/response DTOs.

Spec: docs/design/NH_COVERAGE.md `/krstock/order/*` not-started 4 endpoints —
creditBuy/creditSell/reservedOrder/reservedCancel. Field names and required
flags are copied verbatim from each endpoint's
`request_params[0].schema.properties.Input_0` in
`docs/design/nh_openapi_reference.json` (official openapi.json snapshot,
same source as trading_mixin.py's cashBuy/cashSell/modify/cancel).

Unlike plain cash orders (place_order/modify_order), these 4 endpoints carry
NH-specific fields the domain `Order` model has no room for (credit loan
code, reserved-order type/execution type, the reserved-order-number
`bkg_orr_no`, etc.) — hence a dedicated DTO instead of reusing `Order`. The
`bkg_orr_no` identifier space is distinct from both `mkt_orr_no` (cash
order) and `itg_orr_no` (dailyOrderExecution), so it is not forced into the
existing `exchange_order_id` composite-key convention (`iem_cd:mkt_orr_no`).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from src.data.models.trading import OrderSide, OrderType


@dataclass(frozen=True)
class CreditOrderRequest:
    """Shared request (Input_0) for creditBuy/creditSell — `side` picks
    which of the two endpoints to call (same pattern as place_order's
    cashBuy/cashSell branch)."""

    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    credit_loan_code: str  # cfd_lon_cd -- 01-04/10, required
    price: Decimal | None = None  # orr_pr -- omit for market order(05)
    order_amount: Decimal | None = None  # orr_amt -- amount-based orders only
    loan_date: str | None = None  # lon_dt(YYYYMMDD) -- required when cfd_lon_cd is 03/04
    order_condition_code: str = "00"  # orr_cnd_dit_cd -- 00.none
    stop_condition_price: Decimal | None = None  # sop_cnd_pr -- only when nmn_pr_tp_cd is 16


@dataclass(frozen=True)
class CreditOrderResponse:
    mkt_orr_no: str  # market order number, needed for modify/cancel
    krx_order_no: str | None = None  # anw_cld_mkt_orr_no1
    nxt_order_no: str | None = None  # anw_cld_mkt_orr_no2
    order_group_code: str | None = None  # orr_gno_tab_cd


@dataclass(frozen=True)
class ReservedOrderRequest:
    """reservedOrder request (Input_0)."""

    symbol: str
    side: OrderSide
    quantity: Decimal
    order_type: OrderType
    reserved_order_type_code: str  # bkg_orr_tp_cd -- 1.general 2.remaining-qty 3.fixed-qty
    execution_type_code: str  # bkg_orr_enf_tp_cd -- 1.general 2.relative-to-reference-price
    price: Decimal | None = None  # orr_uit_pr
    credit_loan_code: str = "00"  # cfd_lon_cd -- 00.regular trade(default)
    futures_substitute_order: bool = False  # frs_sba_orr_yn
    start_date: str | None = None  # bkg_orr_sta_dt(YYYYMMDD) -- required for type 2/3
    end_date: str | None = None  # bkg_orr_end_dt(YYYYMMDD) -- required for type 2/3
    price_range_upper: Decimal | None = None  # orr_pr_rge_hlm_pr -- execution type 2 only
    price_range_lower: Decimal | None = None  # orr_pr_rge_llm_pr -- execution type 2 only
    close_price_diff_amount: Decimal | None = None  # end_pr_cmp_ftw_amt -- execution type 2 only


@dataclass(frozen=True)
class ReservedOrderResponse:
    reserved_order_no: str  # bkg_orr_no -- identifier needed to cancel
    symbol: str  # iem_cd
    side: OrderSide  # sby_dit_cd


@dataclass(frozen=True)
class ReservedCancelRequest:
    """reservedCancel request (Input_0)."""

    symbol: str
    side: OrderSide
    reserved_order_no: str  # bkg_orr_no
    reserved_order_type_code: str  # bkg_orr_tp_cd
    reserved_receipt_date: str | None = None  # bkg_rtn_dt(YYYYMMDD) -- required for type 2/3


@dataclass(frozen=True)
class ReservedCancelResponse:
    reserved_order_no: str  # bkg_orr_no
    symbol: str  # iem_cd
    side: OrderSide  # sby_dit_cd
