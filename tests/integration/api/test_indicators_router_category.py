"""IND-12 — category filter integration test for indicators router.

Split from test_indicators_router.py to keep the main file lean.
Tests that the `category` query parameter correctly filters the catalog.
"""

from __future__ import annotations

from httpx import AsyncClient

from src.core.indicators.catalog.registry_tiers import DEFAULT_STATIC_CATALOG

from .test_indicators_router import PATH, _register


async def test_category_filter_matches_core_talib_group(client: AsyncClient) -> None:
    headers, _ = await _register(client)
    category = DEFAULT_STATIC_CATALOG["SMA"].category
    response = await client.get(PATH, params={"category": category, "limit": 200}, headers=headers)
    items = response.json()["data"]["items"]
    assert items
    assert all(item["category"] == category for item in items)
    assert any(item["name"] == "SMA" for item in items)
