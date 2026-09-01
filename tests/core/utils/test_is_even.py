"""is_even 순수 함수 단위 테스트."""

import pytest

from src.core.utils.is_even import is_even

_INTEGER_PARITY_CASES = [
    # even
    (0, True),
    (2, True),
    (4, True),
    (100, True),
    (-2, True),
    (-4, True),
    (-100, True),
    # odd
    (1, False),
    (3, False),
    (5, False),
    (99, False),
    (-1, False),
    (-3, False),
    (-99, False),
]


@pytest.mark.parametrize(("number", "expected"), _INTEGER_PARITY_CASES)
def test_is_even(number: int, expected: bool) -> None:
    assert is_even(number) == expected
