"""6.9-supplement/L4-21 — KISAdapter order **query** methods (read-only, no funds movement).

Spec: 02d_kis_api_supplement_v1.md#§2(FD-4.1/4.4/20),
      docs/specs/L4_execution_oms_and_exchange_v1.0.md §6 F5-b, §9 L4-21

`trading_mixin.py` (the order-mutating group: place/cancel/modify) hit the
300-line cap under L4-21, so the query-only methods were split out into this
file (same call as bitget/trading_query_mixin.py, per the task-1519
precedent). The existing 4 methods (get_buyable_amount, etc.) are a pure
move; `get_open_orders`/`get_order_history`/`find_order_by_match` are the
new logic in this leaf (§6 F5-b UNKNOWN reverse lookup).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Protocol

from src.data.models.trading import Order
from src.exchanges.common.http_client import KISHTTPClient
from src.exchanges.kis.order_reverse_lookup import (
    OrderMatchQuery,
    find_matching_order,
    row_to_order,
)

_EXCHANGE_ID = "KRX"  # Phase 1 target (doc 06 §6.1)
_KST_OFFSET_HOURS = 9


def _kst_today() -> str:
    """KIS interprets "today" as a KST calendar day — between UTC 15:00 and
    24:00 (already the next day in KST), using the raw UTC date would query
    yesterday's date and miss today's orders (this must also line up with the
    order_date assumption in order_reverse_lookup.row_to_order, which treats
    this value as a KST calendar day)."""
    return (datetime.now(timezone.utc) + timedelta(hours=_KST_OFFSET_HOURS)).strftime("%Y%m%d")


class _ReverseLookupClient(KISHTTPClient, Protocol):
    """`find_order_by_match` calls `get_open_orders`/`get_order_history` on the
    same class — included explicitly in the contract for the same reason as
    `_OrderMutatingClient` in trading_mixin.py."""

    async def get_open_orders(self, symbol: str | None = None) -> list[Order]: ...

    async def get_order_history(
        self, symbol: str | None = None, *, date: str | None = None
    ) -> list[Order]: ...


class KISTradingQueryMixin:
    async def get_buyable_amount(
        self: KISHTTPClient, symbol: str, price: Decimal
    ) -> dict[str, Any]:
        """02d spec §2 (P0) — FD-4.1 pre-order validation (buyable amount/
        quantity). Best-effort estimate based on the official example
        (inquire_psbl_order); needs live verification.
        Returns a raw dict — several amount fields (cash/margin/collateral,
        etc.) come back together (per the §2 model-reuse principle), so this
        is not modeled yet."""
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-order",
            "TTTC8908R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "PDNO": symbol,
                "ORD_UNPR": str(price),
                "ORD_DVSN": "00",
                "CMA_EVLU_AMT_ICLD_YN": "N",
                "OVRS_ICLD_YN": "N",
            },
        )
        return dict(raw.get("output", {}))

    async def get_sellable_quantity(self: KISHTTPClient, symbol: str) -> Decimal:
        """02d spec §2 (P0). Best-effort estimate based on the official
        example (inquire_psbl_sell); needs live verification."""
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-sell",
            "TTTC8408R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "PDNO": symbol,
            },
        )
        output = raw.get("output", {})
        return Decimal(output.get("ord_psbl_qty", "0"))

    async def get_cancelable_orders(self: KISHTTPClient) -> list[dict[str, Any]]:
        """02d spec §2 (P0) — FD-4.4 pre-modification validation. Best-effort
        estimate based on the official example (inquire_psbl_rvsecncl); needs
        live verification. Returns a raw dict list (mostly KIS-specific
        fields such as the modifiable/cancelable quantity, so the shape
        differs from the Order model)."""
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
            "TTTC0084R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "INQR_DVSN_1": "0",
                "INQR_DVSN_2": "0",
            },
        )
        return list(raw.get("output", []))

    async def get_realized_pnl(
        self: KISHTTPClient, *, start_date: str | None = None, end_date: str | None = None
    ) -> list[dict[str, Any]]:
        """02d spec §2 (P0) — supplementary support for FD-20 (operations
        report). Best-effort estimate based on the official example
        (inquire_balance_rlz_pl); needs live verification.
        `start_date`/`end_date` are "YYYYMMDD"; default to today if
        omitted."""
        today = datetime.now(timezone.utc).strftime("%Y%m%d")
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-balance-rlz-pl",
            "TTTC8494R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "INQR_STRT_DT": start_date or today,
                "INQR_END_DT": end_date or today,
                "PDNO": "",
                "CBLC_DVSN": "00",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
            },
        )
        return list(raw.get("output1", []))

    async def get_open_orders(self: KISHTTPClient, symbol: str | None = None) -> list[Order]:
        """§6 F5-b reverse-lookup step 1. Could not find a dedicated "open
        order list" TR in the official examples, so this reuses
        inquire-psbl-rvsecncl (the modify/cancel-eligible order inquiry, the
        same endpoint as get_cancelable_orders) (**unverified**)."""
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-psbl-rvsecncl",
            "TTTC0084R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "INQR_DVSN_1": "0",
                "INQR_DVSN_2": "0",
            },
        )
        orders = [row_to_order(row, order_date=_kst_today()) for row in raw.get("output", [])]
        return [o for o in orders if symbol is None or o.symbol == symbol]

    async def get_order_history(
        self: KISHTTPClient, symbol: str | None = None, *, date: str | None = None
    ) -> list[Order]:
        """§6 F5-b reverse-lookup step 2. Calls the same TR that
        `get_order()` uses for single-order lookup (inquire-daily-ccld),
        without an ODNO filter, and returns all rows for the given date
        (defaults to today per §6 F5-b "today")."""
        order_date = date or _kst_today()
        raw = await self._request(
            "GET",
            "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            "TTTC0081R",
            params={
                "CANO": self._cano,
                "ACNT_PRDT_CD": self._acnt_prdt_cd,
                "INQR_STRT_DT": order_date,
                "INQR_END_DT": order_date,
                "SLL_BUY_DVSN_CD": "00",
                "PDNO": symbol or "",
                "CCLD_DVSN": "00",
                "INQR_DVSN": "00",
                "INQR_DVSN_3": "00",
                "ORD_GNO_BRNO": "",
                "ODNO": "",
                "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "",
                "CTX_AREA_NK100": "",
                "EXCG_ID_DVSN_CD": _EXCHANGE_ID,
            },
        )
        orders = [
            row_to_order(row, order_date=order_date) for row in raw.get("output1", [])
        ]
        return [o for o in orders if symbol is None or o.symbol == symbol]

    async def find_order_by_match(
        self: _ReverseLookupClient, query: OrderMatchQuery
    ) -> Order | None:
        """§6 F5-b — UNKNOWN reverse lookup for KIS, which has no
        client_order_id. Unlike `find_order_by_client_id` (F5-a,
        Bitget-only), this matches candidates by symbol/side/quantity/price/
        submission time. If two or more candidates match,
        `find_matching_order` raises `MultipleCandidateOrdersError` to
        ESCALATE immediately (no automatic judgment) — no fallback selection
        is made here."""
        open_orders = await self.get_open_orders(query.symbol)
        history = await self.get_order_history(query.symbol)
        candidates: dict[str, Order] = {}
        for order in (*open_orders, *history):
            candidates.setdefault(order.exchange_order_id or "", order)
        return find_matching_order(query, list(candidates.values()))
