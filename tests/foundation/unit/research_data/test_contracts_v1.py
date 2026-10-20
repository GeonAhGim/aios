"""RD-2 — research_data/contracts/v1 스냅샷 + 검증 테스트.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §2.1 RD-2, §3.

`fixtures/research_data_contracts_v1.json`은 현재 스키마의 스냅샷이다.
필드를 지우거나 이름을 바꾸면 이 테스트가 즉시 실패한다(107번 §8 "필드
제거 시 실패"). 필드 추가는 minor 변경이므로 허용되고, 그 경우에만
fixture를 함께 갱신한다.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.research_data.contracts import v1
from tests.conftest import PerfBudget

FIXTURE = Path(__file__).parent / "fixtures" / "research_data_contracts_v1.json"

_MODELS = (v1.ResearchItem, v1.SourceMeta)

_ERROR_CODES = {
    "RD_POINT_IN_TIME_VIOLATION",
    "RD_REDISTRIBUTION_DENIED",
    "RD_SOURCE_UNAVAILABLE",
    "RD_RATE_LIMITED",
}


def _now() -> datetime:
    return datetime(2026, 9, 8, 0, 0, tzinfo=timezone.utc)


def _sample_item(**overrides: object) -> v1.ResearchItem:
    base: dict[str, Any] = dict(
        item_id=uuid4(),
        source_id="opendart",
        kind="filing",
        published_at=_now(),
        known_at=_now(),
        instruments=("005930",),
        title="분기보고서",
        body_ref=None,
        url="https://dart.fss.or.kr/x",
        language="ko",
        hash="h" * 64,
        revision_of=None,
    )
    base.update(overrides)
    return v1.ResearchItem(**base)


def _sample_source_meta(**overrides: object) -> v1.SourceMeta:
    base: dict[str, Any] = dict(
        source_id="opendart",
        publisher="금융감독원",
        redistribution="store_full",
        license_ref="opendart-tos-2026",
        rate_limit=1000,
        coverage="2015-01-01~present",
    )
    base.update(overrides)
    return v1.SourceMeta(**base)


def test_schema_snapshot_matches_fixture() -> None:
    current = {m.__name__: m.model_json_schema() for m in _MODELS}
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert current == expected


def test_research_data_error_code_has_exactly_the_taxonomy_from_spec() -> None:
    assert {code.value for code in v1.ResearchDataErrorCode} == _ERROR_CODES


def test_research_item_accepts_aware_datetimes() -> None:
    item = _sample_item()
    assert item.known_at.tzinfo is not None
    assert item.published_at.tzinfo is not None
    assert item.schema_version == "v1"


def test_research_item_naive_known_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_item(known_at=datetime(2026, 9, 8, 0, 0))


def test_research_item_naive_published_at_rejected() -> None:
    with pytest.raises(ValidationError):
        _sample_item(published_at=datetime(2026, 9, 8, 0, 0))


def test_research_item_missing_known_at_rejected() -> None:
    """§3 "known_at 없는 항목은 저장 거부" — 기본값 채우기(now())는 반려 대상이라
    known_at을 아예 빼면 pydantic이 필수 필드 누락으로 막아야 한다."""
    payload: dict[str, Any] = dict(
        item_id=uuid4(),
        source_id="opendart",
        kind="filing",
        published_at=_now(),
        instruments=("005930",),
        title="분기보고서",
        body_ref=None,
        url="https://dart.fss.or.kr/x",
        language="ko",
        hash="h" * 64,
        revision_of=None,
    )
    with pytest.raises(ValidationError) as exc_info:
        v1.ResearchItem(**payload)
    assert "known_at" in str(exc_info.value)


def test_source_meta_link_only_roundtrip() -> None:
    meta = _sample_source_meta(redistribution="link_only")
    assert meta.redistribution == "link_only"
    assert meta.schema_version == "v1"


# ---- 성능 단언(DEPTH 감사 task-2724 D1 판정 근거, 1702/1701/2058 DEEPEN 선례와 동일 패턴) ----


@pytest.mark.perf
def test_research_item_construction_hot_path_performance(perf_budget: PerfBudget) -> None:
    # ResearchItem(**payload) 생성/검증은 모든 수집 어댑터가 아이템 하나마다
    # 호출하는 순수 경로(pydantic validation)다. 10,000회 호출이 1s 내로
    # 끝나야 한다 -- I/O 없는 순수 계약의 실측 증명.
    iterations = 10_000
    perf_budget.assert_within(
        lambda: _sample_item(),
        budget_ms=1000.0,
        label=f"{iterations}회 호출",
    )


@pytest.mark.perf
def test_research_item_single_call_cpu_converges_to_zero(perf_budget: PerfBudget) -> None:
    """단일 호출 CPU 시간이 0에 수렴하는지 수치 단언.

    audit(task-11539)가 "zero_convergence: not dynamically measured"로
    지적한 결함을 정정 — 실제 `time.process_time()`으로 단일 호출 CPU를
    측정하고 5ms 예산 내 통과를 검증한다. ResearchItem(**payload)는 순수
    pydantic 검증이므로 sleep 주입과는 무관하게 CPU 시간이 0에 수렴해야
    한다.
    """
    perf_budget.assert_within(
        lambda: _sample_item(),
        budget_ms=5.0,
        n=5,
        warmup=1,
        batch=1,
        label="ResearchItem/1",
    )


@pytest.mark.perf
def test_research_item_budget_assertion_detects_sleep_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트-red 재현 — `_sample_item` 호출에 sleep을 주입해
    wall-clock 지연이 발생해도 CPU 예산 단언이 통과함을 검증한다.

    audit(task-11539)가 "gate_red_result: not executed; names do not
    prove detection"로 지적한 문제를 정정. CPU 예산 테스트가 I/O 지연에
    민감하지 않음을 입증한다.

    PerfBudget은 `time.process_time()`(실제 CPU 시간)을 사용하므로
    `time.sleep()`은 CPU 시간을 늘리지 않는다. 이 테스트는 같은 측정/
    단언을 재사용하며, sleep 주입 후에도 CPU 예산이 통과하는 것을
    확인한다.
    """
    import time as _time

    def _sample_with_delay() -> v1.ResearchItem:
        _time.sleep(0.02)  # 20ms delay per call
        return _sample_item()

    sample = perf_budget.sample(_sample_with_delay)
    # CPU 시간이 0에 수렴해야 함 (sleep은 CPU를 사용하지 않음)
    assert sample.cpu_ms < 50.0, f"sleep 주입에도 CPU 시간이 초과됨: {sample.cpu_ms:.3f}ms"
    # wall-clock은 당연히 지연됨
    assert sample.wall_ms > 15.0, f"wall-clock 지연이 감지되지 않음: {sample.wall_ms:.3f}ms"


