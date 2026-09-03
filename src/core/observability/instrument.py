"""계측 데코레이터/컨텍스트 매니저 — 커맨드 실행 시간·결과를 로그+메트릭으로 남긴다.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A), §9 PLT-05.

이름 규칙은 `metric_names.py`처럼 상수 하나로 고정하지 않는다 — 호출부(커맨드)마다
`component`가 다르므로, 이 모듈은 그 이름을 받아 `<component>.duration_seconds`/
`<component>.count_total`을 조립하는 얇은 계측기일 뿐이다(계측 지점이 늘어날 때마다
`metric_names.py`에 상수를 추가하는 건 이 리프의 범위 밖 — PLT-10 이후 실제 계측
지점이 생기면 그 값들을 상수로 승격한다).
"""
from __future__ import annotations

import functools
import logging
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from typing import Any, TypeVar

from src.core.observability.metrics import metrics

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Awaitable[Any]])


def observe_command(component: str, event: str) -> Callable[[F], F]:
    """async 커맨드 함수를 감싸 성공/실패 각각 `duration_ms` 로그 한 줄과
    `<component>.duration_seconds` 히스토그램, `<component>.count_total{outcome}`
    카운터를 남긴다.

    실패 시 `event`는 `<event>_failed`로 바뀌고 `outcome` 라벨은 예외 클래스명이
    된다 — 호출자가 로그만으로 "무엇이 실패했는지" 구분할 수 있게 한다(108 §5-4).
    성공 시 `outcome="ok"`.
    """

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = time.monotonic()
            outcome = "ok"
            event_name = event
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                outcome = type(exc).__name__
                event_name = f"{event}_failed"
                raise
            finally:
                duration_ms = round((time.monotonic() - start) * 1000)
                logger.info(
                    event_name,
                    extra={"event": event_name, "duration_ms": duration_ms},
                )
                metrics().observe(f"{component}.duration_seconds", duration_ms / 1000)
                metrics().counter(f"{component}.count_total", {"outcome": outcome})

        return wrapper  # type: ignore[return-value]

    return decorator


@contextmanager
def timed(metric_name: str, labels: dict[str, str] | None = None) -> Iterator[None]:
    """블록 실행 시간을 `metric_name` 히스토그램 하나로만 기록한다(로그 없음) —
    호출부가 이미 자체 로그를 남기고 있어 `observe_command`의 로그 한 줄이
    중복될 때 쓰는 더 가벼운 버전."""
    start = time.monotonic()
    try:
        yield
    finally:
        metrics().observe(metric_name, time.monotonic() - start, labels)
