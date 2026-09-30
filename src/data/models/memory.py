"""2.12 — Memory model (4.6-A Provenance Tracking).

Spec: 01_data_models_v1.3.md#§1.5
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

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
    """4.6-A — Every Memory item carries provenance, confidence, and verification status."""

    memory_id: UUID = Field(default_factory=uuid4)
    memory_type: MemoryType
    content: dict[str, Any]
    source_agent: str
    source_task_id: UUID | None = None
    confidence: float = Field(ge=0.0, le=1.0, default=0.5)
    status: ProvenanceStatus = ProvenanceStatus.UNVERIFIED
    verified_by: str | None = None  # Agent that verified (e.g., Auditor)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    verified_at: datetime | None = None

    @model_validator(mode="after")
    def _verify_provenance_order(self) -> MemoryEntry:
        """verified_at must not precede created_at."""
        if self.verified_at is not None and self.created_at is not None:
            if self.verified_at < self.created_at:
                raise ValueError("verified_at must not precede created_at")
        return self

    @model_validator(mode="after")
    def _verify_verified_by_consistency(self) -> MemoryEntry:
        """If verified_by is set, status must be VERIFIED."""
        if self.verified_by is not None and self.status != ProvenanceStatus.VERIFIED:
            raise ValueError("verified_by requires status=VERIFIED")
        return self