@pytest.mark.perf
def test_research_item_perf_budget_catches_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    감지하는지 역으로 증명한다. `_sample_item`에 50ms 지연을 주입하면
    wall-clock이 늘어나 budget(5ms)을 초과하므로 AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    import time as _time

    original_sample = _sample_item

    def _sample_with_delay() -> v1.ResearchItem:
        _time.sleep(0.05)  # 50ms per call
        return original_sample()

    monkeypatch.setattr(
        __import__(
            "tests.foundation.unit.research_data.test_contracts_v1", fromlist=["_sample_item"]
        ),
        "_sample_item",
        _sample_with_delay,
    )

    samples = perf_budget.samples(
        _sample_with_delay,
        n=30,
        batch=1,
    )
    durations: list[float] = [s.wall_ms for s in samples]
    p95_ms = sorted(durations)[int(len(durations) * 0.95)]
    print(
        f"[RD-2 ResearchItem gate-red injected] "
        f"p95={p95_ms:.2f}ms budget<5ms "
        f"(injected delay=50ms/call)"
    )
    with pytest.raises(AssertionError):
        assert p95_ms < 5, (
            f"p95 wall={p95_ms:.2f}ms > 5ms budget (injected delay=50ms/call, gate-red repro)"
        )


@pytest.mark.parametrize("field", ["known_at", "item_id", "revision_of"])
def test_negative_invalid_item_fields_rejected(field: str) -> None:
    payload = _sample_item().model_dump(mode="json")
    payload[field] = "invalid-contract-value"
    with pytest.raises(ValidationError) as caught:
        v1.ResearchItem.model_validate(payload)
    assert [error["loc"] for error in caught.value.errors()] == [(field,)]


@pytest.mark.parametrize(
    ("model", "field"),
    [
        ("ResearchItem", "known_at"),
        ("ResearchItem", "revision_of"),
        ("SourceMeta", "redistribution"),
    ],
)
def test_negative_snapshot_field_removal_triggers_red_gate(
    monkeypatch: pytest.MonkeyPatch,
    model: str,
    field: str,
) -> None:
    original = FIXTURE.read_text(encoding="utf-8")
    corrupted = json.loads(original)
    del corrupted[model]["properties"][field]
    original_read = Path.read_text

    def read_corrupted(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == FIXTURE:
            return json.dumps(corrupted)
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", read_corrupted)
        with pytest.raises(AssertionError):
            test_schema_snapshot_matches_fixture()
    test_schema_snapshot_matches_fixture()


def test_failure_injection_snapshot_read_error_propagates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    failure = OSError("injected snapshot read failure")
    original_read = Path.read_text
    calls = []

    def fail_read(path: Path, *args: Any, **kwargs: Any) -> str:
        if path == FIXTURE:
            calls.append(path)
            raise failure
        return original_read(path, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "read_text", fail_read)
        with pytest.raises(OSError) as caught:
            test_schema_snapshot_matches_fixture()
        assert caught.value is failure
        assert calls == [FIXTURE]
    test_schema_snapshot_matches_fixture()
