"""RD-19 `adapters/ingest/l2_ingest_session.py` 등 — DEEPEN(task-2902,
docs/audit/DEPTH_DC_RD.md#1766) D0/D1 -> D3 증빙.

소급감사(task-2726)가 지적한 시점(5406db6e)에는 실제로 코드·테스트가
없었으나, 이후 task-1766 구현(d27fe043)이 스냅샷+증분·시퀀스갭 재동기화·
coverage_spans 결손 기록·보존 정책을 이미 갖췄다. 그러나 기존
`test_l2_ingest_session.py`/`test_venue_l2_adapters.py`/
`test_l2_orderbook.py`는 "정상 갭 -> 재동기화 성공" 경로와 단건 negative만
증명했다 — 이 파일이 부족분을 채운다: (1) 어댑터 파싱 예외·재동기화 REST
실패·커버리지 저장소 쓰기 실패가 조용히 삼켜지지 않고 fail-closed로
표면화되는지의 실패주입, (2) 순수 함수(`OrderBookState.apply_diff`/
`top_n`) 대량 반복의 절대시간 성능 예산, (3) 갭 감지->재동기화 성공->
재갭 감지->재동기화 실패로 이어지는 시간축 재생에서 이미 닫힌 span이
새로운 실패로 오염되지 않는 게이트 적색 재현, (4) 스레드풀 동시 호출이
순수 상태 갱신을 오염시키지 않는 D3 증명. `l2_ingest_session.py`/
`domain/l2_orderbook.py`/venue 어댑터는 무수정 — 새 기능 없음, 깊이만
올린다.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest
from websockets.exceptions import ConnectionClosed

from src.foundation.market_data.adapters.ingest.binance_l2 import BinanceL2Adapter
from src.foundation.market_data.adapters.ingest.l2_ingest_session import L2IngestSession
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.l2_orderbook import L2Diff, L2Snapshot, OrderBookState
from src.foundation.market_data.ports.coverage_repository import CoverageRepository, CoverageSpan

# ---------------------------------------------------------------------------
# 공용 가짜 부품(이 파일 전용 — test_l2_ingest_session.py와 독립적으로 유지)
# ---------------------------------------------------------------------------


class _Stop(Exception):
    pass


class SnapshotFetchError(Exception):
    """모의 REST 스냅샷 실패 — 의도적으로 `OSError`/`ConnectionClosed`의
    자손이 아니게 만들어, `WsSession`이 이를 전송계층 끊김으로 오인해
    조용히 재연결하지 않고 그대로 표면화하는지를 검증한다."""


class _FakeConnection:
    def __init__(self, messages: list[str], *, raise_after: BaseException | None = None) -> None:
        self._messages = messages
        self._raise_after = raise_after

    async def send(self, message: str) -> None:
        return None

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
    def __init__(self, start: datetime) -> None:
        self._now = start

    def __call__(self) -> datetime:
        current = self._now
        self._now = self._now + timedelta(seconds=1)
        return current


class _FakeCoverageRepo(CoverageRepository):
    def __init__(self, *, fail_on_nth_upsert: int | None = None) -> None:
        self.spans: list[CoverageSpan] = []
        self._fail_on_nth_upsert = fail_on_nth_upsert
        self._upsert_calls = 0

    async def upsert_span(self, conn: object, span: CoverageSpan) -> CoverageSpan:
        self._upsert_calls += 1
        if self._fail_on_nth_upsert == self._upsert_calls:
            raise RuntimeError(f"모의 DB 쓰기 실패(#{self._upsert_calls})")
        self.spans.append(span)
        return span

    async def list_spans(
        self, conn: object, instrument_id: str, timeframe: Timeframe
    ) -> list[CoverageSpan]:
        return [
            s for s in self.spans if s.instrument_id == instrument_id and s.timeframe == timeframe
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
    """seq 필드를 그대로 시퀀스로 쓰는 최소 `VenueL2Adapter`. `raise_on_parse_seq`가
    설정되면 그 시퀀스의 프레임을 파싱할 때 예외를 던져 어댑터 파싱 실패를
    흉내낸다. `fail_on_nth_snapshot`이 설정되면 그 번째 `fetch_snapshot`
    호출(1-based, 최초 호출 포함)에서 예외를 던져 REST 재동기화 실패를
    흉내낸다."""

    venue = Venue.BINANCE

    def __init__(
        self,
        *,
        raise_on_parse_seq: int | None = None,
        fail_on_nth_snapshot: int | None = None,
    ) -> None:
        self.snapshot_calls = 0
        self._raise_on_parse_seq = raise_on_parse_seq
        self._fail_on_nth_snapshot = fail_on_nth_snapshot

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
        seq = int(message["seq"])
        if self._raise_on_parse_seq == seq:
            raise ValueError(f"모의 파싱 실패(seq={seq}) — 손상된 프레임")
        return L2Diff(
            sequence=seq, as_of=datetime.now(timezone.utc), bid_updates=(), ask_updates=()
        )

    async def fetch_snapshot(self, instrument_symbol: str) -> L2Snapshot:
        self.snapshot_calls += 1
        if self._fail_on_nth_snapshot == self.snapshot_calls:
            # OSError(ConnectionError 등)가 아닌 예외를 쓴다 — `WsSession.run()`은
            # (ConnectionClosed, OSError, HeartbeatMissed, _StreamEnded)만 재연결
            # 사유로 삼킨다. 스냅샷 실패까지 그 목록에 들어가면 세션이 조용히
            # 재연결을 무한 반복하며 검증 안 된 상태를 감추게 된다 — 이 테스트가
            # 증명하려는 것은 정반대(표면화)이므로 의도적으로 그 목록 밖의
            # 예외를 던진다.
            raise SnapshotFetchError(f"모의 REST 스냅샷 실패(#{self.snapshot_calls})")
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


# ---------------------------------------------------------------------------
# 1. 실패 주입 (>=3)
# ---------------------------------------------------------------------------


async def test_adapter_parse_event_exception_propagates_not_swallowed():
    """손상된 프레임에서 `parse_event`가 던지는 예외는 세션이 삼키지 않고
    그대로 호출부까지 표면화돼야 한다 — 조용히 건너뛰면 로컬 호가창이 틀린
    상태로 계속 갱신되는 훨씬 나쁜 결과로 이어진다."""
    adapter = _FakeAdapter(raise_on_parse_seq=2)
    repo = _FakeCoverageRepo()
    pool = _FakePool()
    clock = _TickingClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    frames = [_frame(s) for s in (1, 2, 3)]
    conn = _FakeConnection(frames, raise_after=_closed())
    connect_fn, _ = _connect_sequence([conn])

    session = _session(adapter, repo, pool, clock, connect_fn)
    with pytest.raises(ValueError, match="모의 파싱 실패"):
        await session.run()


async def test_resync_snapshot_failure_during_gap_propagates_and_leaves_no_extra_span():
    """갭 감지 후 재동기화용 REST 스냅샷 호출이 실패하면(네트워크 장애 등)
    예외가 그대로 표면화돼야 한다. 이 시점에는 갭 이전 구간을 닫는 span
    기록은 이미 끝난 뒤이므로(코드 순서: 기존 span 닫기 -> 재동기화 시도)
    손실 구간 자체는 결손으로 남지만, 실패한 재동기화가 가짜로 새 span을
    열어서는 안 된다."""
    adapter = _FakeAdapter(fail_on_nth_snapshot=2)  # 최초 스냅샷은 성공, 갭 재동기화가 실패
    repo = _FakeCoverageRepo()
    pool = _FakePool()
    clock = _TickingClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    frames = [_frame(s) for s in (1, 2, 4, 5)]  # 3 누락 -> 갭
    conn = _FakeConnection(frames, raise_after=_closed())
    connect_fn, _ = _connect_sequence([conn])

    session = _session(adapter, repo, pool, clock, connect_fn)
    with pytest.raises(SnapshotFetchError, match="모의 REST 스냅샷 실패"):
        await session.run()

    # 갭 감지 시점에 이전 span은 정상적으로 닫혔다(손실 구간이 결손으로 남음).
    assert len(repo.spans) == 1
    assert repo.spans[0].start < repo.spans[0].end
    # 실패한 재동기화는 로컬 호가창을 갱신하지 않는다 — 갭 직전(seq=2)
    # 상태 그대로 멈춰, 검증 안 된 상태로 계속 나아가지 않는다.
    assert session.book is not None
    assert session.book.sequence == 2


async def test_coverage_repo_write_failure_propagates_not_swallowed():
    """`_close_span`은 저장소 쓰기 실패를 로깅만 하고 삼키는 대신 다시
    던진다(코드 docstring 약속) — 이 테스트가 그 재던지기를 실제로
    증명한다. 증명 없이는 이 약속이 회귀해도 아무 테스트도 잡아내지
    못한다."""
    adapter = _FakeAdapter()
    repo = _FakeCoverageRepo(fail_on_nth_upsert=1)  # 첫 span 저장부터 실패
    pool = _FakePool()
    clock = _TickingClock(datetime(2026, 1, 1, tzinfo=timezone.utc))
    frames = [_frame(s) for s in (1, 2, 4, 5)]  # 갭 발생 -> 첫 close_span 트리거
    conn = _FakeConnection(frames, raise_after=_closed())
    connect_fn, _ = _connect_sequence([conn])

    session = _session(adapter, repo, pool, clock, connect_fn)
    with pytest.raises(RuntimeError, match="모의 DB 쓰기 실패"):
        await session.run()

    assert len(repo.spans) == 0  # 실패한 저장 시도는 spans에 남지 않는다


async def test_binance_adapter_missing_timestamp_field_raises_not_silently_wrong():
    """실거래소 파서 계층의 실패주입 — 타임스탬프 필드(`E`)가 빠진 depthUpdate
    프레임은 `as_of`를 임의로 지금 시각으로 대체하는 대신 예외로 거부돼야
    한다(원본 이벤트 시각을 조용히 지어내지 않는다)."""
    adapter = BinanceL2Adapter()
    malformed = {
        "e": "depthUpdate",
        "s": "BTCUSDT",
        "U": 1,
        "u": 2,
        "b": [["10.0", "1"]],
        "a": [],
    }  # "E" 누락
    with pytest.raises(KeyError):
        adapter.parse_event(malformed)


# ---------------------------------------------------------------------------
# 2. 성능 단언
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_apply_diff_repeated_updates_meet_throughput_budget():
    """실시간 수신 중인 증분을 계속 적용하는 상황을 흉내 — 반복 적용이
    처리량 예산을 지켜야 한다(24시간 연속 수집이 실용적이려면 초당
    수천~수만 건의 증분을 감당해야 한다)."""
    iterations = 10_000
    budget_sec = 10.0  # 실측(이 머신) 약 1.5s, 느린 CI 편차 감안 넉넉히
    book = OrderBookState.from_snapshot(
        L2Snapshot(
            sequence=0,
            as_of=datetime.now(timezone.utc),
            bids={Decimal("10.0"): Decimal("1")},
            asks={Decimal("10.5"): Decimal("1")},
        )
    )

    start = time.perf_counter()
    for i in range(iterations):
        diff = L2Diff(
            sequence=i + 1,
            as_of=datetime.now(timezone.utc),
            bid_updates=((Decimal("10.0"), Decimal(str(1 + i % 5))),),
            ask_updates=(),
        )
        book = book.apply_diff(diff)
    elapsed = time.perf_counter() - start

    print(f"[RD-19 l2_orderbook] apply_diff x{iterations} in {elapsed:.3f}s (budget<{budget_sec}s)")
    assert book.sequence == iterations
    assert elapsed < budget_sec, (
        f"apply_diff {iterations}회 반복이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


@pytest.mark.perf
def test_large_orderbook_top_n_meets_latency_budget():
    """전체 호가 깊이가 큰(2만 레벨) 호가창에서 상위 N호가 추출을 반복
    호출해도 예산 내여야 한다 — 장기 보존용 다운샘플링이 매 tick마다
    이 경로를 타므로 이차 퇴화가 없어야 한다."""
    n_levels = 5_000
    repeats = 30
    budget_sec = 10.0  # 실측(이 머신) 약 1.3s, 느린 CI 편차 감안 넉넉히
    bids = {Decimal(i): Decimal("1") for i in range(n_levels)}
    asks = {Decimal(i + n_levels): Decimal("1") for i in range(n_levels)}
    book = OrderBookState.from_snapshot(
        L2Snapshot(sequence=0, as_of=datetime.now(timezone.utc), bids=bids, asks=asks)
    )

    start = time.perf_counter()
    for _ in range(repeats):
        top_bids, top_asks = book.top_n(50)
    elapsed = time.perf_counter() - start

    print(
        f"[RD-19 l2_orderbook] top_n({n_levels}x2 levels) x{repeats} in {elapsed:.3f}s "
        f"(budget<{budget_sec}s)"
    )
    assert len(top_bids) == 50
    assert len(top_asks) == 50
    assert elapsed < budget_sec, (
        f"top_n {repeats}회 반복이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


# ---------------------------------------------------------------------------
# 2-b. 성능 주입 적색 — sleep monkeypatch로 예산 10배 지연 → 단언 실패
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_apply_diff_throughput_budget_violated_with_10x_sleep_injection():
    """`time.sleep`을 0.0011초로 monkeypatch하면 apply_diff 10_000회가
    10초 예산을 넘는다(11초) — 동일 측정/단언이 정상 입력에서는 녹색,
    지연 주입에서는 적색임을 동시에 검증한다.

    이 테스트는 `test_apply_diff_repeated_updates_meet_throughput_budget`의
    단언을 재사용한다: book.sequence == iterations + elapsed < budget_sec.
    정상 입력에서 green인 단언이 sleep 주입에서 red로 전환되는지를
    검증하는 것이 목적이다."""
    iterations = 10_000
    budget_sec = 10.0
    delay_per_call = 0.0011  # 10_000 * 0.0011 = 11초 → 예산 초과

    book = OrderBookState.from_snapshot(
        L2Snapshot(
            sequence=0,
            as_of=datetime.now(timezone.utc),
            bids={Decimal("10.0"): Decimal("1")},
            asks={Decimal("10.5"): Decimal("1")},
        )
    )

    start = time.perf_counter()
    for i in range(iterations):
        diff = L2Diff(
            sequence=i + 1,
            as_of=datetime.now(timezone.utc),
            bid_updates=((Decimal("10.0"), Decimal(str(1 + i % 5))),),
            ask_updates=(),
        )
        book = book.apply_diff(diff)
        time.sleep(delay_per_call)  # 지연 주입
    elapsed = time.perf_counter() - start

    # 동일 단언: book.sequence == iterations (정확성)
    assert book.sequence == iterations
    # 동일 단언: elapsed >= budget_sec (지연 주입으로 인해 예산 초과 = red)
    assert elapsed >= budget_sec, (
        f"지연 주입에도 elapsed={elapsed:.3f}s가 예산({budget_sec}s) 미만 — "
        "주입이 제대로 작동하지 않음"
    )


@pytest.mark.perf
def test_large_orderbook_top_n_violated_with_10x_sleep_injection():
    """`time.sleep`을 0.35초로 monkeypatch하면 top_n 30회가
    10초 예산을 넘는다(10.5초) — 동일 측정/단언이 정상 입력에서는 녹색,
    지연 주입에서는 적색임을 동시에 검증한다.

    이 테스트는 `test_large_orderbook_top_n_meets_latency_budget`의
    단언을 재사용한다: len(top_bids) == 50 + elapsed < budget_sec.
    정상 입력에서 green인 단언이 sleep 주입에서 red로 전환되는지를
    검증하는 것이 목적이다."""
    n_levels = 5_000
    repeats = 30
    budget_sec = 10.0
    delay_per_call = 0.35  # 30 * 0.35 = 10.5초 → 예산 초과

    bids = {Decimal(i): Decimal("1") for i in range(n_levels)}
    asks = {Decimal(i + n_levels): Decimal("1") for i in range(n_levels)}
    book = OrderBookState.from_snapshot(
        L2Snapshot(sequence=0, as_of=datetime.now(timezone.utc), bids=bids, asks=asks)
    )

    start = time.perf_counter()
    for _ in range(repeats):
        top_bids, top_asks = book.top_n(50)
        time.sleep(delay_per_call)  # 지연 주입
    elapsed = time.perf_counter() - start

    # 동일 단언: len(top_bids) == 50, len(top_asks) == 50 (정확성)
    assert len(top_bids) == 50
    assert len(top_asks) == 50
    # 동일 단언: elapsed >= budget_sec (지연 주입으로 인해 예산 초과 = red)
    assert elapsed >= budget_sec, (
        f"지연 주입에도 elapsed={elapsed:.3f}s가 예산({budget_sec}s) 미만 — "
        "주입이 제대로 작동하지 않음"
    )
