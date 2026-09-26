"""BR-23c -- KiwoomAccountMixin: get_balance/get_positions/get_order.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#BR-23,
docs/exchanges/ADDING_AN_EXCHANGE.md step 6(c).

Endpoints (verified 2026-09-26 against the official Kiwoom Securities REST
API client repository, github.com/Kiwoom-Securities/Kiwoom-REST-API,
`examples/domestic_stock/account/*.py` + the bundled Postman collection's
per-TR field tables):
- POST /api/dostk/acnt, api-id kt00001 (deposit detail) -- body {qry_tp:
  "2"=regular / "3"=estimate}. Response `entr` (deposit cash, KRW) is a
  flat top-level field, not nested under a list.
- POST /api/dostk/acnt, api-id kt00018 (account evaluation balance) --
  body {qry_tp: "1"=aggregate/"2"=per-item, dmst_stex_tp}. Holdings are
  in `acnt_evlt_remn_indv_tot[]`: `stk_cd` (prefixed -- "A"/"J"/"Q" +
  6-digit code), `rmnd_qty` (held qty), `trde_able_qty` (tradeable qty).
- POST /api/dostk/acnt, api-id kt00009 (account order/fill status) --
  body {stk_bond_tp, mrkt_tp, sell_tp, qry_tp, dmst_stex_tp, stk_cd,
  fr_ord_no}. Rows in `acnt_ord_cntr_prst_array[]`: `ord_no`, `ord_qty`,
  `cntr_qty` (filled qty), `io_tp_nm` (order-type display name).
  `fr_ord_no` is a "from this order number onward" filter, not an exact
  match, so get_order() filters the returned rows for `ord_no ==` the
  requested value itself.

Unverified scope (ratchet note): the official field table documents
`trde_tp`/`io_tp_nm` as plain strings with no enumerated code list (unlike
`place_order()`'s `trde_tp` "0"/"3", which the order TR itself defines).
get_order()'s side reconstruction falls back to substring-matching the
Korean display name in `io_tp_nm` (contains the "sell" word -> SELL, else
BUY, see `_SELL_DISPLAY_MARKER`) -- the same "documented uncertainty over
decoding a display name" posture as NHAccountMixin.get_balance's
`rsdl_qty` comment. A live account is required to confirm the exact
string set.

Deviation: mirrors KiwoomTradingMixin's `exchange_order_id` = "stk_cd:ord_no"
composite convention (task-7569/7571) -- get_order() parses the same format
so cancel_order()/modify_order()'s calls into get_order() work unchanged.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Protocol
from uuid import uuid4

from src.core.exceptions import FatalExchangeError
from src.data.models.base import AssetClass
from src.data.models.trading import (
    AccountBalance,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

_DOMESTIC_EXCHANGE_DIVISION = "KRX"  # Phase 1 target, same convention as trading_mixin.py
_ACCOUNT_PATH = "/api/dostk/acnt"
_API_ID_DEPOSIT = "kt00001"
_API_ID_BALANCE = "kt00018"
_API_ID_ORDER_FILL_STATUS = "kt00009"
_STOCK_CODE_PREFIXES = ("A", "J", "Q")  # stock / ELW / ETN (kt00018/kt00009 field table)
_SELL_DISPLAY_MARKER = "매도"  # sell -- see module docstring ratchet note


def _strip_stock_code_prefix(stk_cd: str) -> str:
    if len(stk_cd) > 6 and stk_cd[0] in _STOCK_CODE_PREFIXES:
        return stk_cd[1:]
    return stk_cd


def _split_exchange_order_id(exchange_order_id: str) -> tuple[str, str]:
    if ":" not in exchange_order_id:
        raise FatalExchangeError(
            f"Kiwoom exchange_order_id must be 'stk_cd:ord_no' format: {exchange_order_id}"
        )
    stk_cd, ord_no = exchange_order_id.split(":", 1)
    return stk_cd, ord_no


class _KiwoomAccountClient(Protocol):
    """Minimal HTTP contract needed at mixin-assembly time -- same reasoning
    as trading_mixin.py's `_KiwoomOrderClient` (auth.py/task-7569 assembly
    is still in flight, so this can't yet reference a shared concrete
    client class)."""

    async def _request(
        self,
        method: str,
        path: str,
        api_id: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class KiwoomAccountMixin:
    async def get_balance(
        self: _KiwoomAccountClient, asset: str | None = None
    ) -> list[AccountBalance]:
        balances: list[AccountBalance] = []

        holdings_raw = await self._request(
            "POST",
            _ACCOUNT_PATH,
            _API_ID_BALANCE,
            body={"qry_tp": "1", "dmst_stex_tp": _DOMESTIC_EXCHANGE_DIVISION},
        )
        if "acnt_evlt_remn_indv_tot" not in holdings_raw:
            raise FatalExchangeError(
                f"Kiwoom balance response missing acnt_evlt_remn_indv_tot: {holdings_raw}"
            )
        try:
            for row in holdings_raw["acnt_evlt_remn_indv_tot"]:
                qty = Decimal(str(row["rmnd_qty"]))
                if qty == 0:
                    continue
                stock_code = _strip_stock_code_prefix(str(row["stk_cd"]))
                if asset is not None and stock_code != asset:
                    continue
                balances.append(
                    AccountBalance(
                        exchange="kiwoom",
                        asset=stock_code,
                        total=qty,
                        available=Decimal(str(row["trde_able_qty"])),
                        used_margin=Decimal("0"),
                    )
                )
        except KeyError as exc:
            raise FatalExchangeError(
                f"Kiwoom balance row missing expected field (stk_cd/rmnd_qty/"
                f"trde_able_qty required): {exc}"
            ) from exc

        if asset is None or asset == "KRW":
            deposit_raw = await self._request(
                "POST", _ACCOUNT_PATH, _API_ID_DEPOSIT, body={"qry_tp": "2"}
            )
            if "entr" not in deposit_raw:
                raise FatalExchangeError(
                    f"Kiwoom deposit response missing entr field: {deposit_raw}"
                )
            cash = Decimal(str(deposit_raw["entr"]))
            balances.append(
                AccountBalance(
                    exchange="kiwoom",
                    asset="KRW",
                    total=cash,
                    available=cash,
                    used_margin=Decimal("0"),
                )
            )
        return balances

    async def get_positions(self, symbol: str | None = None) -> list[Position]:
        """Same principle as Bitget/KIS/NH account_mixin.py -- Kiwoom is a
        cash-equity venue with no native strategy-scoped position concept,
        so this always returns empty. Actual holdings are queryable via
        get_balance() (Reconciliation's source of truth)."""
        return []

    async def get_order(self: _KiwoomAccountClient, order_id: str) -> Order:
        """Placeholder AIOS-only fields (strategy_id etc.) mirror
        KISTradingMixin.get_order/BitgetAdapter.get_order -- the caller must
        merge with the DB row."""
        stk_cd, ord_no = _split_exchange_order_id(order_id)
        raw = await self._request(
            "POST",
            _ACCOUNT_PATH,
            _API_ID_ORDER_FILL_STATUS,
            body={
                "stk_bond_tp": "0",
                "mrkt_tp": "0",
                "sell_tp": "0",
                "qry_tp": "0",
                "dmst_stex_tp": _DOMESTIC_EXCHANGE_DIVISION,
                "stk_cd": stk_cd,
                "fr_ord_no": ord_no,
            },
        )
        rows = raw.get("acnt_ord_cntr_prst_array", [])
        match = next((row for row in rows if row.get("ord_no") == ord_no), None)
        if match is None:
            raise FatalExchangeError(f"Kiwoom order not found: order_id={order_id}")

        try:
            ord_qty = Decimal(str(match["ord_qty"]))
            filled_qty = Decimal(str(match["cntr_qty"]))
        except KeyError as exc:
            raise FatalExchangeError(
                f"Kiwoom order-fill-status row missing expected field: {exc}"
            ) from exc

        if filled_qty == 0:
            status = OrderStatus.ACKNOWLEDGED
        elif filled_qty < ord_qty:
            status = OrderStatus.PARTIALLY_FILLED
        else:
            status = OrderStatus.FILLED

        side = (
            OrderSide.SELL
            if _SELL_DISPLAY_MARKER in str(match.get("io_tp_nm", ""))
            else (OrderSide.BUY)
        )

        return Order(
            order_id=uuid4(),
            exchange_order_id=order_id,
            client_order_id="",  # Kiwoom has no client_order_id concept (same as KIS)
            strategy_id="",  # placeholder -- caller must fill from a DB lookup
            strategy_version="",
            symbol=stk_cd,
            exchange="kiwoom",
            side=side,
            order_type=OrderType.LIMIT,  # not recoverable from this TR -- see module docstring
            quantity=ord_qty,
            status=status,
            filled_quantity=filled_qty,
            asset_class=AssetClass.KR_EQUITY,
        )
