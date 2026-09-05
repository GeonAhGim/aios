"""지연·응답 유실 주입 모델(L4 명세 §2-F, §6 F17).

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §2-F, §9 L4-22.

순수 도메인 — 실제 대기는 주입된 `sleeper`(예: `asyncio.sleep`)만 호출하고
전역 시계·전역 난수는 쓰지 않는다. `sample()`은 동기·결정론(rng 고정 시).

DROP 의미: 요청은 나갔고 응답만 유실 — 지연은 그대로 발생한 뒤 `DROP`을
돌려준다. 이를 `SentUnknownError`로 승격해 주문을 UNKNOWN으로 보내는 것은
`simulator_adapter`(L4-23)의 책임이다(§6 F17 = F3 경로). `SentUnknownError`
클래스 자체는 이 시점 저장소에 아직 없다(L4-11/23 몫).

지연 분포: (0,0)·(0.5,p50)·(0.99,p99)·(1.0,2·p99)를 잇는 구간선형 분위함수.
실측 분포가 아닌 합성 모델이다(미검증).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from src.exchanges.paper.fill_model import RandomSource

Sleeper = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class LatencyOutcome:
    kind: Literal["ACK", "DROP"]
    delay_ms: float


@dataclass(frozen=True)
class LatencyModel:
    ack_ms_p50: float
    ack_ms_p99: float
    drop_response_prob: float

    def __post_init__(self) -> None:
        if self.ack_ms_p50 < 0 or self.ack_ms_p99 < self.ack_ms_p50:
            raise ValueError("0 ≤ ack_ms_p50 ≤ ack_ms_p99 이어야 합니다.")
        if not 0.0 <= self.drop_response_prob <= 1.0:
            raise ValueError("drop_response_prob는 [0, 1] 범위여야 합니다.")

    def sample(self, rng: RandomSource) -> LatencyOutcome:
        """rng 두 번 소비: 1) 지연 분위 u, 2) 유실 판정(prob=0이면 미소비)."""
        delay = self._delay_ms(rng.random())
        dropped = self.drop_response_prob > 0 and rng.random() < self.drop_response_prob
        return LatencyOutcome(kind="DROP" if dropped else "ACK", delay_ms=delay)

    async def apply(self, rng: RandomSource, *, sleeper: Sleeper) -> LatencyOutcome:
        """`sleeper`는 필수 주입(전역 `asyncio.sleep` 기본값 없음 — 순수성 유지)."""
        outcome = self.sample(rng)
        await sleeper(outcome.delay_ms / 1000.0)
        return outcome

    def _delay_ms(self, u: float) -> float:
        p50, p99 = self.ack_ms_p50, self.ack_ms_p99
        if u < 0.5:
            return p50 * (u / 0.5)
        if u < 0.99:
            return p50 + (p99 - p50) * ((u - 0.5) / 0.49)
        return p99 + p99 * ((u - 0.99) / 0.01)
