"""Disposable module databases for tests that deliberately seed incomplete orders."""
from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest

from tests.support.db import drop_worker_database, ensure_worker_database, template_database_url


@pytest.fixture(scope="module", autouse=True)
def isolated_raw_orders():
    from tests.conftest import _TEST_ENV

    template = template_database_url()
    clone = "r" + uuid4().hex[:8]
    database_url = asyncio.run(ensure_worker_database(template, clone))
    try:
        with pytest.MonkeyPatch.context() as patch:
            patch.setenv("DATABASE_URL", database_url)
            patch.setitem(_TEST_ENV, "DATABASE_URL", database_url)
            yield database_url
    finally:
        asyncio.run(drop_worker_database(template, clone))
