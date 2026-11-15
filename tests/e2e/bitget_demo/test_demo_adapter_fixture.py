"""L4-30 DEEPEN(task-25198) -- `demo_adapter` 픽스처의 finally(aclose) 계약.

픽스처 본체(async generator)를 직접 구동해 teardown 경로를 실측한다. 테스트가
직접 aclose를 부르는 동어반복이 아니라, 픽스처가 스스로 aclose를 호출하는지를 본다.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
from typing import Any

import pytest
from _pytest.outcomes import Skipped

from tests.e2e.bitget_demo import conftest as demo_conftest
from tests.support.bitget_demo_credentials import CREDENTIAL_ENV_VARS


def _fixture_body() -> Callable[[], AsyncGenerator[Any]]:
    fn: Any = demo_conftest.demo_adapter
    body: Callable[[], AsyncGenerator[Any]] = fn._get_wrapped_function()
    return body


class _FakeAdapter:
    last: _FakeAdapter | None = None

    def __init__(self, *_args: object, fail_sync: bool = False, **_kwargs: object) -> None:
        self.closed = 0
        self.fail_sync = fail_sync
        type(self).last = self

    async def sync_server_time(self) -> None:
        if self.fail_sync:
            raise RuntimeError("time sync failed (injected)")

    async def aclose(self) -> None:
        self.closed += 1


@pytest.fixture
def _creds(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for name in CREDENTIAL_ENV_VARS:
        monkeypatch.setenv(name, "aios-test-only-value")
    monkeypatch.setattr(demo_conftest, "BitgetAdapter", _FakeAdapter)
    return monkeypatch


@pytest.mark.negative
async def test_fixture_closes_adapter_when_test_body_raises(_creds: pytest.MonkeyPatch) -> None:
    gen = _fixture_body()()
    adapter = await anext(gen)
    assert adapter.closed == 0
    with pytest.raises(ValueError, match="body failed"):
        await gen.athrow(ValueError("body failed"))
    assert adapter.closed == 1


@pytest.mark.negative
async def test_fixture_closes_adapter_on_normal_teardown(_creds: pytest.MonkeyPatch) -> None:
    gen = _fixture_body()()
    adapter = await anext(gen)
    with pytest.raises(StopAsyncIteration):
        await anext(gen)
    assert adapter.closed == 1


@pytest.mark.negative
async def test_fixture_skips_before_constructing_adapter_without_credentials(
    _creds: pytest.MonkeyPatch,
) -> None:
    _creds.delenv(CREDENTIAL_ENV_VARS[2], raising=False)
    _FakeAdapter.last = None
    with pytest.raises(Skipped):
        await anext(_fixture_body()())
    assert _FakeAdapter.last is None


@pytest.mark.failure_injection
async def test_time_sync_failure_propagates_and_adapter_is_not_yielded(
    _creds: pytest.MonkeyPatch,
) -> None:
    _creds.setattr(
        demo_conftest,
        "BitgetAdapter",
        lambda *a, **k: _FakeAdapter(*a, fail_sync=True, **k),
    )
    with pytest.raises(RuntimeError, match="time sync failed"):
        await anext(_fixture_body()())


@pytest.mark.perf
async def test_fixture_setup_teardown_within_budget(_creds: pytest.MonkeyPatch) -> None:
    import time

    start = time.perf_counter()
    for _ in range(50):
        gen = _fixture_body()()
        await anext(gen)
        with pytest.raises(StopAsyncIteration):
            await anext(gen)
    assert (time.perf_counter() - start) * 1000 / 50 < 20.0
