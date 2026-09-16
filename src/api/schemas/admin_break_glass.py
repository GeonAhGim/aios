"""PLT-35(task-3850) -- break-glass grant request body schema."""

from __future__ import annotations

from pydantic import BaseModel

from src.core.security.break_glass import MAX_GRANT_MINUTES, BreakGlassScope


class RequestBreakGlassGrantRequest(BaseModel):
    scope: BreakGlassScope
    reason: str
    ttl_minutes: int = MAX_GRANT_MINUTES
