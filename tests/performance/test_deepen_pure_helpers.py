"""tests/performance 패키지 순수 헬퍼(`percentile`, `_query_logger`) 경계 테스트 —
task-9847 DEEPEN(원 리프 task-6704, 고아 산출물 회수 5828 qa-2).

task-8658 선례: pytest 기본 `python_files`(=`test_*.py`)는 `__init__.py`를 test
모듈로 수집하지 않는다(`pytest tests/performance/ --collect-only`로 실측 확인
가능) — 여기 테스트를 `__init__.py`가 아니라 이 파일에 둔다. DB 없이 도는
순수/의존성-주입 테스트로 `test_pre_trade_latency.py`(실 Postgres)와 역할을
분리한다.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import cast

import pytest

from tests.performance.pre_trade_latency_support import _query_logger, percentile

# --- negative tests (불변식 위반 입력 거부) -----------------------------------


def test_percentile_rejects_empty_samples() -> None:
    """빈 표본에 대해 조용히 0.0 같은 값을 반환하면 호출자가 '측정 안 됨'과
    '지연시간 0ms'를 구분할 수 없다 — fail-closed로 명시적으로 실패해야 한다."""
    with pytest.raises(IndexError):
        percentile([], 99)


def test_percentile_rejects_non_numeric_samples() -> None:
    """표본에 숫자가 아닌 값이 섞이면(계측 코드 버그로 문자열이 흘러든 경우)
    잘못된 정렬 순서를 조용히 만들지 않고 즉시 거부해야 한다."""
    bad_samples = cast("list[float]", [1.0, "not-a-number", 2.0])
    with pytest.raises(TypeError):
        percentile(bad_samples, 99)


def test_percentile_rejects_nan_percentile() -> None:
    """`pct`가 NaN이면 `round()`가 정수로 변환할 수 없다 — 잘못된 백분위 인자를
    임의의 인덱스로 눙치지 않고 즉시 실패해야 한다."""
    with pytest.raises(ValueError):
        percentile([1.0, 2.0, 3.0], float("nan"))


# --- 실패주입 (의존성 예외 전파) ----------------------------------------------


class _RecordingSink:
    """`list[str]`과 동일한 `append` 계약을 흉내 내는 대역 — 내장 `list`는
    인스턴스별로 메서드를 monkeypatch할 수 없어(read-only 속성) 실패주입용
    별도 클래스가 필요하다."""

    def __init__(self) -> None:
        self.items: list[str] = []

    def append(self, item: str) -> None:
        self.items.append(item)


def test_query_logger_propagates_append_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """`_query_logger`가 만든 콜백은 asyncpg 쿼리 로거로 등록된다 — 내부 저장소
    (`queries.append`)가 실패하면 그 예외를 삼켜 계측이 누락된 채 perf 왕복
    수치를 조용히 낮게 보고하면 안 된다(fail-closed)."""
    sink = cast("list[str]", _RecordingSink())
    log = _query_logger(sink)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected queries.append failure")

    monkeypatch.setattr(sink, "append", _boom)

    with pytest.raises(RuntimeError, match="injected queries.append failure"):
        log(SimpleNamespace(query="SELECT 1"))


# --- 성능 단언 -----------------------------------------------------------------


@pytest.mark.perf
def test_percentile_throughput_budget() -> None:
    """D2 성능 단언 — 순수 CPU 정렬/인덱싱 연산이므로 10,000개 표본에 대한
    200회 p99 계산이 1초 예산 안에 들어야 한다(회귀 시 여기서 잡힌다)."""
    samples = [float(i) for i in range(10_000)]

    started = time.perf_counter()
    for _ in range(200):
        percentile(samples, 99)
    elapsed = time.perf_counter() - started

    assert elapsed < 1.0, f"percentile 200회 처리 {elapsed:.4f}s가 1s 예산 초과"
