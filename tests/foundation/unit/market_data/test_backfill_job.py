"""DC-16 — application/backfill_job 단위 테스트(포트는 인메모리 가짜로 주입).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§9 DC-16.

DoD: 갭 계획→백필→커버리지 갱신 왕복이 실제로 갭을 없애는 것, 빈 provider
응답이 커버리지로 조용히 둔갑하지 않는 것(§4.1), venue 축이 어긋난 요청이
fail-closed 거부되는 것을 검증한다. 재개·동시성·실패주입 테스트는
test_backfill_job_resilience.py로 분리됐다(RATCHET-split, task-10466 —
500줄 임계값).

fixture/헬퍼는 _backfill_job_fixtures.py 공유 모듈을 그대로 쓴다.
"""

from __future__ import annotations

import pytest

from src.foundation.market_data.application.backfill_job import VenueMismatchError
from src.foundation.market_data.contracts.v1 import SeriesKey, Timeframe, Venue
from src.foundation.market_data.domain.coverage.gaps import IndeterminateCoverageError
from tests.foundation.unit.market_data._backfill_job_fixtures import (
    _INSTRUMENT_ID,
    _columns,
    _dt,
    _FakeCandleStore,
    _FakeCoverageRepository,
    _FakeProvider,
    _run,
)

__all__: list[str] = []


@pytest.mark.asyncio
async def test_backfill_fills_gap_then_second_run_finds_nothing_left() -> None:
    start, end = _dt(0), _dt(4)
    provider = _FakeProvider({(start, end): _columns([0, 1, 2, 3])})
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()

    result = await _run(provider, store, coverage_repo, range_start=start, range_end=end)

    assert result.gaps_planned == 1
    assert len(result.segments) == 1
    assert result.segments[0].stored == 4
    assert result.segments[0].span is not None
    assert coverage_repo.spans == [result.segments[0].span]
    assert len(result.merged_coverage) == 1
    assert result.merged_coverage[0].start_at == start
    assert result.merged_coverage[0].end_at == end

    # 재실행 — 이미 다 채워졌으니 더 이상 갭도, provider 호출도 없다(멱등).
    provider.calls.clear()
    second = await _run(provider, store, coverage_repo, range_start=start, range_end=end)
    assert second.gaps_planned == 0
    assert second.segments == []
    assert provider.calls == []


@pytest.mark.asyncio
async def test_empty_provider_response_does_not_fabricate_coverage() -> None:
    """§4.1 조용한 0 채움 금지 — provider가 빈 응답을 주면 span을 만들지
    않고, 다음 실행에서도 같은 갭이 다시 잡힌다."""
    start, end = _dt(0), _dt(4)
    provider = _FakeProvider({(start, end): _columns([])})
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()

    result = await _run(provider, store, coverage_repo, range_start=start, range_end=end)

    assert result.gaps_planned == 1
    assert result.segments[0].stored == 0
    assert result.segments[0].span is None
    assert coverage_repo.spans == []

    again = await _run(provider, store, coverage_repo, range_start=start, range_end=end)
    assert again.gaps_planned == 1  # 여전히 같은 갭 — 빈 응답이 커버로 둔갑하지 않았다


@pytest.mark.asyncio
async def test_venue_mismatch_between_listing_and_series_key_is_rejected() -> None:
    provider = _FakeProvider({})
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    mismatched_key = SeriesKey(
        venue=Venue.KIS_KRX, instrument_id=_INSTRUMENT_ID, timeframe=Timeframe.H1
    )

    with pytest.raises(VenueMismatchError):
        await _run(
            provider,
            store,
            coverage_repo,
            range_start=_dt(0),
            range_end=_dt(4),
            series_key=mismatched_key,
        )
    assert provider.calls == []
    assert store.rows == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("hours", [[0, 2, 3], [3, 0, 2], [0, 3]])
async def test_sparse_response_preserves_holes_and_resumes(hours: list[int]) -> None:
    """Sparse/reordered responses must not claim missing candles (I-10)."""
    store = _FakeCandleStore()
    repo = _FakeCoverageRepository()
    provider = _FakeProvider({(_dt(0), _dt(4)): _columns(hours)})
    result = await _run(provider, store, repo, range_start=_dt(0), range_end=_dt(4))
    right_start = 2 if 2 in hours else 3
    expected = [(_dt(0), _dt(1)), (_dt(right_start), _dt(4))]
    assert [(s.start, s.end) for s in repo.spans] == expected
    assert [(s.start_at, s.end_at) for s in result.merged_coverage] == expected
    assert result.segments[0].stored == len(hours)
    assert result.segments[0].span is None
    assert list(result.segments[0].spans) == repo.spans

    remaining = _FakeProvider({(_dt(1), _dt(right_start)): _columns(range(1, right_start))})
    resumed = await _run(remaining, store, repo, range_start=_dt(0), range_end=_dt(4))
    assert resumed.gaps_planned == 1
    assert [(s.start, s.end) for s in remaining.calls] == [(_dt(1), _dt(right_start))]
    assert [(s.start_at, s.end_at) for s in resumed.merged_coverage] == [(_dt(0), _dt(4))]
    remaining.calls.clear()
    replay = await _run(remaining, store, repo, range_start=_dt(0), range_end=_dt(4))
    assert replay.gaps_planned == 0
    assert remaining.calls == []


@pytest.mark.asyncio
async def test_reversed_range_is_rejected_fail_closed() -> None:
    """`plan_fetch`(DC-7)이 구간 역전(range_end < range_start)에서
    `IndeterminateCoverageError`로 fail-closed 거부한다 — 빈 갭 목록으로
    '커버리지 충분'을 오독하지 않는다. 이 오케스트레이션 레이어가 그
    예외를 삼키지 않고 그대로 전파하는 것, 그리고 I/O(provider/store)가
    전혀 일어나지 않은 채 거부되는 것을 검증한다."""
    provider = _FakeProvider({})
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()

    with pytest.raises(IndeterminateCoverageError):
        await _run(provider, store, coverage_repo, range_start=_dt(4), range_end=_dt(0))

    assert provider.calls == []
    assert store.rows == {}
    assert coverage_repo.spans == []
