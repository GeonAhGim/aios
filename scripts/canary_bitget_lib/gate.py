"""L4-30b — 로컬 pre-trade 게이트 (어댑터 호출 이전 평가, 결정론적 규칙만).

Spec: task-2750, ADR-2026-08-29-E 설계 제약 1. `scripts/canary_bitget.py`에서
분리된 책임 단위 — 상한/화이트리스트 평가와 "REJECT면 어댑터를 절대 호출하지
않는다"는 배선만 담당한다(RATCHET-split task-10863, ADR-2026-09-10-C LOC
규율).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Protocol

from scripts.canary_bitget_lib.config import CanaryLimits
from src.data.models.trading import Order

logger = logging.getLogger(__name__)

_DEFAULT_MIN_NOTIONAL_USDT = Decimal("1")


class GateVerdict(str, Enum):
    ALLOW = "ALLOW"
    REJECT = "REJECT"


@dataclass(frozen=True)
class GateDecision:
    verdict: GateVerdict
    reason: str


@dataclass
class CanarySessionState:
    """세션 중 누적치 — 오케스트레이터(`submit_with_gate`)만 갱신한다."""

    cumulative_usdt: Decimal = Decimal("0")
    fills: int = 0


def evaluate_pretrade_gate(
    *,
    symbol: str,
    notional_usdt: Decimal,
    limits: CanaryLimits,
    state: CanarySessionState,
    min_notional_usdt: Decimal = _DEFAULT_MIN_NOTIONAL_USDT,
) -> GateDecision:
    """DoD 4 — 최소 수량 미만·상한 초과·화이트리스트 밖 심볼을 로컬에서
    REJECT한다(거래소로 안 나감). ADR-2026-08-29-E 설계 제약 1(결정론적
    규칙만, LLM/Agent 판단 없음)을 그대로 따른다."""
    if symbol not in limits.symbols:
        return GateDecision(
            GateVerdict.REJECT, f"symbol {symbol} not in whitelist {sorted(limits.symbols)}"
        )
    if notional_usdt < min_notional_usdt:
        return GateDecision(
            GateVerdict.REJECT,
            f"notional {notional_usdt} below min_notional_usdt {min_notional_usdt}",
        )
    if notional_usdt > limits.max_order_usdt:
        return GateDecision(
            GateVerdict.REJECT,
            f"notional {notional_usdt} exceeds max_order_usdt {limits.max_order_usdt}",
        )
    if state.cumulative_usdt + notional_usdt > limits.max_total_usdt:
        return GateDecision(
            GateVerdict.REJECT,
            f"cumulative {state.cumulative_usdt}+{notional_usdt} exceeds "
            f"max_total_usdt {limits.max_total_usdt}",
        )
    if state.fills >= limits.max_fills:
        return GateDecision(GateVerdict.REJECT, f"max_fills {limits.max_fills} reached")
    return GateDecision(GateVerdict.ALLOW, "ok")


def build_rejection_fixtures(limits: CanaryLimits) -> list[tuple[str, str, Decimal]]:
    """DoD 4의 3가지 구체 거부 입력 — (label, symbol, notional_usdt)."""
    symbol = next(iter(limits.symbols)) if limits.symbols else "BTC/USDT"
    outside_whitelist = "ETH/USDT" if symbol != "ETH/USDT" else "SOL/USDT"
    return [
        ("below_min_notional", symbol, _DEFAULT_MIN_NOTIONAL_USDT / Decimal("2")),
        ("exceeds_max_order_usdt", symbol, limits.max_order_usdt + Decimal("1")),
        ("symbol_outside_whitelist", outside_whitelist, limits.max_order_usdt),
    ]


class CanaryAdapter(Protocol):
    """이 스크립트가 실제로 쓰는 어댑터 메서드만 좁혀 선언한다 — mock
    어댑터 테스트가 `BitgetAdapter` 전체를 흉내 낼 필요 없이 이 Protocol만
    구현하면 된다."""

    account_mode: Any

    async def get_balance(self, asset: str | None = None) -> list[Any]: ...

    async def get_ticker(self, symbol: str) -> Any: ...

    async def place_order(self, order: Order) -> Order: ...

    async def get_order(self, order_id: str) -> Order: ...

    async def get_order_history(
        self, symbol: str | None = None, *, limit: int = 100
    ) -> list[Order]: ...

    async def cancel_order(self, order_id: str) -> bool: ...


async def submit_with_gate(
    adapter: CanaryAdapter,
    order: Order,
    *,
    notional_usdt: Decimal,
    limits: CanaryLimits,
    state: CanarySessionState,
) -> tuple[Order | None, GateDecision]:
    """DoD 4의 핵심 배선 — REJECT면 `adapter.place_order`를 절대 호출하지
    않는다. 이 함수 자체가 mock 어댑터 테스트의 증명 대상이다."""
    decision = evaluate_pretrade_gate(
        symbol=order.symbol, notional_usdt=notional_usdt, limits=limits, state=state
    )
    if decision.verdict is GateVerdict.REJECT:
        logger.warning("canary pretrade gate REJECT: %s", decision.reason)
        return None, decision
    placed = await adapter.place_order(order)
    state.cumulative_usdt += notional_usdt
    state.fills += 1
    return placed, decision
