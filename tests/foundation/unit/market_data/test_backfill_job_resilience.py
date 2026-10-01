"""DC-16 — application/backfill_job 재개·동시성·실패주입 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9 DC-16.

RATCHET-split(task-10466): test_backfill_job.py의 500줄 임계값 근접으로,
"정상 갭-채움"과 책임 축이 다른 "중단 후 재개/동시 실시간 수집과의 경합/
의존성 실패 주입" 테스트를 이 파일로 분리했다. fixture/헬퍼는
_backfill_job_fixtures.py 공유 모듈을 그대로 쓴다.

F2(M, docs/audits/AUDIT_2026-10-01_data_ingest_replay.md §2) negative test:
동시 backfill+실시간 수집 시, 실시간이 먼저 쓴 캔들을 backfill이 덮어쓰지
않는 것을 증명한다 — 자세한 근거는
`test_concurrent_realtime_write_is_not_overwritten_by_backfill`의 docstring과
backfill_job.py 모듈 docstring("Concurrent backfill vs. realtime ingest"
절) 참조.
"""

from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

import pytest

from src.foundation.market_data.contracts.v1 import CandleRecord, Timeframe, Venue
from src.foundation.market_data.ports.coverage_repository import CoverageQuality
from src.foundation.market_data.ports.coverage_repository import CoverageSpan as StoredCoverageSpan
from tests.foundation.unit.market_data._backfill_job_fixtures import (
    _INSTRUMENT_ID,
    _ULID,
    _columns,
    _dt,
    _FakeCandleStore,
    _FakeCoverageRepository,
    _FakeProvider,
    _run,
    _series_key,
)

__all__: list[str] = []


@pytest.mark.asyncio
async def test_resume_after_interruption_only_refetches_remaining_gap() -> None:
    """가운데 구간(01:00-02:00)은 이미 커버된 상태에서 시작 — 앞뒤 두 갭이
    생긴다. provider가 첫 갭에서 예외를 던지면, 그 갭 이전엔 아무것도
    저장되지 않지만(store 호출조차 없음) 재호출 시 여전히 두 갭이 남는다.
    이후 provider가 둘 다 답하면 재실행 한 번으로 남은 갭이 전부 채워진다
    (재개 = plan_fetch가 최신 DB 상태를 다시 읽는 것, 별도 상태 불필요)."""
    start, end = _dt(0), _dt(4)
    covered_start, covered_end = _dt(1), _dt(2)
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    await coverage_repo.upsert_span(
        object(),
        StoredCoverageSpan(
            instrument_id=_ULID,
            venue=Venue.BITGET,
            timeframe=Timeframe.H1,
            quality=CoverageQuality.PROVISIONAL,
            start=covered_start,
            end=covered_end,
        ),
    )
    await store.upsert_batch(
        object(),
        uuid4(),
        [
            CandleRecord(
                key=_series_key(),
                open_time=_dt(1),
                close_time=_dt(2),
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            )
        ],
    )

    failing_provider = _FakeProvider({})  # 등록된 답이 없으니 첫 호출에서 KeyError
    with pytest.raises(KeyError):
        await _run(failing_provider, store, coverage_repo, range_start=start, range_end=end)
    assert coverage_repo.spans == [
        StoredCoverageSpan(
            instrument_id=_ULID,
            venue=Venue.BITGET,
            timeframe=Timeframe.H1,
            quality=CoverageQuality.PROVISIONAL,
            start=covered_start,
            end=covered_end,
        )
    ]  # 중단 지점 이전엔 아무 변화 없음

    working_provider = _FakeProvider(
        {
            (start, covered_start): _columns([0]),
            (covered_end, end): _columns([2, 3]),
        }
    )
    result = await _run(working_provider, store, coverage_repo, range_start=start, range_end=end)

    assert result.gaps_planned == 2
    assert {seg.stored for seg in result.segments} == {1, 2}
    assert len(coverage_repo.spans) == 3  # 기존 1개 + 새로 채운 2개

    # 세 번째 실행 — 이제 전 구간이 커버돼 갭이 없다.
    final = await _run(working_provider, store, coverage_repo, range_start=start, range_end=end)
    assert final.gaps_planned == 0


