"""scripts/canary_bitget.py — far_limit/market_order roundtrip + 3-way
reconcile 테스트 (RATCHET-split task-10934).

원본 `test_canary_bitget.py`가 532줄로 ADR-2026-09-10-C LOC 규율(500줄
경고)을 초과해 책임별로 분할했다 — 게이트/제출/하드가드/kill switch/리포트는
`test_canary_bitget.py`에 남고, 실제 거래소 왕복(far_limit_roundtrip /
market_order_roundtrip)과 3-way reconcile만 이 파일로 옮겼다. 공개 API
변경 없음(둘 다 동일한 `scripts/canary_bitget.py`를 로드).
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from types import ModuleType

from src.data.models.trading import Order, OrderStatus

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


canary = _load_module("canary_bitget_roundtrip", SCRIPTS_DIR / "canary_bitget.py")


# ---------------------------------------------------------------------------
# far_limit_roundtrip / reconcile_three_way / market_order_roundtrip
# ---------------------------------------------------------------------------


@dataclass
class _RoundtripAdapter:
    account_mode: str = "classic"
    _orders: dict[str, Order] = field(default_factory=dict)
    _history: list[Order] = field(default_factory=list)
    _seq: int = 0

    async def get_balance(self, asset: str | None = None) -> list[object]:
        return [object()]

    async def get_ticker(self, symbol: str) -> object:
        return type("Ticker", (), {"price": Decimal("50000")})()

    async def place_order(self, order: Order) -> Order:
        self._seq += 1
        order_id = f"ex-{self._seq}"
        status = OrderStatus.FILLED if order.order_type == "MARKET" else OrderStatus.ACKNOWLEDGED
        filled = order.quantity if status is OrderStatus.FILLED else Decimal("0")
        placed = order.model_copy(
            update={"exchange_order_id": order_id, "status": status, "filled_quantity": filled}
        )
        self._orders[order_id] = placed
        if status is OrderStatus.FILLED:
            self._history.append(placed)
        return placed

    async def get_order(self, order_id: str) -> Order:
        return self._orders[order_id]

    async def get_order_history(
        self, symbol: str | None = None, *, limit: int = 100
    ) -> list[Order]:
        return list(self._history)

    async def cancel_order(self, order_id: str) -> bool:
        cancelled = self._orders[order_id].model_copy(update={"status": OrderStatus.CANCELLED})
        self._orders[order_id] = cancelled
        return True


async def test_far_limit_roundtrip_places_gets_cancels_and_confirms() -> None:
    adapter = _RoundtripAdapter()
    result = await canary.far_limit_roundtrip(
        adapter, symbol="BTC/USDT", market_price=Decimal("50000"), quantity=Decimal("0.0001")
    )
    assert result.cancelled is True
    assert result.final_status == OrderStatus.CANCELLED.value


async def test_market_order_roundtrip_reconciles_three_way_match() -> None:
    adapter = _RoundtripAdapter()
    result = await canary.market_order_roundtrip(
        adapter, symbol="BTC/USDT", quantity=Decimal("0.0001")
    )
    assert result.buy_reconciliation.matched is True
    assert result.sell_reconciliation.matched is True


def test_reconcile_three_way_reports_pending_when_history_not_yet_propagated() -> None:
    live = Order(
        client_order_id="c1",
        strategy_id="s",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("0.0001"),
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.0001"),
        exchange_order_id="ex-1",
        asset_class="CRYPTO",
    )
    record = canary.reconcile_three_way(
        live_order=live, history_order=None, local_position_qty=Decimal("0.0001")
    )
    assert record.pending is True
    assert record.matched is False


def test_reconcile_three_way_reports_mismatch_when_quantities_diverge() -> None:
    """negative #9 (여분) — 거래소/이력/로컬 셋 중 하나라도 어긋나면
    MATCH로 위장하지 않는다."""
    live = Order(
        client_order_id="c1",
        strategy_id="s",
        strategy_version="v1",
        symbol="BTC/USDT",
        exchange="bitget",
        side="BUY",
        order_type="MARKET",
        quantity=Decimal("0.0001"),
        status=OrderStatus.FILLED,
        filled_quantity=Decimal("0.0001"),
        exchange_order_id="ex-1",
        asset_class="CRYPTO",
    )
    history = live.model_copy(update={"filled_quantity": Decimal("0.00005")})
    record = canary.reconcile_three_way(
        live_order=live, history_order=history, local_position_qty=Decimal("0.0001")
    )
    assert record.pending is False
    assert record.matched is False
    assert "MISMATCH" in record.notes
