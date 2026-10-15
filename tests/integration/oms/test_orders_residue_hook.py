"""FA-15 / I-07, I-10: real pytest must blame the eventless writer module."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.integration.oms.conftest import create_test_user, insert_order
from tests.support.raw_order_isolation import isolated_raw_orders as isolated_raw_orders


async def test_dummy_writer_passes_but_module_teardown_turns_red(pool, tmp_path):
    user_id = await create_test_user(pool)
    async with pool.acquire() as conn:
        order_id = await insert_order(conn, user_id)
    (tmp_path / "conftest.py").write_text(
        "import os\n"
        "from tests.support.orders_residue import OrdersResidueSentinel\n"
        "def pytest_configure(config):\n"
        "    config.pluginmanager.register(OrdersResidueSentinel(os.environ['DATABASE_URL']))\n",
        encoding="utf-8",
    )
    (tmp_path / "test_dummy_writer.py").write_text(
        "import asyncio, asyncpg, os\n"
        "def test_leaves_eventless_order():\n"
        "    async def write():\n"
        "        conn = await asyncpg.connect(os.environ['DATABASE_URL'].replace("
        "'postgresql+asyncpg://', 'postgresql://'))\n"
        "        try:\n"
        "            await conn.execute("
        "\"UPDATE orders SET price = price + 1 WHERE order_id = $1::uuid\", "
        "os.environ['SENTINEL_TEST_ORDER_ID'])\n"
        "        finally:\n"
        "            await conn.close()\n"
        "    asyncio.run(write())\n",
        encoding="utf-8",
    )
    env = dict(os.environ, PYTHONPATH=str(Path.cwd()), PYTEST_ADDOPTS="",
               SENTINEL_TEST_ORDER_ID=str(order_id))
    env.pop("PYTEST_XDIST_WORKER", None)
    result = subprocess.run(
        [sys.executable, "-m", "pytest", str(tmp_path / "test_dummy_writer.py"),
         "--confcutdir", str(tmp_path), "-q", "-p", "no:cacheprovider", "--tb=short"],
        env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
    )
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert "1 passed" in output and "1 error" in output, output
    assert "eventless orders residue" in output, output
    assert "test_dummy_writer.py::test_leaves_eventless_order" in output, output
    assert str(order_id) in output, output
