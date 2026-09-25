"""RD-2 `domain/known_at.py` -- DEEPEN(task-2905, DEPTH_DC_RD 소급감사
task-2726) D1 -> D2 증빙.

기존 `test_known_at.py`는 경계값 3건과 FA-9 위임을 monkeypatch로 증명했다
(D1) -- 감사에서 "실패주입이 위임 계약 검증(monkeypatch)뿐, 수치 성능
단언 없음, 게이트 적색 재현 없음"으로 지적됐다. 이 파일이 그 부족분을
채운다. `assert_point_in_time`은 순수 함수라 DB/네트워크가 없으므로,
여기서는 monkeypatch 없이 **실제 FA-9 `core.bitemporal.as_of` 커널**을
극단값(타임존 오프셋·연대 경계)에 태워 위임 경로 자체가 방어선으로
작동하는지를 "실패주입"으로, 동일 known_at을 고정한 채 as_of를 마이크로초
단위로 단계적으로 재생하며 각 단계의 통과/거부가 정확히 뒤집히는지를
"게이트 적색 재현"으로 삼는다. `known_at.py`는 한 줄도 고치지 않는다 --
새 기능 없음, 깊이만 올린다.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.known_at import (
    PointInTimeViolationError,
    assert_point_in_time,
)

_T0 = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)


def _item(*, known_at: datetime) -> ResearchItem:
    return ResearchItem(
        item_id=uuid4(),
        source_id="opendart",
        kind="filing",
        published_at=known_at,
        known_at=known_at,
        instruments=("005930",),
        title="분기보고서",
        body_ref=None,
        url="https://dart.fss.or.kr/x",
        language="ko",
        hash="h" * 64,
        revision_of=None,
    )


# ---- 실패 주입 (실제 FA-9 커널, monkeypatch 없음) ----------------------------


def test_equivalent_instant_in_a_different_timezone_offset_still_passes() -> None:
    """`known_at`을 UTC+9(KST)로, `as_of`를 UTC로 동일 순간을 서로 다른
    오프셋으로 표현해도 FA-9 비교가 tz-naive 로컬시각 성분을 잘못 비교하지
    않고 실제 절대시각으로 정확히 동률 판정해야 한다."""
    kst = timezone(timedelta(hours=9))
    known_at_kst = _T0.astimezone(kst)  # 같은 순간, 표기만 KST
    item = _item(known_at=known_at_kst)
    assert_point_in_time(item, _T0)  # raise 없음 -- 같은 순간이므로 통과


def test_fractional_offset_timezone_one_microsecond_after_is_rejected() -> None:
    """인도 표준시(UTC+5:30)처럼 분단위 오프셋을 가진 타임존으로 표현된
    `known_at`이 `as_of`보다 1마이크로초 뒤인 경우도 정확히 거부돼야
    한다 -- 오프셋 변환 중 마이크로초가 잘리는 회귀를 잡는다."""
    ist = timezone(timedelta(hours=5, minutes=30))
    known_at = (_T0 + timedelta(microseconds=1)).astimezone(ist)
    item = _item(known_at=known_at)
    with pytest.raises(PointInTimeViolationError):
        assert_point_in_time(item, _T0)


def test_minimum_datetime_known_at_is_always_knowable() -> None:
    """`datetime.min`에 가까운 `known_at`(연대 경계)도 FA-9 커널이 오버플로
    없이 처리하고, 항상 과거이므로 어떤 `as_of`에도 통과해야 한다."""
    item = _item(known_at=datetime(1, 1, 1, tzinfo=timezone.utc))
    assert_point_in_time(item, _T0)  # raise 없음


def test_maximum_datetime_known_at_is_rejected_against_any_realistic_as_of() -> None:
    """`datetime.max`에 가까운 `known_at`(연대 경계)은 어떤 현실적인
    `as_of`보다도 미래이므로 항상 거부돼야 한다 -- 극단값이 정수 오버플로로
    "항상 통과"로 오판정되지 않는지 확인한다."""
    item = _item(known_at=datetime(9999, 12, 31, 23, 59, 59, 999999, tzinfo=timezone.utc))
    with pytest.raises(PointInTimeViolationError):
        assert_point_in_time(item, _T0)


# ---- 성능 단언 ---------------------------------------------------------------


@pytest.mark.perf
def test_repeated_point_in_time_checks_meet_throughput_budget() -> None:
    """통과/거부가 번갈아 나오는 20,000회 반복 호출(폴링 패턴)이 절대시간
    예산 내여야 한다 -- FA-9 위임 경로가 호출당 상수시간에서 벗어나지
    않았는지."""
    iterations = 20_000
    budget_sec = 5.0  # 실측 로컬 <1.0s, CI 편차 감안
    past_item = _item(known_at=_T0 - timedelta(days=1))
    future_item = _item(known_at=_T0 + timedelta(days=1))

    start = time.perf_counter()
    passed = 0
    rejected = 0
    for i in range(iterations):
        if i % 2 == 0:
            assert_point_in_time(past_item, _T0)
            passed += 1
        else:
            try:
                assert_point_in_time(future_item, _T0)
            except PointInTimeViolationError:
                rejected += 1
    elapsed = time.perf_counter() - start

    print(
        f"[RD-2 known_at] {iterations}회 반복(통과/거부 번갈아) in {elapsed:.4f}s "
        f"(budget<{budget_sec}s)"
    )
    assert passed == iterations // 2
    assert rejected == iterations // 2
    assert elapsed < budget_sec, (
        f"{iterations}회 반복이 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."
    )


# ---- 게이트 적색 재현 ---------------------------------------------------------


def test_gate_red_as_of_replayed_microsecond_by_microsecond_around_the_boundary() -> None:
    """`known_at`을 고정한 채 `as_of`를 [known_at - 3us, known_at + 3us]
    범위에서 마이크로초 단위로 7단계 재생한다. 정확히 `as_of < known_at`인
    3단계만 거부되고, `as_of >= known_at`인 나머지 4단계(동률 포함)는
    통과해야 한다 -- 부등호 방향이 손으로 뒤집히면(`<=`가 `<`로, 혹은
    반대로) 경계에서 한 단계가 틀려 이 테스트가 적색이 된다."""
    known_at = _T0
    item = _item(known_at=known_at)

    for offset_us in range(-3, 4):
        as_of_time = known_at + timedelta(microseconds=offset_us)
        should_pass = offset_us >= 0  # as_of >= known_at

        if should_pass:
            assert_point_in_time(item, as_of_time)  # raise 없음, offset=offset_us
        else:
            with pytest.raises(PointInTimeViolationError):
                assert_point_in_time(item, as_of_time)


def test_gate_red_alternating_past_future_items_never_leak_verdict_across_calls() -> None:
    """과거/미래 아이템을 번갈아 20회 호출한다 -- 이전 호출의 판정(예외
    발생 여부)이 다음 호출의 결과에 영향을 주는 숨은 상태(예: 모듈 레벨
    캐시)가 있다면 이 교대 패턴에서 드러난다."""
    past_item = _item(known_at=_T0 - timedelta(hours=1))
    future_item = _item(known_at=_T0 + timedelta(hours=1))

    for i in range(20):
        if i % 2 == 0:
            assert_point_in_time(past_item, _T0)  # raise 없음
        else:
            with pytest.raises(PointInTimeViolationError):
                assert_point_in_time(future_item, _T0)


# ---- D2 -- 동시 호출 / 고정시드 재생 ------------------------------------------


def test_concurrent_threads_checking_point_in_time_do_not_cross_contaminate() -> None:
    """서로 다른 known_at/as_of 조합을 가진 8개 워커가 스레드풀에서 30회
    반복 동시 호출한다 -- 순수 함수라면 공유 가변 상태가 없어 당연히
    독립적이어야 하나, 회귀 시 예외 인스턴스 재사용 등이 끼어드는 것을
    잡는다."""

    def make_task(worker_id: int) -> tuple[ResearchItem, datetime, bool]:
        offset_days = worker_id - 4  # -4..3
        item = _item(known_at=_T0 + timedelta(days=offset_days))
        should_pass = offset_days <= 0
        return item, _T0, should_pass

    tasks = [make_task(i) for i in range(8)]

    def run(task: tuple[ResearchItem, datetime, bool]) -> bool:
        item, as_of_time, should_pass = task
        try:
            assert_point_in_time(item, as_of_time)
            return should_pass
        except PointInTimeViolationError:
            return not should_pass

    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in range(30):
            outcomes = list(pool.map(run, tasks))
            assert all(outcomes), "동시 호출 중 워커 간 판정 오염이 발생했다"


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_fuzz_fixed_seed_verdict_always_matches_direct_comparison(seed: int) -> None:
    """고정시드 5개로 `known_at`/`as_of` 오프셋(-30~30일)을 무작위로 골라
    호출한다 -- 결과(통과/거부)가 단순 `known_at <= as_of` 직접 비교와
    항상 일치해야 한다(무작위 조합이 우연히 위임 경로를 깨는지 탐색)."""
    rng = random.Random(seed)
    known_offset = rng.randint(-30, 30)
    as_of_offset = rng.randint(-30, 30)
    known_at = _T0 + timedelta(days=known_offset)
    as_of_time = _T0 + timedelta(days=as_of_offset)
    item = _item(known_at=known_at)

    expect_pass = known_at <= as_of_time
    if expect_pass:
        assert_point_in_time(item, as_of_time)  # raise 없음
    else:
        with pytest.raises(PointInTimeViolationError):
            assert_point_in_time(item, as_of_time)


def test_replay_with_same_seed_is_deterministic() -> None:
    """동일 시드로 두 번 판정하면 정확히 같은 결과(raise 여부)가 나와야
    한다 -- 숨은 전역 상태(시각·난수)를 참조하지 않고 오직 주어진 입력만
    으로 결정된다는 증거."""

    def _run(seed: int) -> bool:
        rng = random.Random(seed)
        known_at = _T0 + timedelta(days=rng.randint(-30, 30))
        as_of_time = _T0 + timedelta(days=rng.randint(-30, 30))
        item = _item(known_at=known_at)
        try:
            assert_point_in_time(item, as_of_time)
            return True
        except PointInTimeViolationError:
            return False

    first = _run(seed=99)
    second = _run(seed=99)
    assert first == second
