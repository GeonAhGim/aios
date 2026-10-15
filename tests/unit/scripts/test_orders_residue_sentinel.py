"""Boundary ordering and fail-closed checks for the worker sentinel."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.support import orders_residue as sentinel


@pytest.fixture
def scan(monkeypatch):
    conn = MagicMock()
    conn.fetch = AsyncMock(return_value=[])
    conn.transaction.return_value.__aenter__ = AsyncMock()
    pool = MagicMock()
    pool.close = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    monkeypatch.setattr(sentinel.asyncpg, "create_pool", AsyncMock(return_value=pool))
    verify = AsyncMock(return_value=SimpleNamespace(mismatches=[]))
    monkeypatch.setattr(sentinel.replay_verify, "verify", verify)
    return pool, verify


async def test_orders_mismatch_fails_and_closes_pool(scan):
    pool, verify = scan
    verify.return_value.mismatches = [SimpleNamespace(domain="orders", key="source-order")]
    with pytest.raises(AssertionError, match="module writer.*source-order"):
        await sentinel.check_orders("unused", "module writer")
    pool.close.assert_awaited_once()


async def test_non_order_mismatch_is_not_misattributed(scan):
    pool, verify = scan
    verify.return_value.mismatches = [SimpleNamespace(domain="ledger", key="account")]
    await sentinel.check_orders("unused", "boundary")
    pool.close.assert_awaited_once()


async def test_infrastructure_failure_is_not_silently_clean(scan):
    pool, verify = scan
    verify.side_effect = ConnectionError("injected scan failure")
    with pytest.raises(ConnectionError, match="injected scan failure"):
        await sentinel.check_orders("unused", "boundary")
    pool.close.assert_awaited_once()


def test_baseline_fails_before_test_fixtures(monkeypatch):
    check = AsyncMock(side_effect=AssertionError("dirty template"))
    monkeypatch.setattr(sentinel, "check_orders", check)
    plugin = sentinel.OrdersResidueSentinel("captured")
    with pytest.raises(AssertionError, match="dirty template"):
        plugin.pytest_runtest_setup(SimpleNamespace(nodeid="first"))
    assert not plugin.started
    assert "worker baseline" in check.call_args.args[1]


@pytest.mark.parametrize("next_path", [None, "other.py"])
def test_boundary_check_runs_after_all_fixture_finalizers(monkeypatch, next_path):
    check = AsyncMock()
    monkeypatch.setattr(sentinel, "check_orders", check)
    plugin = sentinel.OrdersResidueSentinel("captured")
    item = SimpleNamespace(path="writer.py", nodeid="writer.py::last")
    nextitem = None if next_path is None else SimpleNamespace(path=next_path)
    hook = plugin.pytest_runtest_teardown(item, nextitem)
    next(hook)
    check.assert_not_called()
    monkeypatch.setenv("DATABASE_URL", "fixture-redirect")
    with pytest.raises(StopIteration):
        next(hook)
    check.assert_awaited_once_with("captured", "module/session teardown after writer.py::last")


def test_same_module_has_no_additional_scan_by_default(monkeypatch):
    monkeypatch.delenv("AIOS_ORDERS_SENTINEL_EACH_TEST", raising=False)
    check = AsyncMock()
    monkeypatch.setattr(sentinel, "check_orders", check)
    plugin = sentinel.OrdersResidueSentinel("captured")
    item = SimpleNamespace(path="same.py", nodeid="same.py::first")
    hook = plugin.pytest_runtest_teardown(item, item)
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)
    assert check.await_count == 0


def test_diagnostic_mode_attributes_each_test(monkeypatch):
    monkeypatch.setenv("AIOS_ORDERS_SENTINEL_EACH_TEST", "1")
    check = AsyncMock()
    monkeypatch.setattr(sentinel, "check_orders", check)
    item = SimpleNamespace(path="same.py", nodeid="same.py::writer")
    hook = sentinel.OrdersResidueSentinel("captured").pytest_runtest_teardown(item, item)
    next(hook)
    with pytest.raises(StopIteration):
        next(hook)
    check.assert_awaited_once_with("captured", "test teardown after same.py::writer")


@pytest.mark.parametrize("field", ["version", "status", "filled_quantity"])
async def test_eventless_state_mismatch_fails_at_source(scan, monkeypatch, field):
    pool, _ = scan
    conn = pool.acquire.return_value.__aenter__.return_value
    conn.fetch.return_value = [{"order_id": "raw-order"}]
    monkeypatch.setattr(
        sentinel.replay_verify, "_order_pair",
        AsyncMock(return_value=({field: "expected"}, {field: "changed"})),
    )
    with pytest.raises(AssertionError, match="eventless.*raw writer.*raw-order"):
        await sentinel.check_orders("unused", "raw writer")
    pool.close.assert_awaited_once()


async def test_eventless_scan_failure_propagates_and_closes_pool(scan):
    pool, _ = scan
    conn = pool.acquire.return_value.__aenter__.return_value
    conn.fetch.side_effect = ConnectionError("raw scan failure")
    with pytest.raises(ConnectionError, match="raw scan failure"):
        await sentinel.check_orders("unused", "raw writer")
    pool.close.assert_awaited_once()
