"""Disposable databases for legacy order-service writers without complete events."""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

import asyncpg

from tests.support.db import drop_worker_database, ensure_worker_database, template_database_url


@asynccontextmanager
async def isolated_order_service_pool():
    template = template_database_url()
    worker = "osvc_" + os.environ.get("PYTEST_XDIST_WORKER", "master")
    database_url = await ensure_worker_database(template, worker)
    try:
        pool = await asyncpg.create_pool(
            database_url.replace("postgresql+asyncpg://", "postgresql://"),
            min_size=1, max_size=2,
        )
        try:
            yield pool
        finally:
            await pool.close()
    finally:
        await drop_worker_database(template, worker)
