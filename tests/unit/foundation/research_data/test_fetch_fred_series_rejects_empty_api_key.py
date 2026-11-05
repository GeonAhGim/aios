"""RD-13 -- `fetch_fred_series` rejects empty API key.

Split from test_fred_source.py (task-22134).
Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md sec.9 RD-13
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.foundation.research_data.adapters.sources.fred import (
    FredApiError,
    FredSeriesRequest,
    fetch_fred_series,
)
from src.foundation.research_data.domain.macro_series import MacroFrequency


async def test_fetch_fred_series_rejects_empty_api_key() -> None:
    """Empty `api_key` raises `FredApiError` — no silent skip (fail-closed)."""
    client = AsyncMock()
    request = FredSeriesRequest(series_id="UNRATE", frequency=MacroFrequency.MONTHLY)
    with pytest.raises(FredApiError):
        await fetch_fred_series(client, api_key="", request=request, publication_lag_days=20)
    client.get_json.assert_not_called()
