"""정수의 짝수 여부를 판별하는 유틸리티."""

from __future__ import annotations


def is_even(number: int) -> bool:
    """정수가 2로 나누어떨어지면 True, 아니면 False를 반환한다."""
    return number % 2 == 0
