"""Failure injection for the raw-order module clone lifecycle."""
import os
from unittest.mock import AsyncMock

import pytest

from tests.support import raw_order_isolation as isolation


@pytest.mark.parametrize("failure", [False, True])
def test_clone_is_dropped_and_both_url_readers_restored(monkeypatch, failure):
    from tests.conftest import _TEST_ENV

    monkeypatch.setenv("DATABASE_URL", "before")
    monkeypatch.setitem(_TEST_ENV, "DATABASE_URL", "legacy-before")
    monkeypatch.setattr(isolation, "template_database_url", lambda: "template")
    create = AsyncMock(return_value="clone")
    drop = AsyncMock()
    monkeypatch.setattr(isolation, "ensure_worker_database", create)
    monkeypatch.setattr(isolation, "drop_worker_database", drop)
    fixture = isolation.isolated_raw_orders.__wrapped__()
    assert next(fixture) == "clone"
    assert os.environ["DATABASE_URL"] == _TEST_ENV["DATABASE_URL"] == "clone"
    if failure:
        with pytest.raises(RuntimeError, match="injected failure"):
            fixture.throw(RuntimeError("injected failure"))
    else:
        with pytest.raises(StopIteration):
            next(fixture)
    assert os.environ["DATABASE_URL"] == "before"
    assert _TEST_ENV["DATABASE_URL"] == "legacy-before"
    drop.assert_awaited_once_with(*create.call_args.args)
