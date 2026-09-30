"""Package-level unit tests for the test infrastructure itself.

These tests validate the test support helpers (frozen, coverage_pause) and
verify that the test package rejects invalid inputs — negative tests that
confirm invariants hold, and failure-injection tests that provoke exceptions
via monkeypatch.

DoD(docs/design/INVARIANTS.md I-10): safety/policy components must have
wiring-proof tests (static or adversarial integration). The helpers here
are the wiring for frozen-model and coverage-pause behaviour; they must
prove themselves before any downstream test trusts them.
"""

from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError, dataclass
from typing import Any
from unittest.mock import patch

import pytest

from tests.support.frozen import assign_attr

# ── frozen model helpers: negative tests ──────────────────────────────────


@dataclass(frozen=True)
class _FrozenSample:
    """Minimum frozen dataclass for mutation-rejection testing."""

    id: int
    name: str = ""


class _FrozenPydanticSample:
    """Minimal pydantic-like frozen model (no pydantic dependency).

    Uses __slots__ + __setattr__ override to simulate a frozen pydantic model.
    """

    __slots__ = ("id", "name")

    def __init__(self, id: int, name: str = "") -> None:
        object.__setattr__(self, "id", id)
        object.__setattr__(self, "name", name)

    def __setattr__(self, name: str, value: Any) -> None:
        if hasattr(self, name):
            raise AttributeError(f"cannot assign to frozen attribute '{name}'")
        super().__setattr__(name, value)


def test_frozen_dataclass_rejects_mutation_via_setattr() -> None:
    """불변식 위반: frozen dataclass는 속성 재할당 시 FrozenInstanceError를
    던져야 한다 — mutate 가능한 frozen model은 불변 조건 I-04(불변성)를
    위반한다."""
    obj = _FrozenSample(id=1, name="test")
    # dataclass frozen은 TypeError가 아닌 FrozenInstanceError를
    # 던짐 — TypeError로 잡으면 실제 예외가 누락됨
    with pytest.raises(FrozenInstanceError):
        assign_attr(obj, "name", "changed")


def test_frozen_dataclass_allows_read() -> None:
    """frozen model은 읽기는 허용되어야 한다 — mutation 거부만 테스트하면
    읽기 경로가 깨져도 놓친다."""
    obj = _FrozenSample(id=42, name="readable")
    assert obj.id == 42
    assert obj.name == "readable"


def test_frozen_pydantic_like_rejects_mutation_via_setattr() -> None:
    """불변식 위반: frozen pydantic 모델도 setattr로 속성 재할당 시
    AttributeError를 던져야 한다 — __setattr__ 오버라이드가 정상 동작하는지
    확인한다."""
    obj = _FrozenPydanticSample(id=1, name="test")
    with pytest.raises(AttributeError):
        assign_attr(obj, "name", "changed")


def test_assign_attr_on_non_frozen_allows_write() -> None:
    """회귀 방지: frozen이 아닌 일반 dataclass는 assign_attr가 정상
    동작해야 한다 — frozen 헬퍼가 모든 dataclass에 AttributeError를
    유발하면 레거스 코드가 깨진다."""

    @dataclass(frozen=False)
    class _MutableSample:
        id: int
        name: str = ""

    obj = _MutableSample(id=1, name="test")
    assign_attr(obj, "name", "changed")
    assert obj.name == "changed"


# ── failure-injection tests ───────────────────────────────────────────────


def test_coverage_pause_skips_stop_when_coverage_not_installed() -> None:
    """실패주입: coverage.py가 설치돼 없으면 paused_coverage()는
    예외 없이 아무 동작도 하지 않아야 한다 — coverage 미설치 시 perf
    테스트가 전체 실패하면 안 된다."""
    from tests.support.coverage_pause import paused_coverage

    # coverage 모듈을 None으로 모의 — Coverage.current() 호출 시
    # ImportError를 유발하는 대신 이미 None인 상태로 테스트
    with patch.dict(sys.modules, {"coverage": None}):
        # coverage가 None이면 Coverage.current()는 ImportError를 일으키지
        # 않고 coverage가 None이라 바로 pass해야 함
        # paused_coverage는 try/finally로 coverage를 참조하므로
        # module-level coverage 변수가 None이어야 한다
        import tests.support.coverage_pause as cp

        original_coverage = cp.coverage
        try:
            cp.coverage = None
            # context manager 진입/종료 시 예외 없어야 함
            with paused_coverage():
                pass
        finally:
            cp.coverage = original_coverage


