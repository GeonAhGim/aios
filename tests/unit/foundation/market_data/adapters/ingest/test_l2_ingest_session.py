"""RD-19 — `adapters/ingest/l2_ingest_session.py` 단위테스트(가짜 WS, 실 소켓 없음).

Spec: docs/design/ADR-2026-09-06-H-data-sourcing-self-build-and-contract-tiers.md
D3·D5, task-1766 DoD: "갭 주입 시 재동기화가 실제로 발생하고 손실 구간이
coverage_spans에 결손으로 기록됨(조용한 보간 금지)", "24시간 연속 수집에서
시퀀스 연속성 검증 통과"(가상 시계로 대체, 실시간 대기 없음).

`WsSession`은 재사용 대상이라 재테스트하지 않는다(`tests/unit/exchanges/
common/test_ws_session.py`가 이미 하트비트·ack·백오프를 검증) — 여기서는
`L2IngestSession`이 그 훅(`on_resync`/`on_distrust`)을 받아 커버리지
span을 정확히 여닫는지만 검증한다.
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed

from src.foundation.market_data.adapters.ingest.l2_ingest_session import L2IngestSession
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.l2_orderbook import L2Diff, L2Snapshot
from src.foundation.market_data.ports.coverage_repository import CoverageRepository, CoverageSpan


class _Stop(Exception):
    pass


class _FakeConnection:
    def __init__(self, messages: list[str], *, raise_after: BaseException | None = None) -> None:
        self._messages = messages
        self._raise_after = raise_after
        self.sent: list[str] = []

    async def send(self, message: str) -> None:
        self.sent.append(message)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for message in self._messages:
            yield message
        if self._raise_after is not None:
            raise self._raise_after


class _Ctx:
    def __init__(self, connection: _FakeConnection) -> None:
        self._connection = connection

    async def __aenter__(self):
        return self._connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


def _connect_sequence(connections: list[_FakeConnection]):
    calls = {"n": 0}

    def connect_fn(url: str):
        calls["n"] += 1
        if calls["n"] <= len(connections):
            return _Ctx(connections[calls["n"] - 1])
        raise _Stop

    return connect_fn, calls


async def _no_sleep(_: float) -> None:
    return None


async def _never(_: float) -> None:
    await asyncio.Event().wait()


def _closed() -> ConnectionClosed:
    return ConnectionClosed(None, None)


class _TickingClock:
    """호출마다 1초씩 전진하는 결정론적 가상 시계 — 실시간 대기가 전혀
    없어도 서로 다른 시각을 만들어낸다."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        current = self._now
        self._now = self._now + timedelta(seconds=1)
        return current


class _FakeCoverageRepo(CoverageRepository):
    def __init__(self) -> None:
        self.spans: list[CoverageSpan] = []

    async def upsert_span(self, conn: object, span: CoverageSpan) -> CoverageSpan:
        self.spans.append(span)
        return span

    async def list_spans(
        self, conn: object, instrument_id: str, timeframe: Timeframe
    ) -> list[CoverageSpan]:
        return [
            s
            for s in self.spans
            if s.instrument_id == instrument_id and s.timeframe == timeframe
        ]


class _NullCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeConn:
    def transaction(self) -> _NullCtx:
        return _NullCtx()


class _AcquireCtx:
    async def __aenter__(self) -> _FakeConn:
        return _FakeConn()

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakePool:
    def acquire(self) -> _AcquireCtx:
        return _AcquireCtx()


class _FakeAdapter:
    """seq 필드를 그대로 시퀀스로 쓰는 최소 `VenueL2Adapter` — 갭 주입은
    테스트가 프레임 시퀀스에 직접 구멍을 낸다."""

    venue = Venue.BINANCE

    def __init__(self) -> None:
        self.snapshot_calls = 0

    def ws_url(self, instrument_symbol: str) -> str:
        return "wss://fake"

    def subscription_messages(self, instrument_symbol: str) -> list[dict[str, Any]]:
        return [{"op": "subscribe"}]

    def ack_validator(self, message: dict[str, Any]):
        from src.exchanges.common.ws_session import NOT_ACK

        return NOT_ACK

    def seq_extractor(self, message: dict[str, Any]) -> int | None:
        value = message.get("seq")
        return int(value) if value is not None else None

    def parse_event(self, message: dict[str, Any]) -> L2Diff | None:
        return L2Diff(
            sequence=int(message["seq"]),
            as_of=datetime.now(timezone.utc),
            bid_updates=(),
            ask_updates=(),
        )

    async def fetch_snapshot(self, instrument_symbol: str) -> L2Snapshot:
        self.snapshot_calls += 1
        return L2Snapshot(sequence=0, as_of=datetime.now(timezone.utc), bids={}, asks={})


def _frame(seq: int) -> str:
    return json.dumps({"seq": seq})


def _session(adapter, repo, pool, clock, connect_fn) -> L2IngestSession:
    return L2IngestSession(
        adapter=adapter,
        instrument_id="0" * 26,
        instrument_symbol="BTCUSDT",
        pool=pool,
        coverage_repo=repo,
        clock=clock,
        ws_session_kwargs={
            "connect_fn": connect_fn,
            "sleep_fn": _no_sleep,
            "ping_sleep_fn": _never,
        },
    )


