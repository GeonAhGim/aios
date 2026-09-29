"""Autouse fixture for `src.core.indicators.generate_specs`'s TA-Lib metadata cache.

Split out of tests/conftest.py (task-8284, loc_over_500) -- conftest.py re-exports
`_reset_talib_metadata_cache` the same way it already re-exports `tx_conn` from
tests/support/db.py, so pytest still picks it up as autouse without change.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def reset_talib_metadata_cache() -> Iterator[None]:
    """task-8279 -- IND-10 `generate_talib_specs`는 monkeypatch 테스트를 지원하기
    위해 TA-Lib 메타데이터를 lazy하게 캐시한다. 각 테스트 전에 캐시를 reset해야
    monkeypatch가 효과를 본다."""
    try:
        from src.core.indicators import generate_specs

        generate_specs._TALIB_METADATA_CACHE = None
    except ImportError:
        pass
    yield
    try:
        from src.core.indicators import generate_specs

        generate_specs._TALIB_METADATA_CACHE = None
    except ImportError:
        pass
