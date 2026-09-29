"""QA diagnostic: check placed_at / updated_at timestamps for known orders."""

from __future__ import annotations

import asyncio
import os
from uuid import UUID

import asyncpg


async def main() -> None:
    url: str = os.environ["TEST_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
    async with asyncpg.create_pool(url, min_size=1, max_size=1) as pool:
        async with pool.acquire() as conn:
            order_ids: list[UUID] = [
                UUID("2770de34-2ce7-4e2d-83c4-f4e7b5ce5f9a"),
                UUID("61d24141-0fad-42d5-ac4f-37eb69d107f9"),
                UUID("8328590e-f3c7-4fb9-8dd3-cbffb051ed57"),
                UUID("c7271528-f50e-4dc3-850d-74e5583492c4"),
            ]
            for oid in order_ids:
                row = await conn.fetchrow("SELECT created_at FROM orders WHERE order_id = $1", oid)
                print(f"{oid}: {row['created_at'] if row else 'NOT FOUND'}")


asyncio.run(main())
