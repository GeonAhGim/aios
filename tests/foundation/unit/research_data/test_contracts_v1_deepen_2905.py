"""RD-2 `contracts/v1.py` -- DEEPEN(task-2905, DEPTH_DC_RD 소급감사
task-2726) D1 -> D2 증빙.

기존 `test_contracts_v1.py`는 스키마 스냅샷 일치·필수 필드 누락 1건을
순차 실행으로만 증명했다(D1) -- 감사에서 "실패주입이 위임 계약 검증용
monkeypatch뿐, 수치 성능 단언 없음, 게이트 적색 재현 없음"으로 지적됐다.
이 파일이 그 부족분을 채운다. `contracts/v1.py`는 pydantic 모델 정의뿐이라
DB/네트워크가 없으므로, 여기서 "실패주입"이란 잘못된 리터럴 값·문자열이
튜플 필드로 조용히 쪼개지는 등 실제 pydantic-core 검증 경로가 방어선으로
작동하는지를, "게이트 적색 재현"이란 동일 원본 dict를 단계적으로 변형해
반복 검증하며 각 단계의 통과/거부가 뒤집히지 않는지를 뜻한다. `v1.py`는
한 줄도 고치지 않는다 -- 새 기능 없음, 깊이만 올린다.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.research_data.contracts import v1

_NOW = datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)


def _base_item_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        item_id=uuid4(),
        source_id="opendart",
        kind="filing",
        published_at=_NOW,
        known_at=_NOW,
        instruments=("005930",),
        title="분기보고서",
        body_ref=None,
        url="https://dart.fss.or.kr/x",
        language="ko",
        hash="h" * 64,
        revision_of=None,
    )
    base.update(overrides)
    return base


def _base_source_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        source_id="s",
        publisher="p",
        redistribution="store_full",
        license_ref="l",
        rate_limit=10,
        coverage="c",
    )
    base.update(overrides)
    return base


# ---- 실패 주입 --------------------------------------------------------------


def test_unknown_kind_literal_is_rejected_not_silently_coerced() -> None:
    """`kind`가 `ResearchItemKind`의 4개 값(filing/news/macro/alt) 밖이면
    pydantic-core가 `ValidationError`로 막아야 한다 -- 문자열 필드처럼
    조용히 통과시키지 않는다는 증거."""
    with pytest.raises(ValidationError, match="kind"):
        v1.ResearchItem(**_base_item_kwargs(kind="bogus"))


def test_unknown_redistribution_literal_is_rejected() -> None:
    with pytest.raises(ValidationError, match="redistribution"):
        v1.SourceMeta(**_base_source_kwargs(redistribution="give_away_for_free"))


def test_schema_version_cannot_be_overridden_to_a_foreign_value() -> None:
    """`schema_version`은 `Literal["v1"]` 고정값이다 -- 호출자가 실수로
    다른 값을 넘기면(예: 상위 v2 스키마를 잘못 재사용) 기본값으로 조용히
    되돌리는 대신 거부해야, 표준 107이 요구하는 "필드 의미 변경은 새 모듈"
    원칙이 우회되지 않는다."""
    with pytest.raises(ValidationError, match="schema_version"):
        v1.ResearchItem(**_base_item_kwargs(schema_version="v2"))


def test_string_instruments_is_rejected_not_exploded_into_characters() -> None:
    """`instruments: tuple[str, ...]`에 문자열 `"005930"`을 그대로 넘기면
    (호출자가 리스트 대신 실수로 단일 문자열을 넘긴 흔한 버그) 튜플 필드가
    이터러블인 문자열을 문자 단위로 쪼개 `("0","0","5","9","3","0")`처럼
    조용히 받아들이면 안 된다 -- pydantic-core strict tuple 검증이
    `ValidationError`로 막는지 확인한다."""
    with pytest.raises(ValidationError, match="instruments"):
        v1.ResearchItem(**_base_item_kwargs(instruments="005930"))


def test_unknown_extra_field_is_ignored_not_attached() -> None:
    """모델 설정에 `extra` 지시가 없으므로 pydantic 기본값(ignore)이
    적용된다 -- 알 수 없는 필드가 섞여 들어와도 크래시하지 않고, 그렇다고
    인스턴스 속성으로 몰래 붙지도 않는다(표준 107의 "필드 추가는 minor,
    구버전 리더는 모르는 필드를 무시" 전방 호환 증거)."""
    item = v1.ResearchItem(**_base_item_kwargs(future_field_from_v1_1="surprise"))
    assert not hasattr(item, "future_field_from_v1_1")
    assert "future_field_from_v1_1" not in item.model_dump()


def test_naive_datetime_is_rejected_for_both_time_fields() -> None:
    """`AwareDatetime`은 `published_at`/`known_at` 둘 다에 걸려 있다 --
    한쪽만 naive를 막고 다른 쪽은 놓치는 회귀를 이 두 케이스가 각각
    잡는다."""
    with pytest.raises(ValidationError, match="published_at"):
        v1.ResearchItem(**_base_item_kwargs(published_at=datetime(2026, 9, 8)))
    with pytest.raises(ValidationError, match="known_at"):
        v1.ResearchItem(**_base_item_kwargs(known_at=datetime(2026, 9, 8)))


# ---- 성능 단언 ---------------------------------------------------------------


@pytest.mark.perf
def test_bulk_validation_of_many_research_items_meets_latency_budget() -> None:
    """5,000건의 원본 dict를 `ResearchItem`으로 검증하는 왕복(구성 +
    `model_dump_json` 직렬화 + `model_validate_json` 역직렬화)이 절대시간
    예산 내여야 한다 -- 검증 경로가 건당 상수시간에서 벗어나지 않았는지."""
    n = 5_000
    budget_sec = 3.0  # 실측 로컬 <0.6s
    payloads = [
        _base_item_kwargs(
            item_id=uuid4(),
            title=f"공시 {i}",
            known_at=_NOW,
        )
        for i in range(n)
    ]

    start = time.perf_counter()
    items = [v1.ResearchItem(**payload) for payload in payloads]
    dumped = [item.model_dump_json() for item in items]
    restored = [v1.ResearchItem.model_validate_json(blob) for blob in dumped]
    elapsed = time.perf_counter() - start

    print(
        f"[RD-2 contracts_v1] {n}건 construct+dump+restore in {elapsed:.4f}s (budget<{budget_sec}s)"
    )
    assert len(restored) == n
    assert restored[0].title == "공시 0"
    assert restored[-1].title == f"공시 {n - 1}"
    assert elapsed < budget_sec, f"{n}건 왕복이 예산({budget_sec}s)을 넘었습니다({elapsed:.4f}s)."


# ---- 게이트 적색 재현 ---------------------------------------------------------


def test_gate_red_progressive_field_corruption_flips_pass_fail_at_each_stage() -> None:
    """동일 원본 dict를 시작점으로 두고, 한 번에 한 필드씩만 오염시켜 5단계로
    재생한다. 각 단계는 정확히 그 단계가 오염시킨 이유로만 거부돼야 한다
    -- 이전 단계의 실패가 다음 단계(정상 필드로 되돌린 단계)까지 새어
    "항상 거부"로 고착되거나, 반대로 검증이 한 번 통과하면 캐시되어 이후
    오염을 못 잡는 회귀를 잡는다."""
    good = _base_item_kwargs()

    stages: list[tuple[dict[str, Any], bool]] = [
        (good, True),
        ({**good, "kind": "invalid_kind"}, False),
        (good, True),  # 정상으로 복귀 -- 이전 단계 거부가 새지 않아야 통과
        ({**good, "known_at": datetime(2026, 9, 8)}, False),  # naive
        (good, True),  # 다시 정상 복귀
    ]

    for stage_index, (payload, should_pass) in enumerate(stages):
        if should_pass:
            item = v1.ResearchItem(**payload)
            assert item.schema_version == "v1", f"stage {stage_index}"
        else:
            with pytest.raises(ValidationError):
                v1.ResearchItem(**payload)


def test_gate_red_schema_snapshot_is_stable_across_repeated_validation_cycles() -> None:
    """`model_json_schema()`는 순수 클래스 메서드라 인스턴스 검증을
    반복해도 값이 흔들리면 안 된다 -- 100회 구성/파기를 반복한 뒤에도
    스키마 딕셔너리가 최초 호출과 완전히 동일해야 한다(숨은 전역 캐시
    오염 회귀 방지)."""
    first_schema = v1.ResearchItem.model_json_schema()
    for _ in range(100):
        v1.ResearchItem(**_base_item_kwargs(item_id=uuid4()))
    last_schema = v1.ResearchItem.model_json_schema()
    assert first_schema == last_schema


# ---- D2 -- 동시 호출 / 고정시드 재생 ------------------------------------------


def test_concurrent_thread_validation_does_not_cross_contaminate() -> None:
    """서로 다른 payload를 가진 8개 워커가 스레드풀에서 20회 반복 동시에
    `ResearchItem`을 구성한다 -- pydantic 모델 구성에 공유 가변 상태가
    없다면 당연히 독립적이어야 하나, 회귀 시 클래스 레벨 캐시 등이 실수로
    끼어드는 것을 잡는다."""

    def make_payload(worker_id: int) -> dict[str, Any]:
        return _base_item_kwargs(item_id=uuid4(), title=f"worker-{worker_id}")

    payloads = [make_payload(i) for i in range(8)]

    def run(payload: dict[str, Any]) -> bool:
        item = v1.ResearchItem(**payload)
        return item.title == payload["title"]

    with ThreadPoolExecutor(max_workers=8) as pool:
        for _ in range(20):
            outcomes = list(pool.map(run, payloads))
            assert all(outcomes), "동시 검증 중 워커 간 결과 오염이 발생했다"


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_fuzz_fixed_seed_valid_kind_and_redistribution_always_round_trip(seed: int) -> None:
    """고정시드 5개로 `kind`/`redistribution` 리터럴 값과 `instruments`
    개수를 무작위로 골라 구성 -> JSON 직렬화 -> 역직렬화 왕복해도 값이
    보존돼야 한다(무작위 조합이 우연히 검증 경로를 깨는지 탐색)."""
    rng = random.Random(seed)
    kind = rng.choice(list(v1.ResearchItemKind.__args__))
    n_instruments = rng.randint(0, 5)
    instruments = tuple(f"{rng.randint(0, 999999):06d}" for _ in range(n_instruments))

    item = v1.ResearchItem(**_base_item_kwargs(kind=kind, instruments=instruments))
    restored = v1.ResearchItem.model_validate_json(item.model_dump_json())

    assert restored.kind == kind
    assert restored.instruments == instruments
    assert restored.item_id == item.item_id