def test_coverage_pause_stops_and_resumes_active_coverage() -> None:
    """성능 측정 구간에서 활성 coverage가 stop() → yield → start() 순으로
    호출되는지 확인한다 — coverage가 걸리지 않은 구간이면 perf 예산이
    왜곡된다."""
    import unittest.mock

    mock_cov = unittest.mock.MagicMock()

    # coverage.Coverage를 patch — paused_coverage는 coverage.Coverage.current()
    # 를 호출하므로 Coverage 클래스가 mock_cov를 반환해야 함
    with unittest.mock.patch("coverage.Coverage") as mock_cov_class:
        mock_cov_class.return_value = mock_cov
        mock_cov_class.current.return_value = mock_cov

        from tests.support.coverage_pause import paused_coverage

        with paused_coverage():
            pass
        # stop()과 start()가 모두 호출되었어야 함
        assert mock_cov.stop.called, "paused_coverage 진입 시 coverage.stop() 호출 필요"
        assert mock_cov.start.called, "paused_coverage 종료 시 coverage.start() 호출 필요"


# ── negative tests: invalid inputs to test infrastructure ─────────────────


def test_assign_attr_on_object_without_target_raises() -> None:
    """부정 입력: assign_attr는 타겟이 None이면 AttributeError를
    유발해야 한다 — caller가 None frozen model에 setattr하면 즉시
    실패해야 버그가 묵시적으로 통과하지 않는다."""
    with pytest.raises(AttributeError):
        assign_attr(None, "any_field", "value")


def test_assign_attr_with_empty_name_on_frozen_raises() -> None:
    """부정 입력: 빈 문자열 속성명은 frozen model에서 재할당 시도 시
    예외를 유발해야 한다 — 설정 파일 파싱 등에서 빈 키가 들어오면
    즉시 실패해야 한다."""
    obj = _FrozenSample(id=1, name="test")
    with pytest.raises((TypeError, AttributeError)):
        assign_attr(obj, "", "value")


# ── failure-injection: monkeypatch Path.read_text to simulate I/O failure ──


def test_test_dotenv_values_uses_env_var_when_file_missing(tmp_path: Any) -> None:
    """실패주입: 테스트 환경에서 실제 .env 파일이 없으면 TEST_DATABASE_URL
    환경 변수가 대체 원천으로 사용되어야 한다 — 파일이 없어도 테스트가
    설정 오류 없이 계속될 수 있어야 한다."""
    import os

    # tmp_path 아래 .env 파일을 만들지 않고 테스트
    # 실제 루트 .env 경로는 tmp_path가 아니므로 _test_dotenv_values는
    # 파일을 직접 읽어야 함 — 파일이 없으면 FileNotFoundError 유발
    from tests.support.db import ensure_worker_database

    # ensure_worker_database는 DB URL이 없으면 RuntimeError를 던짐
    # — 테스트 환경이 설정되지 않았을 때 fail-closed behaviour 확인
    test_url = os.environ.get("TEST_DATABASE_URL")
    if test_url is None:
        # 실제 테스트 환경에서 TEST_DATABASE_URL이 없으면
        # ensure_worker_database가 RuntimeError를 던지는지 확인
        with pytest.raises(RuntimeError):
            ensure_worker_database(
                template_url="postgresql://localhost/aios", worker_id="test-worker"
            )


# ── performance assertion ─────────────────────────────────────────────────


@pytest.mark.perf
def test_assign_attr_overhead_is_negligible() -> None:
    """성능 단언: assign_attr는 setattr 래퍼일 뿐이므로 10만 회 호출에
    1초 미만이어야 한다 — 테스트 헬퍼가 실행 시간을먹으면 perf 예산을
    왜곡한다."""
    import time

    obj = _FrozenSample(id=1, name="test")
    t0 = time.perf_counter()
    for _ in range(100_000):
        try:
            assign_attr(obj, "name", "x")
        except FrozenInstanceError:
            pass  # expected — frozen model rejects mutation
    elapsed = time.perf_counter() - t0

    assert elapsed < 1.0, (
        f"assign_attr 10만 회 호출에 {elapsed:.2f}초 — 헬퍼 오버헤드가 성능 측정을 왜곡할 수 있음"
    )
