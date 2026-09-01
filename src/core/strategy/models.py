"""03_core_modules_v1.1.md#§3.5 — Signal.

FD-8.1(StrategyEngine.evaluate)이 만드는 구조화된 신호. "의도"만 담는다 —
target_position은 Draft 값(0)으로 채워 넘기고 FD-8.2(PortfolioEngine)가
실제 수량으로 덮어쓴다(8.2-A Master Authority — 이 계층은 얼마를 살지
결정하지 않는다).
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from src.data.models.strategy_fsm import FSMState
from src.data.models.trading import OrderSide


class Signal(BaseModel):
    strategy_id: str
    strategy_version: str
    symbol: str
    direction: OrderSide
    confidence: float
    target_position: Decimal
    stop_loss: Decimal | None
    take_profit: Decimal | None
    timestamp: datetime
    # 03번 §3.5 원문에는 없던 필드 — FD-8.0(FSM 실행상태 추적) 도입으로
    # 실행 루프가 "이 신호가 어느 전이를 대표하는가"를 알아야만 fsm_state를
    # 올바른 PENDING 상태로 전이시킬 수 있다(HOLDING에서 exit/stop_loss
    # 둘 다 SELL 방향이라 direction만으로는 구분 불가). Signal 자체가
    # "전이 결과로 생성됨"이라는 03번 원문 설명과 부합하는 자연스러운 확장.
    to_state: FSMState
