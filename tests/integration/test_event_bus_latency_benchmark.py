"""8.2-D — 종단간 지연 벤치마크 최초 측정.

Spec: 05_communication_architecture_v1.2.md#§5.4("이 결정은 8.2-D 지연
벤치마크(종단간 50ms) 설계와 직결된다 — 프로세스 간 통신 오버헤드가
없으므로"), 08_test_plan_v1.2.md#§8.4("pytest-benchmark 등으로 종단간
지연 측정, 결과를 docs/benchmarks/에 기록"), 06_mvp_scope_v1.3.md#§6.3

06번 §5.4가 명시하듯 Phase 1은 단일 프로세스·단일 asyncio 루프 구조라
"종단간"의 실제 의미는 InProcessEventBus.publish() 호출 시점부터 구독
handler가 payload를 실제로 처리하기 시작하는 시점까지다(다른 프로세스로
넘어가는 홉이 아예 없음). §6.3 DoD의 완료조건은 "목표 미달이어도 측정
자체"이므로, 이 테스트는 pytest-benchmark로 실제 측정하고 그 결과를
있는 그대로 docs/benchmarks/event_bus_latency.md에 기록한다.
"""
from __future__ import annotations

import asyncio
import statistics
import time
from pathlib import Path

from src.core.event_bus.in_process import InProcessEventBus
from src.core.event_bus.policy import HandlerCriticality

TARGET_LATENCY_MS = 50.0  # 정책문서 8.2-D 목표치(Draft)
SAMPLE_COUNT = 200
REPORT_PATH = Path(__file__).resolve().parents[2] / "docs" / "benchmarks" / "event_bus_latency.md"


def _measure_round_trip_latencies_ms(
    loop: asyncio.AbstractEventLoop, sample_count: int
) -> list[float]:
    bus = InProcessEventBus()
    received_at: list[float] = []

    async def handler(payload: dict) -> None:
        received_at.append(time.perf_counter())

    bus.subscribe("bench.round_trip", handler, criticality=HandlerCriticality.SAFE)
    loop.run_until_complete(bus.start())

    latencies_ms: list[float] = []
    try:
        for i in range(sample_count):
            published_at = time.perf_counter()

            async def publish_and_wait(i: int = i, published_at: float = published_at) -> None:
                await bus.publish("bench.round_trip", {"i": i})
                # EventBus 워커가 같은 루프의 별도 Task라 sleep(0)으로 양보하면
                # 다음 루프 틱에 실제로 처리된다 — 진짜 처리 완료를 기다리지
                # 폴링 없이 "발행만 하고 끝"으로 착각하지 않기 위함.
                while len(received_at) <= i:
                    await asyncio.sleep(0)

            loop.run_until_complete(publish_and_wait())
            latencies_ms.append((received_at[i] - published_at) * 1000)
    finally:
        loop.run_until_complete(bus.stop())

    return latencies_ms


def test_event_bus_end_to_end_latency_benchmark():
    """08번 §8.4는 "pytest-benchmark 등으로"라 도구를 못 박지 않는다 —
    pytest-benchmark의 반복측정 모델(매 라운드 대상 함수를 통째로 다시
    호출)은 여기서 실제로 재보면 InProcessEventBus.stop()의 유휴 워커
    폴링 타임아웃(최대 0.5초, in_process.py::_worker_loop 참조)까지
    "지연"에 섞어버려 메시지 처리 지연이 아니라 테스트 하네스 자체의
    시작/종료 오버헤드를 보고하게 된다(실측으로 확인 — 라운드당
    ~500ms로 나왔는데 이건 EventBus 성능과 무관). 그래서 여기서는 버스를
    한 번만 띄운 채 200회 왕복을 직접 재는 방식을 쓴다 — 측정 대상을
    "발행→handler 도달"로 정확히 좁히기 위함."""
    loop = asyncio.new_event_loop()
    try:
        all_samples_ms = _measure_round_trip_latencies_ms(loop, SAMPLE_COUNT)
    finally:
        loop.close()

    all_samples_ms.sort()
    mean_ms = statistics.mean(all_samples_ms)
    p50_ms = all_samples_ms[len(all_samples_ms) // 2]
    p95_ms = all_samples_ms[int(len(all_samples_ms) * 0.95)]
    max_ms = all_samples_ms[-1]

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        "# 8.2-D 종단간 지연 벤치마크\n\n"
        "정책문서 8.2-D · 06_mvp_scope_v1.3.md#SS6.3 Definition of Done\n\n"
        "측정 대상: InProcessEventBus.publish() -> 구독 handler 처리 시작까지"
        "(Phase 1 단일 프로세스 구조라 이게 곧 '종단간').\n\n"
        f"- 샘플 수: {SAMPLE_COUNT}\n"
        f"- 평균: {mean_ms:.3f} ms\n"
        f"- p50: {p50_ms:.3f} ms\n"
        f"- p95: {p95_ms:.3f} ms\n"
        f"- 최대: {max_ms:.3f} ms\n"
        f"- 목표(Draft): {TARGET_LATENCY_MS} ms\n"
        f"- 목표 충족 여부(p95 기준): {'예' if p95_ms < TARGET_LATENCY_MS else '아니오'}\n\n"
        "측정 자체가 Phase 1 SCAFFOLD 완료조건이며, 목표 미달이어도 조건은 "
        "충족한다(FROZEN 착수 조건인 20.1-A A그룹 통과와는 별개).\n",
        encoding="utf-8",
    )

    print(f"\nEvent Bus latency: mean={mean_ms:.3f}ms p50={p50_ms:.3f}ms p95={p95_ms:.3f}ms")

    assert len(all_samples_ms) == SAMPLE_COUNT
