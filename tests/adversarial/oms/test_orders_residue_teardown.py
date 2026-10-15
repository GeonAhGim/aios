"""FA-15 / I-07, I-10: a real pytest teardown attributes residue to its writer."""
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import pytest

from tests.adversarial.oms.test_cross_tenant_isolation import (
    isolated_event_pool,  # noqa: F401
)


@pytest.mark.parametrize("leave_residue", [False, True])
async def test_pytest_reports_the_polluting_module_after_finalizers(
    isolated_event_pool, tmp_path, leave_residue,  # noqa: F811
):
    async with isolated_event_pool.acquire() as conn:
        database = await conn.fetchval("SELECT current_database()")
    parts = urlsplit(os.environ["DATABASE_URL"])
    clone_url = urlunsplit(parts._replace(path="/" + database))
    (tmp_path / "conftest.py").write_text(
        "import os\n"
        "from tests.support.orders_residue import OrdersResidueSentinel\n"
        "def pytest_configure(config):\n"
        "    config.pluginmanager.register(OrdersResidueSentinel("
        "os.environ['SENTINEL_TEST_URL']), 'orders-residue')\n",
        encoding="utf-8",
    )
    (tmp_path / "test_dummy_writer.py").write_text(
        "import asyncio, os, asyncpg, pytest\n"
        "from tests.adversarial.oms import test_cross_tenant_isolation as source\n"
        "async def write_residue():\n"
        "    pool = await asyncpg.create_pool(os.environ['SENTINEL_TEST_URL'].replace("
        "'postgresql+asyncpg://', 'postgresql://'), min_size=1, max_size=2)\n"
        "    try:\n"
        "        await source.test_list_order_events_cross_tenant_does_not_leak_existence(pool)\n"
        "    finally:\n"
        "        await pool.close()\n"
        "@pytest.fixture(scope='module', autouse=True)\n"
        "def writer():\n"
        "    yield\n"
        "    if os.environ['LEAVE_RESIDUE'] == '1':\n"
        "        asyncio.run(write_residue())\n"
        "def test_dummy():\n"
        "    assert True\n",
        encoding="utf-8",
    )
    env = dict(os.environ, SENTINEL_TEST_URL=clone_url,
               LEAVE_RESIDUE=str(int(leave_residue)),
               PYTHONPATH=str(Path(__file__).resolve().parents[3]))
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "--confcutdir", str(tmp_path), str(tmp_path / "test_dummy_writer.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60,
    )
    output = result.stdout + result.stderr
    if leave_residue:
        assert result.returncode == 1, output
        assert "1 passed" in output and "1 error" in output, output
        assert "Sentinel: orders residue at module/session teardown after" in output
        assert "test_dummy_writer.py::test_dummy" in output
    else:
        assert result.returncode == 0, output
        assert "1 passed" in output
