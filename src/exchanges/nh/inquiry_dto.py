"""BR-20b(task-7009) -- NH krstock/inquiry extension: request/response DTOs
for the 10 not-started endpoints.

Spec: docs/design/NH_COVERAGE.md `/krstock/inquiry/*` not-started 10 --
assetStatus/buyableQuantity/dailyPnl/integratedMargin/realizedPnl/
reservedInquiry/rightsHeld/rightsScheduled/sellableQuantity/tradingPnl.
Field names and required flags are copied verbatim from each endpoint's
`request_params[0].schema.properties.Input_0` / `response_schema` in
`docs/design/nh_openapi_reference.json` (official openapi.json snapshot,
same source as get_balance/credit_reserved_dto.py).

Responses often carry dozens of fields -- not all of them are mapped. Each
DTO keeps only the fields a caller is realistically expected to use (money
as Decimal, everything else as str) and preserves the full original row in
`raw: dict` so no data is silently discarded (a trade-off that keeps the
mapped surface small -- mapping every field as a dataclass attribute across
10 endpoints x dozens of fields would immediately blow past the file's
loc-cap)."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


def _dec(value: Any) -> Decimal:
    return Decimal(str(value))


def _opt_dec(value: Any) -> Decimal | None:
    return None if value is None else Decimal(str(value))


# ---------------------------------------------------------------------------
# assetStatus -- investment account asset status inquiry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetStatusRequest:
    net_or_gross_code: str = "1"  # aet_bse -- 1.net 2.gross
    valuation_apply_code: str = "1"  # eal_aly_cd -- 1.book value 2.market value
    quote_division_code: str = "UNT"  # qut_dit_cd -- UNT/KRX/NXT
    applied_quote_code: str = "1"  # aly_qut_cd -- 1.regular session 2.all sessions


@dataclass(frozen=True)
class AssetStatusHolding:
    symbol: str  # iem_cd
    name: str | None  # iem_nm
    quantity: Decimal  # itg_bnc_qty
    eval_amount: Decimal | None  # eal_amt
    eval_profit_amount: Decimal | None  # eal_pls_amt
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AssetStatusResponse:
    deposit: Decimal  # dca -- cash deposit
    total_asset_amount: Decimal  # tot_aet_amt
    total_eval_amount: Decimal | None  # tot_eal_amt
    total_eval_profit_amount: Decimal | None  # tot_eal_pls_amt
    holdings: list[AssetStatusHolding]
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# buyableQuantity -- buyable quantity inquiry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BuyableQuantityRequest:
    symbol: str  # iem_cd
    division_code: str  # ost_dit_cd -- 1.cash 2.credit 3.purchase-fund loan
    order_price_type_code: str  # nmn_pr_tp_cd
    order_price: Decimal | None = None  # orr_pr
    credit_loan_code: str | None = None  # cfd_lon_cd -- required when division_code is "2"
    loan_date: str | None = None  # lon_dt(YYYYMMDD)


@dataclass(frozen=True)
class BuyableQuantityResponse:
    cash_buyable_quantity: Decimal  # csh_orr_pbl_qty
    cash_buyable_amount: Decimal | None  # csh_orr_pbl_amt
    max_buyable_quantity: Decimal | None  # max_pbl_qty
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# dailyPnl -- realized P&L daily aggregate inquiry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DailyPnlRequest:
    start_date: str  # iqr_sta_dt(YYYYMMDD)
    end_date: str  # iqr_end_dt(YYYYMMDD)
    symbol: str | None = None  # iem_cd


@dataclass(frozen=True)
class DailyPnlRow:
    trade_date: str  # sby_dt
    buy_amount: Decimal | None  # byn_amt
    sell_amount: Decimal | None  # sll_amt
    profit_amount: Decimal | None  # pls_amt
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DailyPnlResponse:
    total_profit_amount: Decimal  # pls_amt_sum
    total_buy_cost: Decimal | None  # byn_cst_sum
    total_sell_cost: Decimal | None  # sll_cst_sum
    rows: list[DailyPnlRow]
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# integratedMargin -- integrated margin status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntegratedMarginResponse:
    limit_amount: Decimal  # lmt_amt
    limit_used_amount: Decimal | None  # lmt_use_amt
    remaining_limit_amount: Decimal | None  # rmn_lmt_amt
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# realizedPnl -- stock balance inquiry with realized P&L
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RealizedPnlRequest:
    inquiry_division_code: str  # iqr_dit_cd1 -- 0.all 1.holdings only 2.today's trades
    fee_division_code: str  # fee_dit_cd -- 1.online 2.branch
    quote_division_code: str = "UNT"  # qut_dit_cd
    applied_quote_code: str = "1"  # aly_qut_cd


@dataclass(frozen=True)
class RealizedPnlHolding:
    symbol: str  # iem_cd
    name: str | None  # iem_nm
    quantity: Decimal  # itg_bnc_qty
    realized_profit_amount: Decimal | None  # rzt_pls_amt
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RealizedPnlResponse:
    eval_amount_sum: Decimal  # eal_amt_sum
    eval_profit_amount: Decimal | None  # eal_pls_amt
    holdings: list[RealizedPnlHolding]
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# reservedInquiry -- reserved order inquiry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReservedInquiryRequest:
    side_code: str  # sby_dit_cd -- 0.all 1.sell 2.buy
    reserved_order_type_code: str  # bkg_orr_tp_cd
    symbol: str | None = None  # iem_cd
    credit_loan_code: str | None = None  # cfd_lon_cd
    cancel_division_code: str | None = None  # bkg_orr_can_dit_cd
    reserved_receipt_date: str | None = None  # bkg_orr_rtn_dt(YYYYMMDD)


@dataclass(frozen=True)
class ReservedInquiryRow:
    reserved_order_no: str  # bkg_rtn_orr_no
    symbol: str  # iem_cd
    side_code: str | None  # sby_dit_cd
    quantity: Decimal | None  # orr_qty
    price: Decimal | None  # orr_pr
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# rightsHeld -- held corporate-action rights over a period
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RightsHeldRequest:
    rights_type_code: str | None = None  # rit_tp_cd
    start_date: str | None = None  # sta_dt(YYYYMMDD)


@dataclass(frozen=True)
class RightsHeldRow:
    symbol: str  # iem_cd
    name: str | None  # iem_nm
    rights_type_code: str | None  # rit_tp_cd
    held_quantity: Decimal | None  # hld_qty
    allocated_quantity: Decimal | None  # aloc_qty
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# rightsScheduled -- scheduled corporate-action rights over a period
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RightsScheduledRow:
    """`response_schema.Output_0` is itself an array here, unlike the other
    9 endpoints (no `Output_1`, confirmed against the official
    openapi.json) -- there is no separate summary/rows split."""

    symbol: str  # iem_cd
    name: str | None  # iem_nm
    rights_type_code: str | None  # rit_tp_cd
    allocated_quantity: Decimal | None  # aloc_qty
    base_date: str | None  # bse_dt
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# sellableQuantity -- sellable quantity inquiry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SellableQuantityRequest:
    symbol: str  # iem_cd
    credit_loan_code: str  # cfd_lon_cd
    loan_date: str | None = None  # lon_dt(YYYYMMDD) -- when cfd_lon_cd is "01"


@dataclass(frozen=True)
class SellableQuantityResponse:
    sellable_quantity: Decimal  # sll_pbl_qty
    balance_quantity: Decimal | None  # bnc_qty
    purchase_unit_price: Decimal | None  # phs_uit_pr
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# tradingPnl -- per-symbol realized P&L status
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TradingPnlRequest:
    start_date: str  # iqr_sta_dt(YYYYMMDD)
    end_date: str  # iqr_end_dt(YYYYMMDD)


@dataclass(frozen=True)
class TradingPnlRow:
    symbol: str  # iem_cd
    name: str | None  # iem_nm
    profit_amount: Decimal | None  # pls_amt
    profit_rate: Decimal | None  # pft_rt
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TradingPnlResponse:
    total_profit_amount: Decimal  # pls_amt (Output_0)
    total_fee_sum: Decimal | None  # fee_sum
    rows: list[TradingPnlRow]
    raw: dict[str, Any] = field(default_factory=dict)
