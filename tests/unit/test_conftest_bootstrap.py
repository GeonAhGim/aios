"""task-7693 DEEPEN(원 리프 task-408) — `tests/conftest.py`의 부트스트랩
헬퍼/훅을 직접 검증하는 negative/실패주입 테스트.

`tests/conftest.py`는 테스트 파일이 아니라 pytest 플러그인 모듈이라 자기 자신을
`test_*` 함수로 채우면 `check_code_ratchets.py`의 `loc_over_500` 기준선을 건드린다
(487줄 -> 500줄 초과). 대신 그 모듈의 순수 헬퍼/훅을 여기서 임포트해 독립적으로
호출한다 -- conftest.py가 무엇을 의존성으로 끌어오는지와 무관하게 로직만 검증한다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Iterator

import asyncpg
import pytest

from tests.conftest import (
    _ROOT_ENV_PATH,
    _TEST_ENV,
    PerfBudget,
    _test_dotenv_values,
    pytest_collection_modifyitems,
    retry_too_many_connections,
)


class _FakeMarker:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeItem:
    """`pytest.Item`을 흉내내는 최소 duck-type. `pytest_collection_modifyitems`는
    `get_closest_marker`/`add_marker`/`iter_markers`만 호출하므로 그 셋만 구현한다."""

    def __init__(self, markers: tuple[str, ...] = ()) -> None:
        self._markers = [_FakeMarker(name) for name in markers]

    def get_closest_marker(self, name: str) -> _FakeMarker | None:
        for marker in self._markers:
            if marker.name == name:
                return marker
        return None

    def add_marker(self, marker: _FakeMarker) -> None:
        self._markers.append(marker)

    def iter_markers(self) -> Iterator[_FakeMarker]:
        return iter(self._markers)


class _FakeHook:
    def __init__(self) -> None:
        self.deselected_calls: list[list[_FakeItem]] = []

    def pytest_deselected(self, items: list[_FakeItem]) -> None:
        self.deselected_calls.append(list(items))


class _FakeConfig:
    """`pytest.Config`를 흉내내는 최소 duck-type — `getoption("markexpr")`와
    `.hook.pytest_deselected(...)`만 필요하다."""

    def __init__(self, markexpr: str = "") -> None:
        self._markexpr = markexpr
        self.hook = _FakeHook()

    def getoption(self, name: str) -> str | None:
        if name == "markexpr":
            return self._markexpr
        return None


def test_pytest_collection_modifyitems_deselects_default_excluded_marker():
    """마커 없이(`pytest tests/`) 실행하면 `redis` 마커 테스트는 기본적으로
    deselect돼야 한다(pyproject.toml addopts의 기본 제외 집합과 동일)."""
    kept_item = _FakeItem()
    excluded_item = _FakeItem(markers=("redis",))
    items = [kept_item, excluded_item]
    config = _FakeConfig(markexpr="")

    pytest_collection_modifyitems(config, items)

    assert items == [kept_item]
    assert config.hook.deselected_calls == [[excluded_item]]


def test_pytest_collection_modifyitems_keeps_item_when_marker_named_in_markexpr():
    """`-m redis`처럼 그 마커를 명시적으로 요청하면(task-6845) 기본 제외를
    재적용하지 않고 그대로 유지해야 한다 — 그렇지 않으면 명시적 요청조차
    조용히 0건으로 걸러진다."""
    redis_item = _FakeItem(markers=("redis",))
    items = [redis_item]
    config = _FakeConfig(markexpr="redis")

    pytest_collection_modifyitems(config, items)

    assert items == [redis_item]
    assert config.hook.deselected_calls == []


def test_pytest_collection_modifyitems_extends_perf_marker_timeout():
    """`perf` 마커 테스트는 전역 120s 타임아웃 대신 600s를 받아야 한다
    (task-645) — 아니면 100회 실 DB 왕복 같은 의도된 느림이 행(hang)으로
    오인돼 강제 종료된다."""
    perf_item = _FakeItem(markers=("perf",))
    items = [perf_item]
    config = _FakeConfig(markexpr="")

    pytest_collection_modifyitems(config, items)

    timeout_markers = [m for m in perf_item.iter_markers() if m.name == "timeout"]
    assert len(timeout_markers) == 1


async def test_retry_too_many_connections_propagates_unrelated_exceptions():
    """`asyncpg.exceptions.TooManyConnectionsError`가 아닌 예외(예: 설정 오류로
    인한 `ValueError`)는 재시도 대상이 아니다 — 재시도로 감싸면 진짜 버그를
    은폐하고 6번의 지수 백오프만큼 실패 신호를 지연시킨다."""
    call_count = 0

    async def _factory():
        nonlocal call_count
        call_count += 1
        raise ValueError("not a connection-pool error")

    with pytest.raises(ValueError):
        await retry_too_many_connections(_factory, attempts=6, base_delay=0.0)

    assert call_count == 1


async def test_retry_too_many_connections_reraises_after_exhausting_attempts(monkeypatch):
    """실패주입: 팩토리가 매번 `TooManyConnectionsError`를 던지도록 강제하면
    (다른 worker 프로세스가 계속 max_connections를 다 쓰는 상황을 흉내낸다)
    `attempts`회 재시도 후에도 성공하지 못하면 마지막 예외를 그대로 전파해야
    한다 — 조용히 삼키면 커넥션 누수/설정 오류를 통과시킨다."""
    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    call_count = 0

    async def _factory():
        nonlocal call_count
        call_count += 1
        raise asyncpg.exceptions.TooManyConnectionsError("sorry, too many clients already")

    with pytest.raises(asyncpg.exceptions.TooManyConnectionsError):
        await retry_too_many_connections(_factory, attempts=3, base_delay=0.5)

    assert call_count == 3
    assert sleep_calls == [0.5 * 2**0, 0.5 * 2**1, 0.5 * 2**2]


async def test_retry_too_many_connections_recovers_after_transient_errors():
    """일시적으로만 거절되다가 회복되는 정상 경로 — 마지막 시도에서 성공하면
    그 결과를 그대로 돌려주고 더 이상 재시도하지 않아야 한다. `base_delay=0.0`이라
    실제 `asyncio.sleep(0.0)` 백오프도 테스트 시간에 영향을 주지 않는다."""
    call_count = 0

    async def _factory():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise asyncpg.exceptions.TooManyConnectionsError("sorry, too many clients already")
        return "connected"

    result = await retry_too_many_connections(_factory, attempts=6, base_delay=0.0)

    assert result == "connected"
    assert call_count == 3


def test_test_dotenv_values_does_not_leak_test_secrets_for_other_paths(tmp_path):
    """`_test_dotenv_values`는 루트 `.env` 경로일 때만 테스트 전용 값을
    돌려줘야 한다 — 다른 `.env` 경로(예: 어떤 서브모듈의 자체 `.env`)까지
    가로채면 그 파일이 실제로 가진 값 대신 테스트 시크릿이 조용히 섞여
    들어가 통합테스트가 아닌 코드 경로를 오염시킨다."""
    other_env = tmp_path / ".env"
    other_env.write_text("SOME_OTHER_KEY=real-value\n", encoding="utf-8")

    result = _test_dotenv_values(other_env)

    assert result == {"SOME_OTHER_KEY": "real-value"}
    assert "DATABASE_URL" not in result


def test_test_dotenv_values_returns_test_env_for_root_env_path():
    """루트 `.env` 경로를 요청하면(레거시 모듈이 `dotenv_values(ROOT/.env)`를
    직접 호출하는 경우) 실제 파일을 읽지 않고 결정적 테스트 값만 돌려줘야
    한다 — 이 파일 상단 docstring이 명시하는 불변식."""
    result = _test_dotenv_values(_ROOT_ENV_PATH)

    assert result == _TEST_ENV
    assert result is not _TEST_ENV  # 호출자가 반환값을 mutate해도 원본은 보존


def test_perf_budget_assert_within_fails_closed_over_budget():
    """예산을 초과하면 조용히 통과시키지 않고 `AssertionError`로 fail-closed
    해야 한다 — perf 회귀를 놓치지 않는 것이 이 헬퍼의 존재 이유다."""
    budget = PerfBudget()

    def _slow() -> None:
        # `PerfBudget`은 `time.process_time()`(CPU 시간) 기준으로 판정하므로
        # I/O 대기(`time.sleep`)가 아니라 실제로 CPU를 태우는 바쁜 루프여야 한다.
        deadline = time.process_time() + 0.02
        while time.process_time() < deadline:
            pass

    with pytest.raises(AssertionError):
        budget.assert_within(_slow, budget_ms=0.001, n=1, warmup=0)
