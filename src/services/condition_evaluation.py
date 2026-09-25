"""Shared condition comparison logic — PreviewCalculator(FD-14.4) and AlertService(new)
use the same "indicator value vs threshold" verdict. This is the private `_compare()`
from preview_service.py moved into a shared module when a second call site
appeared with the alert feature (no logic changes).
"""
from __future__ import annotations

from typing import Literal

Operator = Literal[">", "<", ">=", "<=", "==", "crosses_above", "crosses_below"]


def compare_value(
    value: float, operator: str, threshold: float, prev_value: float | None
) -> bool:
    if operator == ">":
        return value > threshold
    if operator == "<":
        return value < threshold
    if operator == ">=":
        return value >= threshold
    if operator == "<=":
        return value <= threshold
    if operator == "==":
        return value == threshold
    if operator == "crosses_above":
        return prev_value is not None and prev_value <= threshold < value
    if operator == "crosses_below":
        return prev_value is not None and prev_value >= threshold > value
    raise ValueError(f"지원하지 않는 연산자입니다: {operator}")
