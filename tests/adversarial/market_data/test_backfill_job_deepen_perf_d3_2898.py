# ratchet-allow: unused _FakeProvider stub methods raise NotImplementedError (never exercised)
"""DC-16 `application/backfill_job.py` -- DEEPEN(task-2898,
DEPTH_DC_RD 소급감사 task-2726) D1 -> D3 증빙.

성능 단언 및 D3 다중 인스턴스/고정시드 재생 테스트.
공용 헬퍼/페이크 클래스는 `test_backfill_job_deepen_2898.py`에 정의됨.
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from src.foundation.market_data.application.backfill_job import BackfillJobResult
from src.foundation.market_data.contracts.v1 import Timeframe, Venue
from src.foundation.market_data.domain.candle_columns import CandleColumns
from src.foundation.market_data.ports.coverage_repository import (
    CoverageQuality,
)
from src.foundation.market_data.ports.coverage_repository import (
    CoverageSpan as StoredCoverageSpan,
)
from tests.adversarial.market_data.test_backfill_job_deepen_2898 import (
    _columns,
    _columns_from_times,
    _dt,
    _EchoProvider,
    _FakeCandleStore,
    _FakeCoverageRepository,
    _FakeProvider,
    _listing,
    _run,
    _seed_covered_candles,
    _series_key,
)

_ULID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"


# ---- 성능 단언 ---------------------------------------------------------------


def _checkerboard(
    n_gaps: int,
) -> tuple[
    datetime, datetime, list[StoredCoverageSpan], dict[tuple[datetime, datetime], CandleColumns]
]:
    """짝수 시간대는 이미 커버, 홀수 시간대는 미커버로 둬 `plan_fetch`가
    매 홀수 시간마다 별도 `CoverageGap`을 내도록 한다(연속 구간이면
    `_coalesce`가 하나로 합쳐 갭 수를 인위적으로 부풀릴 수 없다 -- 이렇게
    하면 처리 비용이 실제 gap 개수에 비례하도록 강제된다)."""
    base = datetime(2026, 9, 4, 0, 0, tzinfo=timezone.utc)
    start = base
    end = base + timedelta(hours=2 * n_gaps)
    covered_spans = [
        StoredCoverageSpan(
            instrument_id=_ULID,
            venue=Venue.BITGET,
            timeframe=Timeframe.H1,
            quality=CoverageQuality.PROVISIONAL,
            start=base + timedelta(hours=2 * i),
            end=base + timedelta(hours=2 * i + 1),
        )
        for i in range(n_gaps)
    ]
    answers = {
        (
            base + timedelta(hours=2 * i + 1),
            base + timedelta(hours=2 * i + 2),
        ): _columns_from_times([base + timedelta(hours=2 * i + 1)])
        for i in range(n_gaps)
    }
    return start, end, covered_spans, answers


@pytest.mark.perf
@pytest.mark.asyncio
async def test_backfill_job_meets_latency_budget_with_many_disjoint_gaps() -> None:
    n_gaps = 250
    budget_sec = 5.0  # 실측 로컬 <1s, CI 편차 감안
    start, end, covered_spans, answers = _checkerboard(n_gaps)
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    coverage_repo.spans = list(covered_spans)
    _seed_covered_candles(store, covered_spans)
    provider = _FakeProvider(answers)

    t0 = time.perf_counter()
    result = await _run(provider, store, coverage_repo, range_start=start, range_end=end)
    elapsed = time.perf_counter() - t0

    print(f"[DC-16 backfill_job] {n_gaps}개 분리 갭 처리 {elapsed:.4f}s (budget<{budget_sec}s)")
    assert result.gaps_planned == n_gaps
    assert len(provider.calls) == n_gaps  # 갭당 정확히 한 번 -- 재조회/중복fetch 없음
    assert elapsed < budget_sec, (
        f"{n_gaps}개 갭 처리가 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."
    )


@pytest.mark.perf
@pytest.mark.asyncio
async def test_backfill_job_scales_sub_quadratically_with_gap_count() -> None:
    """`merged` 커버리지 재계산(`merge_spans([*merged, new])`)가 갭마다
    이미 합쳐진 전체 목록을 다시 스캔하므로 이론상 O(갭^2)로 퇴화할 수
    있다. 갭 수를 4배로 늘렸을 때 처리 시간이 넉넉한 여유배수(12배)를
    넘으면 이차 퇴화로 간주해 적색 처리한다(순수 선형이면 ~4배)."""

    async def _time_for(n_gaps: int) -> float:
        start, end, covered_spans, answers = _checkerboard(n_gaps)
        store = _FakeCandleStore()
        coverage_repo = _FakeCoverageRepository()
        coverage_repo.spans = list(covered_spans)
        _seed_covered_candles(store, covered_spans)
        provider = _FakeProvider(answers)
        t0 = time.perf_counter()
        await _run(provider, store, coverage_repo, range_start=start, range_end=end)
        return time.perf_counter() - t0

    small = await _time_for(60)
    large = await _time_for(240)  # 4배

    print(f"[DC-16 backfill_job] scaling: 60 gaps={small:.4f}s, 240 gaps={large:.4f}s")
    ratio_budget = max(small * 12.0, 0.5)
    assert large < ratio_budget, (
        f"gap 4배 증가에 처리시간이 {large / max(small, 1e-6):.1f}배로 늘었습니다"
        f"(이차 퇴화 의심: small={small:.4f}s, large={large:.4f}s, budget<{ratio_budget:.4f}s)."
    )


# ---- D3 -- 다중 인스턴스 동시성 / 고정시드 재생 -------------------------------


@pytest.mark.asyncio
async def test_concurrent_backfills_for_different_instruments_do_not_cross_contaminate() -> None:
    """서로 다른 instrument_id 3개를 같은 `_FakeCandleStore`/
    `_FakeCoverageRepository`/provider 인스턴스에 `asyncio.gather`로
    동시에 백필한다. 이벤트 루프 하나에서 협조적으로 스케줄되는
    코루틴이라 진짜 스레드 경합은 아니지만, 인터리빙된 await 지점 사이에
    다른 워커의 상태가 새는 경우(예: 저장소/커버리지 키를 잘못 공유)는
    이 테스트가 잡는다."""
    start, end = _dt(0), _dt(4)
    store = _FakeCandleStore()
    coverage_repo = _FakeCoverageRepository()
    provider = _FakeProvider({(start, end): _columns([0, 1, 2, 3])})

    instrument_ids = [uuid4() for _ in range(3)]
    ulids = [
        "01ARZ3NDEKTSV4RRFFQ69G5FA1",
        "01ARZ3NDEKTSV4RRFFQ69G5FA2",
        "01ARZ3NDEKTSV4RRFFQ69G5FA3",
    ]

    async def _backfill(instrument_id: UUID, ulid: str) -> BackfillJobResult:
        return await _run(
            provider,
            store,
            coverage_repo,
            range_start=start,
            range_end=end,
            listing=_listing(instrument_id=ulid),
            series_key=_series_key(instrument_id=instrument_id),
        )

    results = await asyncio.gather(
        *(_backfill(iid, u) for iid, u in zip(instrument_ids, ulids, strict=True))
    )

    assert len(results) == 3
    for r in results:
        assert r.gaps_planned == 1
        assert r.segments[0].span is not None
        assert r.segments[0].stored == 4  # 4시간분 저장

    # 각 instrument_id의 캔들이 정확히 4개씩, 다른 instrument_id의 캔들과 섞이지 않음
    for iid, _ulid in zip(instrument_ids, ulids, strict=True):
        key = (Venue.BITGET, iid, Timeframe.H1)
        rows = store.rows.get(key, [])
        assert len(rows) == 4
        for c in rows:
            assert c.key.instrument_id == iid  # instrument_id가 섞이지 않음


@pytest.mark.asyncio
async def test_fixed_seed_random_coverage_converges_without_overlap_failure() -> None:
    """고정시드 5개로 무작위 기존 커버리지 구간을 흩뿌린 뒤, 한 번의 백필로
    남은 모든 갭을 채운다. `_FakeCoverageRepository.upsert_span`은
    겹치는 구간을 `ValueError`로 거부하므로, `plan_fetch`가 계산한
    갭이 조금이라도 기존 커버리지와 겹치면 이 테스트가 그 자리에서
    적색이 된다. 채운 뒤 재실행하면 갭이 0이어야 한다(완전 수렴)."""
    for seed in (10, 20, 30, 40, 50):
        rng = random.Random(seed)
        start = _dt(0)
        total_hours = 24
        end = start + timedelta(hours=total_hours)

        n_spans = rng.randint(2, 6)
        used_hours: set[int] = set()
        covered_spans: list[StoredCoverageSpan] = []
        attempts = 0
        while len(covered_spans) < n_spans and attempts < 50:
            attempts += 1
            h = rng.randint(0, total_hours - 2)
            if h in used_hours or (h + 1) in used_hours:
                continue
            used_hours.add(h)
            covered_spans.append(
                StoredCoverageSpan(
                    instrument_id=_ULID,
                    venue=Venue.BITGET,
                    timeframe=Timeframe.H1,
                    quality=CoverageQuality.PROVISIONAL,
                    start=start + timedelta(hours=h),
                    end=start + timedelta(hours=h + 1),
                )
            )

        store = _FakeCandleStore()
        coverage_repo = _FakeCoverageRepository()
        coverage_repo.spans = covered_spans
        _seed_covered_candles(store, covered_spans)

        first = await _run(_EchoProvider(), store, coverage_repo, range_start=start, range_end=end)
        assert first.gaps_planned == len(first.segments)
        for segment in first.segments:
            assert segment.span is not None  # EchoProvider는 항상 응답하므로 빈 세그먼트가 없다

        again = await _run(_EchoProvider(), store, coverage_repo, range_start=start, range_end=end)
        assert again.gaps_planned == 0  # 완전 수렴 -- 남은 갭이 없다
