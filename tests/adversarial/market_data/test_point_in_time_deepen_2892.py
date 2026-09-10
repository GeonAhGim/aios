"""DC-21 `domain/point_in_time.py` -- DEEPEN(task-2892,
DEPTH_DC_RD 소급감사 task-2726) D1 -> D3 증빙.

기존 `test_point_in_time.py`는 단일 정정(T0/T1) 리키지 방지와 FA-9
`core.bitemporal.as_of` 위임을 순차 실행으로만 증명했다(D1) -- 감사에서
"수치 성능 단언 없음, 게이트 적색 재현 없음, D3 요소 없음"으로 지적됐다.
이 파일이 그 부족분을 채운다. `point_in_time.py`는 순수 함수라 DB/네트워크가
없으므로, 이 파일에서 "실패주입"이란 naive as_of가 FA-9 위임 경로를 통해
여전히 거부되는지·동률 known_at의 결정론적 타이브레이크를, "게이트 적색
재현"이란 하나의 레코드 목록을 여러 as_of 시점으로 단계적으로 재생하며
각 단계가 이전 단계의 값으로 새지 않는지를, "D3"란 순수 함수에 공유 가변
상태가 없어 다중 스레드 동시 호출·고정시드 재생이 서로 오염되지 않는지를
뜻한다. `point_in_time.py`는 한 줄도 고치지 않는다 -- 새 기능 없음, 깊이만
올린다.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from src.foundation.market_data.domain.point_in_time import (
    ReferenceAttribute,
    latest_attributes_as_of,
)

_INSTRUMENT_ID = "TEST-INSTRUMENT-DEEPEN"
_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _attr(*, key: str, value: str, known_at: datetime) -> ReferenceAttribute:
    return ReferenceAttribute(
        instrument_id=_INSTRUMENT_ID, attr_key=key, attr_value=value, known_at=known_at
    )


# ---- 실패 주입 -------------------------------------------------------------


def test_naive_as_of_is_rejected_through_the_fa9_delegation_path() -> None:
    """`as_of`가 naive datetime이면 이 모듈이 조용히 UTC로 취급하는 대신
    FA-9 `core.bitemporal.as_of`가 거부한 `ValueError`가 그대로 전파돼야
    한다 -- 위임 경로 자체가 fail-closed 방어선임을 증명한다."""
    attribute = _attr(key="lot_size", value="10", known_at=_T0)
    with pytest.raises(ValueError, match="naive"):
        latest_attributes_as_of([attribute], as_of=datetime(2026, 1, 2))


def test_tie_break_on_identical_known_at_keeps_first_seen_deterministically() -> None:
    """DB PK `(instrument_id, attr_key, known_at)`는 동률을 막지만, 순수
    함수는 그 불변조건을 가정하지 않는다(§ latest_attributes_as_of
    docstring). 동일 `known_at`을 가진 두 행이 입력에 섞여도 크래시하지
    않고, 먼저 등장한 행을 결정론적으로 유지해야 한다(엄격한 `>` 비교이므로
    나중 행이 덮어쓰지 않는다) -- 이 동작이 뒤집혀도(예: `>=`로 바뀌어도)
    이 테스트가 적색이 된다."""
    first = _attr(key="lot_size", value="A", known_at=_T0)
    second = _attr(key="lot_size", value="B", known_at=_T0)

    result = latest_attributes_as_of([first, second], as_of=_T0)
    assert result["lot_size"].attr_value == "A"

    reversed_result = latest_attributes_as_of([second, first], as_of=_T0)
    assert reversed_result["lot_size"].attr_value == "B"


def test_far_future_known_at_never_leaks_into_present_query() -> None:
    """`known_at`이 수백 년 뒤인 정정이 섞여도 현재 시점 조회에 새어들면
    안 된다 -- 정수 오버플로/비교 연산자 실수로 극단값이 항상 "최신"으로
    오판정되지 않는지 확인한다."""
    present = _attr(key="lot_size", value="10", known_at=_T0)
    far_future = _attr(
        key="lot_size", value="9999", known_at=datetime(3000, 1, 1, tzinfo=timezone.utc)
    )

    result = latest_attributes_as_of([present, far_future], as_of=_T0)
    assert result["lot_size"].attr_value == "10"


# ---- 성능 단언 --------------------------------------------------------------


@pytest.mark.perf
def test_latest_attributes_as_of_meets_latency_budget_with_many_keys_and_corrections() -> None:
    """2,000개 attr_key x 3건 정정(6,000 레코드)에 대한 단건 판정이 절대시간
    예산 내여야 한다(선형 스캔이 이차로 퇴화하지 않았는지 -- FA-9 `as_of`
    필터링 + 이 모듈의 attr_key별 최댓값 리듀스 둘 다 대상)."""
    n_keys = 2_000
    budget_sec = 2.0  # 실측 로컬 <0.3s
    as_of = _T0 + timedelta(days=2)
    records: list[ReferenceAttribute] = []
    for i in range(n_keys):
        key = f"attr_{i}"
        records.append(_attr(key=key, value="v0", known_at=_T0))
        records.append(_attr(key=key, value="v1", known_at=_T0 + timedelta(days=1)))
        records.append(
            _attr(key=key, value="v2", known_at=_T0 + timedelta(days=3))
        )  # 미래(안 보임)

    start = time.perf_counter()
    result = latest_attributes_as_of(records, as_of=as_of)
    elapsed = time.perf_counter() - start

    print(
        f"[DC-21 point_in_time] latest_attributes_as_of with {len(records)} records "
        f"({n_keys} keys) in {elapsed:.4f}s (budget<{budget_sec}s)"
    )
    assert len(result) == n_keys
    assert result["attr_0"].attr_value == "v1"  # v2는 as_of보다 미래라 안 보임
    assert elapsed < budget_sec, (
        f"{len(records)}건 판정이 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."
    )


@pytest.mark.perf
def test_repeated_calls_meet_throughput_budget() -> None:
    """동일 레코드 집합에 대한 반복 판정(호출자가 매번 as_of만 바꾸는
    폴링 패턴)이 처리량 예산을 지켜야 한다."""
    iterations = 5_000
    budget_sec = 4.0  # 실측 로컬 <0.5s, CI 편차 감안
    records = [
        _attr(key="lot_size", value="10", known_at=_T0),
        _attr(key="lot_size", value="20", known_at=_T0 + timedelta(days=1)),
        _attr(key="tick_size", value="0.01", known_at=_T0),
    ]

    start = time.perf_counter()
    for i in range(iterations):
        result = latest_attributes_as_of(records, as_of=_T0 + timedelta(hours=i % 48))
        assert "lot_size" in result
    elapsed = time.perf_counter() - start

    print(
        f"[DC-21 point_in_time] latest_attributes_as_of x{iterations} repeated calls "
        f"in {elapsed:.3f}s (budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"{iterations}회 반복이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s)."
    )


# ---- 게이트 적색 재현 -------------------------------------------------------


def test_gate_red_correction_chain_replayed_stage_by_stage_never_leaks_future_value() -> None:
    """단일 attr_key에 대해 10건의 정정을 T0..T9에 쌓아두고, as_of를
    T0부터 T9까지 순서대로 재생한다. 각 단계는 정확히 그 단계까지의 최신
    값만 봐야 한다 -- 이전 단계 결과가 다음 단계로 캐시처럼 새거나(값이
    갱신되지 않음), 다음 단계 값이 앞당겨 보이면(리키지) 이 테스트가
    적색이 된다. 레코드 목록은 전체 재생 동안 하나만 공유해 입력 목록이
    호출 간 변형되지 않음(순수성)도 함께 증명한다."""
    records = [
        _attr(key="lot_size", value=str(i), known_at=_T0 + timedelta(days=i)) for i in range(10)
    ]

    for stage in range(10):
        as_of = _T0 + timedelta(days=stage, hours=1)
        result = latest_attributes_as_of(records, as_of=as_of)
        assert result["lot_size"].attr_value == str(stage), (
            f"stage {stage}: expected {stage!r}, got {result['lot_size'].attr_value!r}"
        )

    # 재생이 끝난 뒤에도 원본 입력이 그대로다 -- 리듀스 과정이 입력을 변형하지 않는다.
    assert len(records) == 10
    assert records[0].attr_value == "0"


def test_gate_red_interleaved_multi_key_records_do_not_cross_contaminate() -> None:
    """서로 다른 attr_key의 정정 행을 입력 목록에 무작위로 뒤섞어도, 각
    key의 독립적인 "최신 known_at <= as_of" 판정이 다른 key의 행 개수·
    known_at 분포에 영향받으면 안 된다. `tick_size`는 정정이 1건뿐이고
    `lot_size`는 20건이 섞여 있는 비대칭 상황에서도 둘 다 정확해야 한다."""
    rng = random.Random(7)
    records = [
        _attr(key="lot_size", value=str(i), known_at=_T0 + timedelta(hours=i)) for i in range(20)
    ]
    records.append(_attr(key="tick_size", value="0.01", known_at=_T0))
    rng.shuffle(records)

    as_of = _T0 + timedelta(hours=9, minutes=30)
    result = latest_attributes_as_of(records, as_of=as_of)

    assert result["lot_size"].attr_value == "9"
    assert result["tick_size"].attr_value == "0.01"


# ---- D3 -- 동시 다중 스레드 / 고정시드 재생 결정론 --------------------------


def test_concurrent_threads_calling_pure_function_do_not_cross_contaminate() -> None:
    """서로 다른 데이터셋·as_of를 가진 8개 워커를 스레드풀에서 30회 반복
    동시 호출한다 -- 순수 함수라면 공유 가변 상태가 없어 당연히 서로
    오염되지 않아야 하나, 회귀 시 모듈 레벨 캐시 등이 실수로 추가되는
    것을 이 테스트가 잡는다."""

    def make_task(worker_id: int) -> tuple[list[ReferenceAttribute], datetime, str]:
        records = [
            _attr(
                key="lot_size",
                value=f"w{worker_id}-v{i}",
                known_at=_T0 + timedelta(days=i),
            )
            for i in range(5)
        ]
        as_of = _T0 + timedelta(days=2, hours=1)
        expected = f"w{worker_id}-v2"
        return records, as_of, expected

    tasks = [make_task(worker_id) for worker_id in range(8)]

    def run(task: tuple[list[ReferenceAttribute], datetime, str]) -> bool:
        records, as_of, expected = task
        result = latest_attributes_as_of(records, as_of=as_of)
        return result["lot_size"].attr_value == expected

    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in range(30):
            outcomes = list(pool.map(run, tasks))
            assert all(outcomes), "동시 호출 중 워커 간 결과 오염이 발생했다"


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_fuzz_fixed_seed_result_always_respects_known_at_leq_as_of_invariant(seed: int) -> None:
    """고정시드 5개로 무작위 개수(3~15건)의 정정을 무작위 순서·무작위
    known_at 오프셋(0~30일)으로 생성해 판정한다. 결과로 나온 값의
    `known_at`이 `as_of`를 넘어서면 안 되고(리키지 없음), 입력 중 그
    조건을 만족하는 행 중 최댓값이어야 한다(무작위성이 불변식을 깨는
    입력 조합을 우연히 만들어내는지 탐색)."""
    rng = random.Random(seed)
    n = rng.randint(3, 15)
    offsets = rng.sample(range(30), n)
    records = [
        _attr(key="lot_size", value=f"v{offset}", known_at=_T0 + timedelta(days=offset))
        for offset in offsets
    ]
    rng.shuffle(records)
    as_of = _T0 + timedelta(days=rng.randint(0, 30))

    visible_offsets = [o for o in offsets if _T0 + timedelta(days=o) <= as_of]
    result = latest_attributes_as_of(records, as_of=as_of)

    if not visible_offsets:
        assert "lot_size" not in result
    else:
        expected_offset = max(visible_offsets)
        assert result["lot_size"].attr_value == f"v{expected_offset}"


def test_replay_with_same_seed_is_deterministic() -> None:
    """동일 시드로 두 번 데이터셋을 생성·판정하면 정확히 같은 결과가
    나와야 한다 -- 숨겨진 전역 상태(시각·난수)를 참조하지 않고 오직 주어진
    입력(`attributes`, `as_of`)만으로 결정된다는 증거."""

    def _run(seed: int) -> str:
        rng = random.Random(seed)
        n = rng.randint(3, 15)
        offsets = rng.sample(range(30), n)
        records = [
            _attr(key="lot_size", value=f"v{offset}", known_at=_T0 + timedelta(days=offset))
            for offset in offsets
        ]
        rng.shuffle(records)
        as_of = _T0 + timedelta(days=rng.randint(0, 30))
        result = latest_attributes_as_of(records, as_of=as_of)
        return result["lot_size"].attr_value if "lot_size" in result else "<none>"

    first = _run(seed=99)
    second = _run(seed=99)
    assert first == second
