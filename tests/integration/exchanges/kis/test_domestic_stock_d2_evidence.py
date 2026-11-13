"""task-24415 DEEPEN-DEPTH BR-14 — domestic_stock D2 floor for test_live_demo_roundtrip.py.

Split into a sibling file because test_live_demo_roundtrip.py has only 29 lines of headroom
below the 500-line ratchet (CLAUDE.md §6 #12). Offline: `_request` is stubbed, so these run in
default CI without credentials (no live_demo marker).

Finding (app code, not fixed here): `place_order` for 005930.KS@kis raises OrderValidationError
UNVERIFIED_SPEC offline because the instrument spec snapshot is verified=False, so the offline
sequence starts at get/cancel with a fixed exchange_order_id instead of placing first.

New perf budget: get+cancel against an instant stub is pure adapter CPU; local best-of-3
measured ~1 ms, budget 50 ms (>30x headroom).
"""

from __future__ import annotations

import pytest

from src.core.exceptions import FatalExchangeError
from src.exchanges.kis.adapter import KISAdapter
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import virtual_delayed_async

_BUDGET_MS = 50.0
_ORDER_ID = "91252:0000002"
_ROW = {"pdno": "005930", "ord_qty": "1", "tot_ccld_qty": "0", "sll_buy_dvsn_cd": "01"}


class _Stub:
    def __init__(self, *, rt_cd: str = "0", fail_on: str | None = None, rows: int = 1) -> None:
        self.calls: list[str] = []
        self._rt_cd, self._fail_on, self._rows = rt_cd, fail_on, rows

    async def request(self, method: str, path: str, tr_id: str, **_: object) -> dict[str, object]:
        self.calls.append(tr_id)
        if tr_id == self._fail_on:
            raise ConnectionError("injected transport failure")
        if tr_id == "TTTC0081R":
            return {"rt_cd": "0", "output1": [_ROW][: self._rows]}
        return {"rt_cd": self._rt_cd}


def _adapter(mp: pytest.MonkeyPatch, stub: _Stub) -> KISAdapter:
    adapter = KISAdapter("k", "s", "12345678", "01", is_paper_trading=True)
    mp.setattr(adapter, "_request", stub.request)
    return adapter


async def _get_cancel(adapter: KISAdapter) -> bool:
    fetched = await adapter.get_order(_ORDER_ID)
    assert fetched.exchange_order_id == _ORDER_ID
    return await adapter.cancel_order(_ORDER_ID)


async def test_domestic_get_cancel_uses_expected_tr_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    stub = _Stub()
    assert await _get_cancel(_adapter(monkeypatch, stub)) is True
    assert stub.calls == ["TTTC0081R", "TTTC0013U"]


async def test_domestic_cancel_rejected_returns_false_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _Stub(rt_cd="1")
    assert await _get_cancel(_adapter(monkeypatch, stub)) is False


async def test_domestic_get_order_not_found_raises_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _Stub(rows=0)
    with pytest.raises(Exception):  # noqa: B017 - exact type owned by the app code
        await _get_cancel(_adapter(monkeypatch, stub))
    assert "TTTC0013U" not in stub.calls


async def test_domestic_cancel_malformed_order_id_raises_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(FatalExchangeError):
        await _adapter(monkeypatch, _Stub()).cancel_order("no-colon-id")


async def test_domestic_failure_injection_stops_before_cancel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub = _Stub(fail_on="TTTC0081R")
    with pytest.raises(ConnectionError):
        await _get_cancel(_adapter(monkeypatch, stub))
    assert "TTTC0013U" not in stub.calls  # cancel never reached


@pytest.mark.perf
async def test_domestic_roundtrip_perf_budget_and_gate_red(
    monkeypatch: pytest.MonkeyPatch, perf_budget: PerfBudget
) -> None:
    adapter = _adapter(monkeypatch, _Stub())
    sample = await perf_budget.best_of_async(lambda: _get_cancel(adapter), n=3, warmup=1)
    assert sample.cpu_ms < _BUDGET_MS, perf_budget.describe(sample, budget_ms=_BUDGET_MS)

    # gate-red: a virtual 10x-budget delay on `_request` must trip the same wall-time gate.
    monkeypatch.setattr(
        adapter, "_request", virtual_delayed_async(_Stub().request, _BUDGET_MS / 100)
    )
    red = await perf_budget.best_of_async(lambda: _get_cancel(adapter), n=3, warmup=0)
    assert red.wall_ms >= _BUDGET_MS
