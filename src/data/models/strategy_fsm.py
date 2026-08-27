"""2.5 / 2.6 — FSMStrategyConfig.

Spec: 01_data_models_v1.3.md#§1.2 (9.11 스키마)
"""
from __future__ import annotations

from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class FSMState(str, Enum):
    IDLE = "IDLE"
    BUY_ORDER_PENDING = "BUY_ORDER_PENDING"
    HOLDING = "HOLDING"
    SELL_ORDER_PENDING = "SELL_ORDER_PENDING"
    STOP_LOSS = "STOP_LOSS"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"


class FSMTransition(BaseModel):
    from_state: FSMState
    to_state: FSMState
    condition: str  # 조건식 문자열 — 평가 로직은 FROZEN Zone(Strategy Engine)에서 구현


class FSMStrategyConfig(BaseModel):
    """9.11 — 전략은 반드시 이 FSM 구조로만 정의한다(무한루프/상태꼬임 방지)."""

    strategy_id: str
    version: str  # 예: "v1.4"
    target_asset: str
    market: str  # "crypto" | "kr_stock" 등
    exchange: str
    initial_state: FSMState = FSMState.IDLE
    states: list[FSMState]
    transitions: list[FSMTransition]

    # 9.4 Strategy Definition 추가 필드
    author_agent: str  # 어떤 Agent가 생성했는지 (5.4 Strategy Research Agent)
    memory_provenance: list[UUID] = Field(default_factory=list)
    """10차 레드팀 반영 — 이 전략 생성에 참조된 Memory 항목 ID들.
    4.6-A Memory-Strategy 출처 연결 원칙 구현. 9.5 검증 시 이 필드의 다양성을 확인한다."""
