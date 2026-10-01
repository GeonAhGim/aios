"""L4-30b — preflight / 지정가 왕복 / 시장가 왕복 + 3-way 대사.

Spec: task-2750, docs/specs/L4_execution_oms_and_exchange_v1.0.md §9 L4-30
계보. `scripts/canary_bitget.py`에서 분리된 책임 단위 — 실제 거래소 왕복
동작(계정 모드 감지, place/get/cancel, 5 USDT 매수/매도 + 대사)만 담당한다
(RATCHET-split task-10863, ADR-2026-09-10-C LOC 규율).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from uuid import uuid4

from scripts.canary_bitget_lib.gate import CanaryAdapter
from src.data.models.base import AssetClass, Currency, Money
from src.data.models.trading import Order, OrderSide, OrderType


class AdapterContractViolationError(Exception):
    """`ExchangeAdapter.place_order()`가 계약(§2 02번 문서)을 어기고
    `exchange_order_id` 없이 반환한 경우 — 이후 get/cancel이 불가능하므로
    조용히 진행하지 않고 즉시 중단한다."""


def _require_exchange_order_id(order: Order) -> str:
    if not order.exchange_order_id:
        raise AdapterContractViolationError("place_order가 exchange_order_id 없이 반환됨")
    return order.exchange_order_id


# ---------------------------------------------------------------------------
# 사전 점검 (계정 모드 감지 + 거래 권한 확인)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PreflightResult:
    account_mode: str
    balances_observed: int


async def run_preflight(adapter: CanaryAdapter) -> PreflightResult:
    """`get_balance()` 성공 자체가 서명 인증 + 계좌 접근 권한 확인이다
    (Bitget 스팟은 잔고 조회도 서명을 요구하므로, 실패 없이 응답이 오면
    이 키가 계정에 유효하게 도달한다는 뜻). 계정 모드는
    `account_mode.account_aware_request()`가 40085 관측 시 자동 전환하므로
    이 함수는 그 결과를 읽기만 한다(task-2514 L4-31)."""
    balances = await adapter.get_balance()
    mode = adapter.account_mode
    mode_value = mode.value if hasattr(mode, "value") else str(mode)
    return PreflightResult(account_mode=mode_value, balances_observed=len(balances))


# ---------------------------------------------------------------------------
# 시장가 30% 이격 지정가 place/get/cancel 왕복
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LimitRoundtripResult:
    exchange_order_id: str
    placed_status: str
    fetched_status: str
    cancelled: bool
    final_status: str


def _client_order_id(tag: str) -> str:
    return f"l4-30b-canary-{tag}-{uuid4().hex[:8]}"


async def far_limit_roundtrip(
    adapter: CanaryAdapter,
    *,
    symbol: str,
    market_price: Decimal,
    quantity: Decimal,
    side: OrderSide = OrderSide.BUY,
) -> LimitRoundtripResult:
    """DoD 2 — 시장가에서 30% 떨어진(매수는 -30%, 매도는 +30%) 지정가라
    체결 위험 없이 place -> get(NEW) -> cancel -> get(CANCELED) 왕복이
    가능하다(L4-30 데모 테스트의 `_SAFE_PRICE` 선례와 동일 원리)."""
    offset = Decimal("0.7") if side is OrderSide.BUY else Decimal("1.3")
    far_price = market_price * offset
    order = Order(
        client_order_id=_client_order_id("far-limit"),
        strategy_id="l4-30b-canary",
        strategy_version="v1",
        symbol=symbol,
        exchange="bitget",
        side=side,
        order_type=OrderType.LIMIT,
        quantity=quantity,
        price=Money(amount=far_price, currency=Currency.USDT),
        asset_class=AssetClass.CRYPTO,
    )
    placed = await adapter.place_order(order)
    exchange_order_id = _require_exchange_order_id(placed)

    fetched = await adapter.get_order(exchange_order_id)
    cancelled = await adapter.cancel_order(exchange_order_id)
    final = await adapter.get_order(exchange_order_id)

    return LimitRoundtripResult(
        exchange_order_id=exchange_order_id,
        placed_status=placed.status.value,
        fetched_status=fetched.status.value,
        cancelled=cancelled,
        final_status=final.status.value,
    )


# ---------------------------------------------------------------------------
# 5 USDT 시장가 매수/매도 왕복 + 3-way 대사
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReconciliationRecord:
    """거래소(get_order) · 거래소 이력(get_order_history, 별도 엔드포인트) ·
    로컬 포지션(이 스크립트 자신의 누적치), 3원 비교. 내부 원장/포지션
    테이블이 아직 없어(모듈 docstring 참조) history_order가 아직
    전파되지 않은 경우는 실패가 아니라 PENDING으로 구분한다(8.3 원칙 —
    모르는 상태를 실패로 단정하지 않는다)."""

    matched: bool
    pending: bool
    exchange_filled_qty: Decimal
    history_filled_qty: Decimal | None
    local_position_qty: Decimal
    notes: str


def reconcile_three_way(
    *,
    live_order: Order,
    history_order: Order | None,
    local_position_qty: Decimal,
    tolerance: Decimal = Decimal("0.000001"),
) -> ReconciliationRecord:
    exchange_qty = live_order.filled_quantity
    if history_order is None:
        return ReconciliationRecord(
            matched=False,
            pending=True,
            exchange_filled_qty=exchange_qty,
            history_filled_qty=None,
            local_position_qty=local_position_qty,
            notes=f"order_history에 아직 미전파(exchange_order_id={live_order.exchange_order_id})",
        )
    history_qty = history_order.filled_quantity
    matched = (
        abs(exchange_qty - history_qty) <= tolerance
        and abs(exchange_qty - local_position_qty) <= tolerance
    )
    notes = (
        "OK"
        if matched
        else f"MISMATCH exchange={exchange_qty} history={history_qty} local={local_position_qty}"
    )
    return ReconciliationRecord(
        matched=matched,
        pending=False,
        exchange_filled_qty=exchange_qty,
        history_filled_qty=history_qty,
        local_position_qty=local_position_qty,
        notes=notes,
    )


@dataclass(frozen=True)
class MarketRoundtripResult:
    buy_order_id: str
    buy_reconciliation: ReconciliationRecord
    sell_order_id: str
    sell_reconciliation: ReconciliationRecord


async def _place_market_and_reconcile(
    adapter: CanaryAdapter,
    *,
    symbol: str,
    side: OrderSide,
    quantity: Decimal,
    local_position_qty: Decimal,
) -> tuple[Order, ReconciliationRecord]:
    order = Order(
        client_order_id=_client_order_id(f"market-{side.value.lower()}"),
        strategy_id="l4-30b-canary",
        strategy_version="v1",
        symbol=symbol,
        exchange="bitget",
        side=side,
        order_type=OrderType.MARKET,
        quantity=quantity,
        asset_class=AssetClass.CRYPTO,
    )
    placed = await adapter.place_order(order)
    exchange_order_id = _require_exchange_order_id(placed)

    live = await adapter.get_order(exchange_order_id)
    history_rows = await adapter.get_order_history(symbol=symbol)
    history_order = next(
        (row for row in history_rows if row.exchange_order_id == exchange_order_id), None
    )
    reconciliation = reconcile_three_way(
        live_order=live, history_order=history_order, local_position_qty=local_position_qty
    )
    return live, reconciliation


async def market_order_roundtrip(
    adapter: CanaryAdapter,
    *,
    symbol: str,
    quantity: Decimal,
) -> MarketRoundtripResult:
    """DoD 3 — 5 USDT 시장가 매수 -> 체결 대사 -> 동일 수량 매도."""
    buy_order, buy_recon = await _place_market_and_reconcile(
        adapter, symbol=symbol, side=OrderSide.BUY, quantity=quantity, local_position_qty=quantity
    )
    sell_qty = buy_order.filled_quantity if buy_order.filled_quantity > 0 else quantity
    sell_order, sell_recon = await _place_market_and_reconcile(
        adapter,
        symbol=symbol,
        side=OrderSide.SELL,
        quantity=sell_qty,
        # 매도는 "방금 매수로 확보한 만큼 전량 청산"이 로컬이 기대하는
        # 수량이다 — 매수 체결량(sell_qty)이 곧 로컬 기대 포지션 변화량.
        local_position_qty=sell_qty,
    )
    return MarketRoundtripResult(
        buy_order_id=buy_order.exchange_order_id or "",
        buy_reconciliation=buy_recon,
        sell_order_id=sell_order.exchange_order_id or "",
        sell_reconciliation=sell_recon,
    )
