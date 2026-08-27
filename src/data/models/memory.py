"""2.12 — Memory 모델 (4.6-A Provenance Tracking).

Spec: 01_data_models_v1.3.md#§1.5
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from src.data.models.base import ProvenanceStatus


class MemoryType(str, Enum):
    SHORT_TERM = "SHORT_TERM"
    WORKING = "WORKING"
    LONG_TERM = "LONG_TERM"
    EPISODIC = "EPISODIC"
    DECISION = "DECISION"
    FAILURE = "FAILURE"
    PERFORMANCE = "PERFORMANCE"


class MemoryEntry(BaseModel):
    """4.6-A — 모든 Memory 항목은 출처·신뢰도·검증상태를 가진다."""

    memory_id: UUID = Field(default_factory=uuid4)
    memory_type: MemoryType
    content: dict[str, Any]
    source_agent: str
    source_task_id: UUID | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    status: ProvenanceStatus = ProvenanceStatus.UNVERIFIED
    verified_by: str | None = None  # 검증한 Agent (Auditor 등)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verified_at: datetime | None = None
