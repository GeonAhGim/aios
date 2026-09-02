"""L4-11 — 재시도 정책·백오프·타임아웃 예산.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md#§2-D, §9 L4-11

시간/난수는 전부 주입받는다(`rng: Callable[[], float]`) — `random.random`을
직접 호출하지 않아 테스트가 결정론적이다(task-423 d3227c9 패턴 재사용).

주문 제출 경로는 `RetryPolicy(max_attempts=1)`을 써야 한다(§5.4) — 전송 후
실패는 outbox 재처리가 책임지며, 이 모듈이 자체 재시도하면 이중 제출
위험이 생긴다.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

JitterMode = Literal["full"]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 4
    base: float = 0.25
    cap: float = 8.0
    jitter: JitterMode = "full"


@dataclass(frozen=True)
class TimeoutBudget:
    connect: float = 2.0
    read: float = 5.0
    total: float = 8.0


def backoff_delay(
    policy: RetryPolicy,
    attempt: int,
    retry_after: float | None,
    rng: Callable[[], float],
) -> float:
    """다음 재시도까지의 지연(초)을 계산한다.

    `retry_after`(서버가 보낸 Retry-After 값)가 주어지면 그 값을 그대로
    따른다 — 서버가 명시한 지연을 우리 쪽 backoff 공식으로 덮어쓰지 않는다.
    없으면 full-jitter 지수 백오프: `rng()`가 반환하는 [0, 1) 값으로
    `[0, min(cap, base * 2**attempt))` 범위를 균일 샘플링한다.

    `attempt`는 1부터 시작하는 재시도 횟수(1 = 첫 재시도)로 해석한다.
    """
    if retry_after is not None:
        return max(0.0, retry_after)
    if attempt < 1:
        raise ValueError(f"attempt는 1 이상이어야 함: {attempt}")
    ceiling: float = min(policy.cap, policy.base * (2 ** (attempt - 1)))
    return ceiling * rng()
