"""공유 조건 비교 로직 — PreviewCalculator(FD-14.4)와 AlertService(신설)가
동일한 "지표값 vs 임계값" 판정을 쓴다. preview_service.py에 있던
private `_compare()`를 그대로 옮긴 것 — 알림 기능이 생기며 두 번째
사용처가 생겨 공유 모듈로 뺐다(로직 변경 없음).
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
