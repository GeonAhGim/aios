"""5.7 / 5.8 — Validator 공통 반환 타입.

Spec: 03_core_modules_v1.1.md#§3.3
"""
from __future__ import annotations

from pydantic import BaseModel, Field


class ValidationResult(BaseModel):
    is_valid: bool
    errors: list[str] = Field(default_factory=list)
