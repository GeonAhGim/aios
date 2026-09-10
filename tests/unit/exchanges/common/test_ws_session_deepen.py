"""L4-19 DEEPEN(task-2760, DEPTH 감사 task-2722 `docs/audit/DEPTH_L4_BR.md`) —
원 리프(task-1551, commit `1864403`, `test_ws_session.py`)가 D3 미달(실측 D1)로
판정된 두 결손을 보강한다:

1. 수치 성능/지연 단언 + gate-red 회귀 테스트(D2) — 원 테스트는 하트비트·ack·
   seq 갭·resync 정확성만 검증했고 처리량/지연은 전혀 재지 않았다. 여기서는
   대량 프레임(5,000건) 처리 시간과 프레임당 디스패치 지연 p95를 **단언**한다
   (print 아님) — 핫패스에 실수로 블로킹 호출이 들어가면 이 테스트가 적색이
   된다.
2. 다중 인스턴스/재생(replay) 적대적 증명(D3) — 운영에서는 여러 거래소/채널의
   `WsSession`이 같은 프로세스에서 동시에 돈다(상태·메트릭·장애 격리가 필요).
   또한 중계가 오동작하거나 공격자가 과거 프레임을 재생해도 `on_resync()`이
   진짜 전진 갭에만 반응해야 한다(스톰이 REST 레이트리밋을 태우면 §5 WS 내구성
   규칙 위반).

원 하네스(`_FakeConnection`/`_Ctx`/`_connect_sequence`/`_ack`/`_seq`/`_no_sleep`/
`_never`/`_closed`/`SUB`/`_Stop`/`_Harness`)는 재사용하고 재정의하지 않는다
(task-2804 OMS DEEPEN과 동일한 재노출 패턴).
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from src.core.observability.metrics_registry import MetricsRegistry
from src.exchanges.common.ws_session import WsAckError, WsSession
from tests.unit.exchanges.common.test_ws_session import (
    SUB,
    _ack,
    _closed,
    _connect_sequence,
    _FakeConnection,
    _Harness,
    _never,
    _no_sleep,
    _seq,
    _Stop,
)

# ---------- 수치 성능/지연 게이트(D2) ----------

_THROUGHPUT_MESSAGE_COUNT = 5000
_THROUGHPUT_BUDGET_SECONDS = 2.0  # 인메모리 가짜 전송만 쓰므로 넉넉한 절대 예산
_THROUGHPUT_MIN_MSGS_PER_SEC = 2000.0

_LATENCY_SAMPLE_COUNT = 500
_LATENCY_P95_BUDGET_MS = 20.0  # CI 튐 흡수 여유 — 핫패스 회귀(sleep/블로킹 IO 오삽입) 탐지용


async def test_pump_processes_large_batch_within_latency_budget():
    """수치 성능 게이트(D2) — 대량 프레임 처리 총 시간과 처리량을 단언한다.
    DEPTH 감사 결손: "no numeric performance/latency assertion"."""
    h = _Harness()
    frames = [json.dumps({"seq": s}) for s in range(1, _THROUGHPUT_MESSAGE_COUNT + 1)]
    conn = _FakeConnection(frames, raise_after=_closed())
    connect_fn, calls = _connect_sequence([conn])

    started = time.perf_counter()
    with pytest.raises(_Stop):
        await h.session(connect_fn).run([SUB], h.handler)
    elapsed_seconds = time.perf_counter() - started

    assert len(h.received) == _THROUGHPUT_MESSAGE_COUNT
    throughput = _THROUGHPUT_MESSAGE_COUNT / elapsed_seconds
    print(
        f"\nws_session pump throughput: {throughput:.0f} msg/s "
        f"({_THROUGHPUT_MESSAGE_COUNT}건 / {elapsed_seconds * 1000:.1f}ms)"
    )
    assert elapsed_seconds <= _THROUGHPUT_BUDGET_SECONDS, (
        f"{_THROUGHPUT_MESSAGE_COUNT}건 처리에 {elapsed_seconds:.3f}s — "
        f"예산({_THROUGHPUT_BUDGET_SECONDS}s) 초과: 핫패스 회귀 의심"
    )
    assert throughput >= _THROUGHPUT_MIN_MSGS_PER_SEC, (
        f"처리량({throughput:.0f} msg/s)이 하한({_THROUGHPUT_MIN_MSGS_PER_SEC} msg/s) 미달"
    )


async def test_message_dispatch_latency_p95_stays_within_ci_gate():
    """CI 레드라인 게이트(D2) — 프레임 1건당(디코드+ack검증+seq처리+핸들러
    호출) 지연의 p95가 예산을 넘으면 빌드가 적색이 된다. 원 테스트는 이
    수치를 전혀 재지 않았다(DEPTH 감사 결손)."""
    h = _Harness()
    frames = [json.dumps({"seq": s}) for s in range(1, _LATENCY_SAMPLE_COUNT + 1)]
    conn = _FakeConnection(frames, raise_after=_closed())
    connect_fn, _ = _connect_sequence([conn])
    timestamps: list[float] = []

    async def timing_handler(message: dict[str, object]) -> None:
        timestamps.append(time.perf_counter())

    with pytest.raises(_Stop):
        await h.session(connect_fn).run([SUB], timing_handler)

    assert len(timestamps) == _LATENCY_SAMPLE_COUNT
    deltas_ms = sorted(
        (timestamps[i] - timestamps[i - 1]) * 1000.0 for i in range(1, len(timestamps))
    )
    p95_ms = deltas_ms[int(len(deltas_ms) * 0.95)]
    print(f"\nws_session per-message dispatch latency p95={p95_ms:.3f}ms (n={len(deltas_ms)})")
    assert p95_ms <= _LATENCY_P95_BUDGET_MS, (
        f"프레임당 처리 지연 p95({p95_ms:.3f}ms)가 "
        f"CI 게이트({_LATENCY_P95_BUDGET_MS}ms)를 초과했습니다."
    )


# ---------- 다중 인스턴스 증명(D3) ----------


async def test_multiple_concurrent_instances_isolate_state_metrics_and_failure():
    """다중 인스턴스 증명(D3) — 서로 다른 채널의 `WsSession` 두 개가 같은
    프로세스·같은 레지스트리에서 동시에 돌 때(운영의 실제 사용 패턴: 여러
    거래소/채널 병렬 구독), 한쪽의 ack 실패가 다른 쪽 수신·처리량·메트릭에
    새지 않아야 한다."""
    registry = MetricsRegistry()
    received_a: list[dict[str, object]] = []
    received_b: list[dict[str, object]] = []

    async def handler_a(message: dict[str, object]) -> None:
        received_a.append(message)

    async def handler_b(message: dict[str, object]) -> None:
        received_b.append(message)

    conn_a = _FakeConnection(
        [json.dumps({"seq": 1}), json.dumps({"seq": 2}), json.dumps({"seq": 4})],
        raise_after=_closed(),
    )
    conn_b = _FakeConnection(['{"event":"error","code":"boom"}'])
    connect_a, calls_a = _connect_sequence([conn_a])
    connect_b, calls_b = _connect_sequence([conn_b])

    session_a = WsSession(
        "wss://venue-a",
        venue="venueA",
        channel="ticker",
        ack_validator=_ack,
        connect_fn=connect_a,
        seq_extractor=_seq,
        sleep_fn=_no_sleep,
        ping_sleep_fn=_never,
        registry=registry,
    )
    session_b = WsSession(
        "wss://venue-b",
        venue="venueB",
        channel="books",
        ack_validator=_ack,
        connect_fn=connect_b,
        seq_extractor=_seq,
        sleep_fn=_no_sleep,
        ping_sleep_fn=_never,
        registry=registry,
    )

    async def run_a() -> None:
        with pytest.raises(_Stop):
            await session_a.run([SUB], handler_a)

    async def run_b() -> None:
        with pytest.raises(WsAckError, match="boom"):
            await session_b.run([SUB], handler_b)

    await asyncio.gather(run_a(), run_b())

    assert [m["seq"] for m in received_a] == [1, 2, 4]
    assert received_b == []  # ack 실패 프레임은 핸들러로 전달되지 않는다
    assert calls_a["n"] == 2  # venueB의 실패와 무관하게 venueA는 정상 재연결까지 진행
    assert calls_b["n"] == 1  # WsAckError는 재연결 없이 즉시 표면화

    gap_samples = registry.counter(
        "aios.exchange.ws.sequence_gap.count_total", ("venue", "channel")
    ).samples()
    assert gap_samples == {("venueA", "ticker"): 1.0}  # venueB 라벨은 아예 없다(교차 오염 없음)


async def test_second_instance_failure_does_not_block_first_instance_progress():
    """다중 인스턴스 증명(D3) — 한 인스턴스가 즉시 실패해도(연결 자체가
    거부) 동시에 실행 중인 다른 인스턴스는 프레임을 계속 처리한다(하나의
    asyncio 태스크 예외가 다른 세션의 이벤트 루프 진행을 막지 않음을 증명)."""
    h = _Harness()
    received_other: list[dict[str, object]] = []

    async def other_handler(message: dict[str, object]) -> None:
        received_other.append(message)

    def failing_connect(url: str):
        # OSError가 아닌 예외 — run()은 OSError/ConnectionClosed만 재연결
        # 사유로 삼키므로, 이건 즉시 호출부로 나가야 한다(다른 인스턴스를
        # 막지 않고).
        raise RuntimeError("venueC misconfigured")

    other_conn = _FakeConnection(
        [json.dumps({"seq": n}) for n in range(1, 51)], raise_after=_closed()
    )
    other_connect, other_calls = _connect_sequence([other_conn])
    other_session = h.session(other_connect)

    failing_session = WsSession(
        "wss://venue-c",
        venue="venueC",
        channel="trades",
        ack_validator=_ack,
        connect_fn=failing_connect,
        sleep_fn=_no_sleep,
        ping_sleep_fn=_never,
        registry=h.registry,
    )

    async def run_failing() -> None:
        with pytest.raises(RuntimeError, match="venueC misconfigured"):
            await failing_session.run([SUB], other_handler)

    async def run_other() -> None:
        with pytest.raises(_Stop):
            await other_session.run([SUB], other_handler)

    await asyncio.gather(run_failing(), run_other())

    assert len(received_other) == 50
    assert other_calls["n"] == 2


# ---------- 재생(replay) 적대적 증명(D3) ----------


async def test_replayed_and_out_of_order_seq_do_not_trigger_spurious_resync():
    """재생 공격 적대적 증명(D3) — 오동작하는 중계나 공격자가 과거 seq를
    반복 재생해도(진짜 전진 갭은 없음) `on_resync()`이 스톰을 일으키지
    않아야 한다. resync 스톰은 REST 레이트리밋을 태워 서비스 저하로
    이어진다(§5 WS 내구성 규칙). 프레임 자체는 전부 핸들러로 전달된다 —
    중복 배제는 앱 계층(멱등키) 책임이지 세션 책임이 아니다."""
    h = _Harness()
    replay_storm = [1, 2, 3, 4, 5] + [3, 2, 1, 5, 4, 3, 2] * 20  # 진짜 갭 0개
    frames = [json.dumps({"seq": s}) for s in replay_storm]
    conn = _FakeConnection(frames, raise_after=_closed())
    connect_fn, _ = _connect_sequence([conn])

    with pytest.raises(_Stop):
        await h.session(connect_fn).run([SUB], h.handler)

    assert len(h.received) == len(replay_storm)
    assert "resync" not in h.events
    assert h.counter("aios.exchange.ws.sequence_gap.count_total") == 0


async def test_replay_noise_around_one_genuine_gap_resyncs_exactly_once():
    """재생 잡음 속 진짜 갭 1개만 정확히 잡아내는지 증명(D3) — 재생/중복
    프레임이 아무리 섞여도 진짜 전진 갭(3 누락)에 대한 resync는 정확히
    1회여야 한다(과다 resync도, 누락도 둘 다 결함)."""
    h = _Harness()
    sequence = [1, 2, 4] + [2, 1, 4, 3] * 15  # 3 누락(진짜 갭) 후 재생 잡음 반복
    frames = [json.dumps({"seq": s}) for s in sequence]
    conn = _FakeConnection(frames, raise_after=_closed())
    connect_fn, _ = _connect_sequence([conn])

    with pytest.raises(_Stop):
        await h.session(connect_fn).run([SUB], h.handler)

    assert h.events.count("resync") == 1
    assert h.counter("aios.exchange.ws.sequence_gap.count_total") == 1
