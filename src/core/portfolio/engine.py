"""FD-8.2 — 포트폴리오 배분 결정 (PortfolioEngine).

Spec: 기능설계문서_v1.21.md#FD-8.2, 03_core_modules_v1.1.md#§3.6

자본 배분 한도는 이미 FD-16.1(실행 생성 시점)에서 확정돼 있다 — 이
클래스는 그 한도 안에서 "지금 이 신호에 얼마를 쓸지"만 계산한다(한도
재설정이 아님). Phase 1은 분할청산 미지원 — SELL은 항상 전량청산.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.core.portfolio.models import AllocationDecision
from src.core.strategy.models import Signal
from src.data.models.trading import OrderSide


class PortfolioEngineError(Exception):
    """FD-8.2 예외상황 — 상위(FD-8.1)가 정상 흐름에서는 절대 만들지 않아야
    하는 상태 조합(무포지션인데 SELL 신호, 이미 보유 중인데 재진입 BUY
    신호). 발생하면 Executor가 아니라 FD-8.1의 로직 버그다."""


class PortfolioEngine:
    def allocate(
        self, signal: Signal, current_portfolio_state: dict[str, Any]
    ) -> AllocationDecision | None:
        """`current_portfolio_state` 필수 키:

        - `allocated_capital: Decimal` — 이 실행의 FD-16.1 자본배분 한도
        - `position_quantity: Decimal` — 현재 보유 수량(0이면 무포지션)
        - `current_price: Decimal | None` — 신호 발생 시점 현재가
          (조회 실패 시 None — 이 틱을 스킵하기 위한 신호)
        - `total_equity: Decimal` — 계좌 총자산(capital_pct 계산용)

        03번 §3.6 시그니처는 반환형을 `AllocationDecision`(non-Optional)으로
        적어뒀지만, FD-8.2 예외상황("현재가 조회 실패 시 이 틱을 스킵")을
        만족하려면 실제로는 Optional이어야 한다 — FD-8.1의 `evaluate()`가
        이미 같은 이유로 `Signal | None`을 반환하는 것과 동일한 원칙 확장.
        """
        current_price = current_portfolio_state.get("current_price")
        if current_price is None or current_price <= 0:
            return None  # 가격 정보 없이 수량을 추정하지 않는다 — 다음 틱 재시도

        position_quantity: Decimal = current_portfolio_state["position_quantity"]
        allocated_capital: Decimal = current_portfolio_state["allocated_capital"]
        total_equity: Decimal = current_portfolio_state["total_equity"]

        if signal.direction == OrderSide.BUY:
            if position_quantity != 0:
                raise PortfolioEngineError(
                    "이미 보유 포지션이 있는 상태에서 진입(BUY) 신호가 발생했습니다 — "
                    "IDLE에서만 entry 전이가 나와야 하므로 FD-8.1 로직 오류입니다."
                )
            approved_quantity = allocated_capital / current_price
        else:
            if position_quantity == 0:
                raise PortfolioEngineError(
                    "포지션이 없는 상태에서 SELL(exit/stop_loss) 신호가 발생했습니다 — "
                    "FD-8.1 로직 오류입니다."
                )
            approved_quantity = position_quantity  # Phase 1은 분할청산 미지원 — 전량청산

        capital_pct = (approved_quantity * current_price) / total_equity * Decimal("100")

        return AllocationDecision(
            symbol=signal.symbol,
            strategy_id=signal.strategy_id,
            approved_quantity=approved_quantity,
            capital_pct=capital_pct,
        )
