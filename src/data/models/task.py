"""2.3 / 2.4 — AIOSTask.

Spec: 01_data_models_v1.3.md#§1.1 (1:1 Pydantic implementation of section 4.3 schema)
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    PENDING = "PENDING"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    VERIFYING = "VERIFYING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class AIOSTask(BaseModel):
    """1:1 Pydantic implementation of section 4.3 JSON Schema.

    Integrated with 16.2 Capability Token via task_id.
    """

    task_id: UUID = Field(default_factory=uuid4)
    parent_task_id: UUID | None = None
    objective: str
    assigned_agent: str  # refers to agent_id from Agent Registry (section 5)
    required_permission_level: int = Field(ge=0, le=6)  # 4.5 Permission Level
    status: TaskStatus = TaskStatus.PENDING
    input_payload: dict[str, Any] = Field(default_factory=dict)
    output_result: dict[str, Any] | None = None
    retry_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None

    model_config = {"use_enum_values": True}
