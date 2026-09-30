"""비동기 측정 결과 및 예외 발생 시 coverage 복원 검증."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from tests.support import coverage_pause


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, ValueError, RuntimeError, TimeoutError])
async def test_async_sample_restores_coverage(perf_budget, monkeypatch, failure):
    active = Mock()
    monkeypatch.setattr(
        coverage_pause, "coverage",
        SimpleNamespace(Coverage=SimpleNamespace(current=lambda: active)),
    )
    result = object()

    async def operation():
        active.stop.assert_called_once_with()
        active.start.assert_not_called()
        if failure:
            raise failure("injected")
        return result

    if failure:
        with pytest.raises(failure, match="injected"):
            await perf_budget.sample_async(operation)
    else:
        measured = await perf_budget.sample_async(operation)
        assert measured.result is result
        assert measured.wall_ms >= 0
        assert measured.cpu_ms >= 0
    active.start.assert_called_once_with()
