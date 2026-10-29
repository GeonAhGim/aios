"""2.5 / 2.6 — FSMStrategyConfig.

Spec: 01_data_models_v1.3.md#§1.2 (9.11 schema)
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field


class FSMState(StrEnum):
    IDLE = "IDLE"
    BUY_ORDER_PENDING = "BUY_ORDER_PENDING"
    HOLDING = "HOLDING"
    SELL_ORDER_PENDING = "SELL_ORDER_PENDING"
    STOP_LOSS = "STOP_LOSS"
    EMERGENCY_EXIT = "EMERGENCY_EXIT"


class FSMTransition(BaseModel):
    from_state: FSMState
    to_state: FSMState
    condition: str  # condition expression string; evaluation logic is in
    # the FROZEN Zone (Strategy Engine)


class FSMStrategyConfig(BaseModel):
    """9.11 — Strategies must be defined only via this FSM structure
    (prevents infinite loops / state entanglement)."""

    strategy_id: str
    version: str  # e.g. "v1.4"
    target_asset: str
    market: str  # "crypto" | "kr_stock", etc.
    exchange: str
    initial_state: FSMState = FSMState.IDLE
    states: list[FSMState]
    transitions: list[FSMTransition]

    # 9.4 Strategy Definition additional fields
    author_agent: str  # which Agent created this (5.4 Strategy Research Agent)
    memory_provenance: list[UUID] = Field(default_factory=list)
    """Reflects 10th red team review — Memory item IDs referenced
    during strategy creation. Implements 4.6-A Memory-Strategy provenance
    linking principle. Verify diversity of this field at 9.5 validation."""