@pytest.mark.asyncio
async def test_concurrent_realtime_write_is_not_overwritten_by_backfill() -> None:
    """F2(M, AUDIT_2026-10-01_data_ingest_replay.md §2) 재현 + 회귀 가드.

    감사의 전제("backfill upsert가 `ON CONFLICT DO UPDATE`를 쓴다")는 현재
    코드와 맞지 않는다 — `postgres_candle_store.upsert_batch`는 LA-13부터
    줄곧 PK(`venue, instrument_id, timeframe, open_time`)에
    `ON CONFLICT DO NOTHING`이었다(git log: 22cec238f 최초 도입 이후 변경
    없음). 이 테스트가 쓰는 `_FakeCandleStore.upsert_batch`도 같은
    first-writer-wins 규칙을 흉내낸다(기존 open_time은 건너뛰고 added만
    센다). 즉 실시간 수집이 특정 open_time을 backfill보다 먼저 쓰면, 그
    open_time은 PK 충돌로 거부되어 backfill 값으로 덮이지 않는다 — WHERE
    조건을 추가할 필요 없이 이미 성립하는 불변식이며, 이 테스트는 그
    불변식이 깨지면(예: 누군가 DO NOTHING을 DO UPDATE로 바꾸면) 실패한다."""
    start, end = _dt(0), _dt(4)
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()

    realtime_sentinel = CandleRecord(
        key=_series_key(),
        open_time=_dt(1),
        close_time=_dt(2),
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("999"),  # realtime이 먼저 쓴 값 — backfill 값(105)과 다름
        volume=Decimal("1"),
    )
    await store.upsert_batch(object(), uuid4(), [realtime_sentinel])

    # 아직 coverage span은 선언되지 않았다 — 전 구간이 NOT_COVERED 갭 하나다.
    provider = _FakeProvider({(start, end): _columns([0, 1, 2, 3])})

    result = await _run(provider, store, coverage_repo, range_start=start, range_end=end)

    assert result.gaps_planned == 1
    # open_time=01:00은 이미 존재해 PK 충돌로 건너뛰므로 4개가 아니라 3개만 삽입된다.
    assert result.segments[0].stored == 3

    stored_at_hour1 = next(
        c for c in store.rows[(Venue.BITGET, _INSTRUMENT_ID, Timeframe.H1)] if c.open_time == _dt(1)
    )
    assert stored_at_hour1.close == Decimal("999")  # realtime 값이 그대로 보존됐다


@pytest.mark.asyncio
async def test_failure_injection_mid_loop_leaves_earlier_gaps_persisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입 — 두 갭(앞/뒤) 중 첫 갭은 정상 처리되고, 두 번째 갭에서
    `coverage_repo.upsert_span`이 예외를 던지면(의존성 실패 흉내) 이미
    커밋된 첫 갭의 store/coverage 기록은 롤백되지 않는다(모듈 docstring
    "Resume after interruption" 절: 이 함수는 자체 트랜잭션을 열지 않으므로
    갭 N에서 실패해도 1..N-1은 이미 영속화돼 있다는 문서화된 동작)."""
    start, end = _dt(0), _dt(4)
    covered_start, covered_end = _dt(1), _dt(2)
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    # 중간 구간을 미리 커버해 두어 앞(00-01)/뒤(02-04) 두 갭이 생기게 한다.
    await coverage_repo.upsert_span(
        object(),
        StoredCoverageSpan(
            instrument_id=_ULID,
            venue=Venue.BITGET,
            timeframe=Timeframe.H1,
            quality=CoverageQuality.PROVISIONAL,
            start=covered_start,
            end=covered_end,
        ),
    )
    await store.upsert_batch(
        object(),
        uuid4(),
        [
            CandleRecord(
                key=_series_key(),
                open_time=_dt(1),
                close_time=_dt(2),
                open=Decimal("1"),
                high=Decimal("1"),
                low=Decimal("1"),
                close=Decimal("1"),
                volume=Decimal("1"),
            )
        ],
    )
    provider = _FakeProvider(
        {
            (start, covered_start): _columns([0]),
            (covered_end, end): _columns([2, 3]),
        }
    )

    real_upsert_span = coverage_repo.upsert_span
    call_count = 0

    async def _flaky_upsert_span(conn: object, span: StoredCoverageSpan) -> StoredCoverageSpan:
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise ConnectionError("의존성(coverage_repo) 실패 주입 — 커밋 중단")
        return await real_upsert_span(conn, span)

    monkeypatch.setattr(coverage_repo, "upsert_span", _flaky_upsert_span)

    with pytest.raises(ConnectionError):
        await _run(provider, store, coverage_repo, range_start=start, range_end=end)

    # 첫 갭(00-01)은 예외 전에 이미 store/coverage에 영속화돼 있다.
    assert store.rows[(Venue.BITGET, _INSTRUMENT_ID, Timeframe.H1)]
    persisted_starts = {s.start for s in coverage_repo.spans}
    assert start in persisted_starts  # 첫 갭 기록은 롤백되지 않았다
    assert covered_end not in persisted_starts  # 두 번째 갭은 실패로 커밋되지 않았다
