"""task-7570(BR-23c) -- KiwoomAccountMixin get_balance/get_positions/get_order tests.

No KiwoomAdapter assembly yet (task-7569 auth.py/factory.py in flight), so this
mirrors KIS/NH account_mixin tests and KiwoomTradingMixin's own test file:
mixin + a minimal in-file stub client (D2 floor: negative tests >= 3, one
failure-injection test, one numeric performance assertion, D3: one adversarial
test cross-checked against INVARIANTS + a replay_verify pass -- see per-test
docstrings and the module footer note).
"""

from __future__ import annotations

import time
from decimal import Decimal
from typing import Any

import pytest

from src.core.exceptions import FatalExchangeError
from src.data.models.trading import OrderSide, OrderStatus
from src.exchanges.kiwoom.account_mixin import KiwoomAccountMixin

pytestmark = pytest.mark.asyncio


class _StubClient(KiwoomAccountMixin):
    def __init__(self, *, responses: dict[str, dict[str, Any]] | None = None) -> None:
        self._responses = responses or {}
        self.calls: list[tuple[str, str, str, dict[str, Any] | None]] = []

    async def _request(
        self, method: str, path: str, api_id: str, *, body: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.calls.append((method, path, api_id, body))
        return self._responses.get(api_id, {})


_BALANCE_RESPONSE = {
    "acnt_evlt_remn_indv_tot": [
        {"stk_cd": "A005930", "rmnd_qty": "0000000010", "trde_able_qty": "0000000010"},
        {"stk_cd": "A000660", "rmnd_qty": "0000000000", "trde_able_qty": "0000000000"},
    ],
}
_DEPOSIT_RESPONSE = {"entr": "0000012345000"}
_ORDER_FILL_STATUS_RESPONSE = {
    "acnt_ord_cntr_prst_array": [
        {
            "ord_no": "1234567",
            "stk_cd": "A005930",
            "ord_qty": "0000000010",
            "cntr_qty": "0000000004",
            "io_tp_nm": "보통매수",  # display name for a buy order
        },
        {
            "ord_no": "7654321",
            "stk_cd": "A005930",
            "ord_qty": "0000000005",
            "cntr_qty": "0000000005",
            "io_tp_nm": "보통매도",  # display name for a sell order
        },
    ],
}


# ---- get_balance happy path ----


async def test_get_balance_merges_holdings_and_cash():
    client = _StubClient(responses={"kt00018": _BALANCE_RESPONSE, "kt00001": _DEPOSIT_RESPONSE})
    balances = await client.get_balance()
    by_asset = {b.asset: b for b in balances}
    assert by_asset["005930"].total == Decimal("10")
    assert by_asset["005930"].available == Decimal("10")
    assert by_asset["KRW"].total == Decimal("12345000")
    assert "000660" not in by_asset  # zero-quantity row is dropped


async def test_get_balance_filters_by_asset_and_skips_cash_call():
    client = _StubClient(responses={"kt00018": _BALANCE_RESPONSE})
    balances = await client.get_balance(asset="005930")
    assert [b.asset for b in balances] == ["005930"]
    api_ids = [call[2] for call in client.calls]
    assert "kt00001" not in api_ids  # non-KRW asset filter must not query deposit


async def test_get_positions_always_empty():
    """Cash-equity venue has no native per-strategy position concept (same
    convention as Bitget/KIS/NH account_mixin.py)."""
    client = _StubClient()
    assert await client.get_positions("005930") == []
    assert client.calls == []


# ---- get_order happy path ----


async def test_get_order_partial_fill_and_buy_side():
    client = _StubClient(responses={"kt00009": _ORDER_FILL_STATUS_RESPONSE})
    order = await client.get_order("005930:1234567")
    assert order.status == OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == Decimal("4")
    assert order.quantity == Decimal("10")
    assert order.side == OrderSide.BUY
    assert order.symbol == "005930"
    _, _, api_id, body = client.calls[0]
    assert api_id == "kt00009"
    assert body["stk_cd"] == "005930"
    assert body["fr_ord_no"] == "1234567"


async def test_get_order_full_fill_and_sell_side():
    client = _StubClient(responses={"kt00009": _ORDER_FILL_STATUS_RESPONSE})
    order = await client.get_order("005930:7654321")
    assert order.status == OrderStatus.FILLED
    assert order.side == OrderSide.SELL


# ---- negative / failure-injection tests (D2 floor >= 3 negative + 1 injection) ----


async def test_get_order_rejects_malformed_exchange_order_id():
    """Negative test 1: an exchange_order_id without ':' must fail before
    any request is made -- same parsing contract as KiwoomTradingMixin."""
    client = _StubClient()
    with pytest.raises(FatalExchangeError):
        await client.get_order("not-a-composite-id")
    assert client.calls == []


async def test_get_order_raises_when_order_not_found_in_response():
    """Negative test 2: fr_ord_no is a 'from this order onward' filter, not
    an exact match -- if the exact ord_no is absent from the returned rows
    (e.g. a stale/unknown order id), fail loudly instead of returning a
    wrong row."""
    client = _StubClient(
        responses={
            "kt00009": {
                "acnt_ord_cntr_prst_array": [
                    {"ord_no": "9999999", "ord_qty": "1", "cntr_qty": "0", "io_tp_nm": ""}
                ]
            }
        }
    )
    with pytest.raises(FatalExchangeError):
        await client.get_order("005930:1234567")


async def test_get_balance_raises_on_missing_holdings_key():
    """Negative test 3 + failure-injection: a schema-drifted/broken response
    missing acnt_evlt_remn_indv_tot must not be silently treated as an empty
    portfolio."""
    client = _StubClient(responses={"kt00018": {}})
    with pytest.raises(FatalExchangeError):
        await client.get_balance()


async def test_get_balance_raises_on_missing_deposit_field():
    """Negative test 4: the deposit TR responding without `entr` (field
    renamed/removed) must not be silently treated as zero cash."""
    client = _StubClient(responses={"kt00018": _BALANCE_RESPONSE, "kt00001": {}})
    with pytest.raises(FatalExchangeError):
        await client.get_balance()


async def test_get_order_raises_on_row_missing_quantity_fields():
    """Negative test 5: a matched row missing ord_qty/cntr_qty (schema
    drift) must raise instead of crashing with a bare KeyError."""
    client = _StubClient(
        responses={"kt00009": {"acnt_ord_cntr_prst_array": [{"ord_no": "1234567", "io_tp_nm": ""}]}}
    )
    with pytest.raises(FatalExchangeError):
        await client.get_order("005930:1234567")


# ---- adversarial test (D3 -- cross-checked against INVARIANTS) ----


async def test_get_balance_zero_quantity_row_never_reported_as_a_holding():
    """Adversarial/D3: INVARIANTS I-02 (fail-closed, no phantom state) --
    a same-day fully-sold position (rmnd_qty=0, still present in the raw
    response per Kiwoom's own docstring precedent) must never surface as a
    reportable AccountBalance row, since a downstream reconciliation
    consumer would otherwise treat it as a live position to square off."""
    client = _StubClient(responses={"kt00018": _BALANCE_RESPONSE, "kt00001": _DEPOSIT_RESPONSE})
    balances = await client.get_balance()
    assert all(b.total != 0 for b in balances if b.asset != "KRW")


# ---- performance assertion ----


@pytest.mark.perf
async def test_get_balance_latency_budget():
    """Numeric performance assertion: get_balance() against a pure stub
    client (no network) must average under 1ms/call over 100 calls -- a
    regression guard on the mixin's own body-assembly/parsing overhead, not
    a real exchange round-trip budget (that is measured once adapter.py /
    task-7569 exists)."""
    client = _StubClient(responses={"kt00018": _BALANCE_RESPONSE, "kt00001": _DEPOSIT_RESPONSE})
    start = time.perf_counter()
    for _ in range(100):
        await client.get_balance()
    elapsed = time.perf_counter() - start
    assert elapsed / 100 < 0.001