async def test_sequence_gap_closes_span_at_gap_and_reopens_after_resync_leaving_hole():
    """DoD: 갭 주입 시 재동기화가 실제로 발생하고, 손실 구간이 두 span
    사이의 빈 구간으로 남는다(조용히 이어붙이지 않는다)."""
    adapter = _FakeAdapter()
    repo = _FakeCoverageRepo()
    pool = _FakePool()
    clock = _TickingClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    frames = [_frame(s) for s in (1, 2, 4, 5)]  # 3 누락 → 갭
    conn = _FakeConnection(frames, raise_after=_closed())
    connect_fn, calls = _connect_sequence([conn])

    session = _session(adapter, repo, pool, clock, connect_fn)
    with pytest.raises(_Stop):
        await session.run()

    assert adapter.snapshot_calls == 2  # 최초 1회 + 갭 재동기화 1회
    assert len(repo.spans) == 2  # 첫 구간(갭 전) + 둘째 구간(재동기화 후~끊김)
    first, second = repo.spans
    assert first.start < first.end
    assert second.start < second.end
    # 두 span 사이에 빈 구간이 존재해야 한다 — 재동기화 시각이 갭 감지
    # 시각보다 뒤이므로 이어붙지 않는다(조용한 보간 금지).
    assert first.end < second.start
    assert calls["n"] == 2  # 등록된 연결 1개 소진 후 재시도에서 _Stop


async def test_disconnect_then_reconnect_also_closes_and_reopens_span():
    """연결 자체가 끊긴 경우도 동일하게 손실 구간을 남긴다(전송 계층
    끊김은 시퀀스 갭과 별개 경로이지만 결과는 같아야 한다)."""
    adapter = _FakeAdapter()
    repo = _FakeCoverageRepo()
    pool = _FakePool()
    clock = _TickingClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    first_conn = _FakeConnection([_frame(1), _frame(2)], raise_after=_closed())
    second_conn = _FakeConnection([_frame(1)], raise_after=_closed())
    connect_fn, calls = _connect_sequence([first_conn, second_conn])

    session = _session(adapter, repo, pool, clock, connect_fn)
    with pytest.raises(_Stop):
        await session.run()

    assert calls["n"] == 3  # 등록된 연결 2개 소진 후 재시도에서 _Stop
    assert adapter.snapshot_calls == 2  # 최초 1회 + 재연결 재동기화 1회
    assert len(repo.spans) == 2
    assert repo.spans[0].end < repo.spans[1].start


async def test_stream_ending_without_exception_still_closes_exactly_one_span():
    """서버가 예외 없이 스트림을 닫는 것도 `WsSession` 관점에서는 끊김
    취급이라(`test_ws_session.py::test_server_ending_stream_without_
    exception_is_treated_as_disconnect`와 동일 전제) span이 정확히
    1개만 닫힌다 — 유실 여부와 무관하게 임의로 여러 번 잘리지 않는다."""
    adapter = _FakeAdapter()
    repo = _FakeCoverageRepo()
    pool = _FakePool()
    clock = _TickingClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    conn = _FakeConnection([_frame(1), _frame(2), _frame(3)])  # 예외 없이 스트림 종료 → 끊김 취급
    connect_fn, calls = _connect_sequence([conn])

    session = _session(adapter, repo, pool, clock, connect_fn)
    with pytest.raises(_Stop):
        await session.run()

    assert len(repo.spans) == 1


async def test_virtual_24h_replay_never_bridges_gaps_and_resyncs_every_time():
    """DoD: 24시간 연속 수집에서 시퀀스 연속성 검증(가상 시계 리플레이로
    대체 — 실시간 대기 없음). 여러 차례 갭을 주입한 긴 프레임 스트림을
    한 연결로 흘려보내고, 갭 횟수만큼 재동기화가 일어나며 기록된 span이
    서로 겹치지 않고(non-overlapping) 항상 시간 순으로 정렬됨을 검증한다."""
    adapter = _FakeAdapter()
    repo = _FakeCoverageRepo()
    pool = _FakePool()
    clock = _TickingClock(datetime(2026, 1, 1, tzinfo=timezone.utc))

    # 1..200 연속 시퀀스에서 10군데를 의도적으로 건너뛰어 갭 10회를 만든다.
    skip_points = {20, 40, 60, 80, 100, 120, 140, 160, 180, 200}
    seqs = [s for s in range(1, 221) if s not in skip_points]
    frames = [_frame(s) for s in seqs]
    conn = _FakeConnection(frames, raise_after=_closed())
    connect_fn, calls = _connect_sequence([conn])

    session = _session(adapter, repo, pool, clock, connect_fn)
    with pytest.raises(_Stop):
        await session.run()

    # 최초 스냅샷 1회 + 갭마다 재동기화(10회).
    assert adapter.snapshot_calls == 11
    # 갭 10회가 각각 span을 하나씩 닫고, 마지막 끊김이 열려 있던 마지막
    # span을 추가로 닫는다 — 총 11개.
    assert len(repo.spans) == 11
    ordered = sorted(repo.spans, key=lambda s: s.start)
    for earlier, later in zip(ordered, ordered[1:], strict=False):
        assert earlier.end <= later.start  # 겹침 없음(non-overlapping)
        assert earlier.end < later.start  # 손실 구간이 실제로 존재(조용한 보간 금지)
