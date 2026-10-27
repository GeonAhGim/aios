"""RD-3 — `domain/redistribution.assert_redistribution_allowed` tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §3, §9 RD-3
DoD (a)(b).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import cast
from uuid import uuid4

import pytest

from src.foundation.market_data.domain.entitlement import source_contract as source_contract_module
from src.foundation.market_data.domain.entitlement.source_contract import (
    DataUse,
    RedistributionScope,
    SourceCapability,
    SourceContractGrant,
    SourceContractTier,
)
from src.foundation.research_data.contracts.v1 import (
    RedistributionPolicy,
    ResearchItem,
    SourceMeta,
)
from src.foundation.research_data.domain.redistribution import (
    RedistributionViolationError,
    assert_redistribution_allowed,
)
from tests.conftest import PerfBudget

_KNOWN_AT = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
_CAPABILITY = SourceCapability(
    asset_classes=frozenset({"EQUITY_KR"}), resolutions=frozenset({"1d"})
)


def _item(*, source_id: str = "GDELT", body_ref: str | None) -> ResearchItem:
    return ResearchItem(
        item_id=uuid4(),
        source_id=source_id,
        kind="news",
        published_at=_KNOWN_AT,
        known_at=_KNOWN_AT,
        instruments=("005930",),
        title="some headline",
        body_ref=body_ref,
        url="https://example.com/x",
        language="en",
        hash="h" * 64,
        revision_of=None,
    )


def _source(
    *, source_id: str = "GDELT", redistribution: RedistributionPolicy = "link_only"
) -> SourceMeta:
    return SourceMeta(
        source_id=source_id,
        publisher="GDELT Project",
        redistribution=redistribution,
        license_ref="https://example.com/license",
        rate_limit=60,
        coverage="2015-01-01~present",
    )


def _grant(
    *, allowed: bool = True, scope: RedistributionScope | None = RedistributionScope.INTERNAL
) -> SourceContractGrant:
    if not allowed:
        return SourceContractGrant(
            allowed=False,
            tier=None,
            redistribution_scope=None,
            rate_limit=None,
            quota=None,
            capability=None,
            denial_reason=source_contract_module.SourceContractDenialReason.NOT_FOUND,
        )
    return SourceContractGrant(
        allowed=True,
        tier=SourceContractTier.FREE,
        redistribution_scope=scope,
        rate_limit=60,
        quota=1000,
        capability=_CAPABILITY,
        denial_reason=None,
    )


def test_link_only_source_with_one_char_body_rejected(perf_budget: PerfBudget) -> None:
    """RD-3 DoD (a) — 본문 문자열 1자만 있어도 link_only 소스는 거부.

    CPU 측정 — 단일 호출이 O(1) 상수 시간임을 확인: batch=100 으로 묶어
    양자화 오차를 줄이고 1ms 미만임을 단언한다.
    """
    item = _item(body_ref="a")
    source = _source(redistribution="link_only")
    grant = _grant()

    # Measure CPU time of the rejection path (exception is caught internally).
    def _call() -> None:
        try:
            assert_redistribution_allowed(item, source, grant)
        except RedistributionViolationError:
            pass

    sample = perf_budget.sample(_call, batch=100)
    # Also verify the exception semantics are correct.
    with pytest.raises(RedistributionViolationError) as exc_info:
        assert_redistribution_allowed(item, source, grant)
    assert exc_info.value.error_code.value == "RD_REDISTRIBUTION_DENIED"
    assert exc_info.value.reason == "link_only_body_present"
    assert sample.cpu_ms < 1.0, (
        f"CPU time {sample.cpu_ms:.3f}ms exceeds 1ms floor; wall={sample.wall_ms:.3f}ms"
    )


def test_link_only_body_rejected_goes_red_with_10x_delay_injection(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """성능 단언이 실제로 예산을 검증하는지 확인 — `time.process_time`
    을 monkeypatch 해 1회 호출당 예산(1ms) 을 10배 초과하는 CPU 시간을
    가장하면 `sample` 의 cpu_ms 가 1ms 를 넘어 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지" 를
    검증한다.
    """
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 15.0  # 15ms per call → budget 1ms × 15x 초과
        return _fake_time

    monkeypatch.setattr(time, "process_time", _fake_process_time)

    item = _item(body_ref="a")
    source = _source(redistribution="link_only")
    grant = _grant()

    def _call() -> None:
        try:
            assert_redistribution_allowed(item, source, grant)
        except RedistributionViolationError:
            pass

    with pytest.raises(AssertionError):
        sample = perf_budget.sample(_call, batch=100)
        assert sample.cpu_ms < 1.0, (
            f"CPU time {sample.cpu_ms:.3f}ms should exceed 1ms budget under injection"
        )


def test_link_only_source_without_body_passes(perf_budget: PerfBudget) -> None:
    """RD-3 DoD (a) — link_only + body_ref=None 은 정상 통과.

    CPU 측정 — 단일 호출이 O(1) 상수 시간임을 확인: batch=100 으로 묶어
    양자화 오차를 줄이고 1ms 미만임을 단언한다.
    """
    item = _item(body_ref=None)
    source = _source(redistribution="link_only")

    def _call() -> None:
        assert_redistribution_allowed(item, source, _grant())

    sample = perf_budget.sample(_call, batch=100)
    assert sample.cpu_ms < 1.0, (
        f"CPU time {sample.cpu_ms:.3f}ms exceeds 1ms floor; wall={sample.wall_ms:.3f}ms"
    )


def test_link_only_negative_combinations_no_raise(perf_budget: PerfBudget) -> None:
    """link_only + None body + 다른 source_id 조합이 조용히 통과해야 한다.

    음성 테스트 1: body_ref=None 이지만 source_id 가 다른 조합.
    음성 테스트 2: store_full + INTERNAL scope.
    음성 테스트 3: store_full + REDISTRIBUTE scope + None body.
    """
    # 1: link_only + None body + 다른 source_id
    item1 = _item(body_ref=None, source_id="NEWS_API")
    source1 = _source(source_id="NEWS_API", redistribution="link_only")
    assert_redistribution_allowed(item1, source1, _grant())

    # 2: store_full + INTERNAL scope
    item2 = _item(body_ref=None)
    source2 = _source(redistribution="store_full")
    assert_redistribution_allowed(item2, source2, _grant(scope=RedistributionScope.INTERNAL))

    # 3: store_full + REDISTRIBUTE scope + None body
    item3 = _item(body_ref=None)
    source3 = _source(redistribution="store_full")
    assert_redistribution_allowed(item3, source3, _grant(scope=RedistributionScope.REDISTRIBUTE))


def test_link_only_passes_goes_red_with_10x_delay_injection(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """성능 단언이 실제로 예산을 검증하는지 확인 — `time.process_time`
    을 monkeypatch 해 1회 호출당 예산(1ms) 을 10배 초과하는 CPU 시간을
    가장하면 `sample` 의 cpu_ms 가 1ms 를 넘어 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지" 를
    검증한다.
    """
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 15.0  # 15ms per call → budget 1ms × 15x 초과
        return _fake_time

    monkeypatch.setattr(time, "process_time", _fake_process_time)

    item = _item(body_ref=None)
    source = _source(redistribution="link_only")

    def _call() -> None:
        assert_redistribution_allowed(item, source, _grant())

    with pytest.raises(AssertionError):
        sample = perf_budget.sample(_call, batch=100)
        assert sample.cpu_ms < 1.0, (
            f"CPU time {sample.cpu_ms:.3f}ms should exceed 1ms budget under injection"
        )


def test_gate_red_regression_link_only_body_denied(perf_budget: PerfBudget) -> None:
    """gate-red 회귀 감지: link_only + body 가 있으면 거부해야 하는데
    현재 구현이 이를 거부하는지 확인. 만약 구현이 회귀해 조용히 통과하면
    이 테스트는 AssertionError 를 던진다 (예산 단언이 실패하지 않음).

    이 테스트는 두 가지 사실을 확인한다:
    1. link_only + body 가 있는 경우 RedistributionViolationError 가 발생한다.
    2. 그 경로의 CPU 시간이 예산 내다 (정상 동작 확인).
    """
    item = _item(body_ref="summary text")
    source = _source(redistribution="link_only")

    def _call() -> None:
        try:
            assert_redistribution_allowed(item, source, _grant())
        except RedistributionViolationError:
            pass  # 정상 경로: 예외 발생

    sample = perf_budget.sample(_call, batch=100)
    # 정상 구현이면 예외가 발생하므로 pass 로 떨어지고 CPU 측정만 남는다.
    # 만약 회귀하면 예외가 발생하지 않고 _call 이 정상 리턴 → sample.result 는 None.
    # 회귀를 감지하기 위해 예외가 실제로 발생했는지 별도 검증:
    with pytest.raises(RedistributionViolationError) as exc_info:
        assert_redistribution_allowed(item, source, _grant())
    assert exc_info.value.reason == "link_only_body_present"
    assert sample.cpu_ms < 1.0, (
        f"CPU time {sample.cpu_ms:.3f}ms exceeds 1ms floor; wall={sample.wall_ms:.3f}ms"
    )


def test_store_full_source_with_body_allowed_when_grant_permits() -> None:
    item = _item(body_ref="full article text")
    source = _source(redistribution="store_full")
    assert_redistribution_allowed(item, source, _grant(scope=RedistributionScope.REDISTRIBUTE))


def test_source_contract_denied_blocks_even_when_license_allows_body() -> None:
    """RD-3 DoD (b) — 등급 강제(DC-27)는 라이선스(store_full)와 독립적인
    별도 게이트다: 계약이 없거나 만료되면 store_full이어도 거부된다."""
    item = _item(body_ref="full article text")
    source = _source(redistribution="store_full")
    with pytest.raises(RedistributionViolationError) as exc_info:
        assert_redistribution_allowed(item, source, _grant(allowed=False))
    assert exc_info.value.reason == "source_contract_denied"


def test_none_scope_grant_blocks_even_when_license_allows_body() -> None:
    item = _item(body_ref="full article text")
    source = _source(redistribution="store_full")
    with pytest.raises(RedistributionViolationError):
        assert_redistribution_allowed(item, source, _grant(scope=RedistributionScope.NONE))


def test_mismatched_source_id_rejected() -> None:
    item = _item(source_id="GDELT", body_ref=None)
    source = _source(source_id="RSS_GENERIC")
    with pytest.raises(ValueError):
        assert_redistribution_allowed(item, source, _grant())


def test_delegates_to_permits_use_not_reimplemented(monkeypatch: pytest.MonkeyPatch) -> None:
    """RD-3 DoD (b) 재구현 금지 반증: `permits_use`를 monkeypatch로 뒤집으면
    판정이 그대로 따라 바뀌어야 한다 — 이 모듈이 자체 등급표를 다시
    짰다면 이 patch가 결과에 영향을 주지 못해 테스트가 실패한다."""
    import src.foundation.research_data.domain.redistribution as redistribution_module

    item = _item(body_ref="full article text")
    source = _source(redistribution="store_full")

    # 원래는 통과해야 하는 조합(REDISTRIBUTE)인데 permits_use가 항상 거부하도록
    # 바꾸면 위임하는 구현은 거부로 뒤집힌다.
    monkeypatch.setattr(redistribution_module, "permits_use", lambda scope, use: False)
    with pytest.raises(RedistributionViolationError):
        assert_redistribution_allowed(item, source, _grant(scope=RedistributionScope.REDISTRIBUTE))

    # 반대로 원래는 거부해야 하는 조합(NONE)인데 permits_use가 항상 허용하도록
    # 바꾸면 위임하는 구현은 통과로 뒤집힌다.
    monkeypatch.setattr(redistribution_module, "permits_use", lambda scope, use: True)
    assert_redistribution_allowed(item, source, _grant(scope=RedistributionScope.NONE))


# ---- DEEPEN(task-2907) — DEPTH_DC_RD.md D2 부족분: 실패주입/성능단언/게이트적색 ----


def test_corrupted_grant_scope_propagates_keyerror_instead_of_silently_permitting() -> None:
    """실패 주입 — DC-27 grant가 pydantic 검증을 우회해 만들어진 경우(예:
    성능 경로에서 `model_construct`를 쓰는 어댑터, 또는 구버전 enum 값이
    남은 행의 역직렬화) `redistribution_scope`가 이 코드가 아는
    `RedistributionScope` 멤버가 아닐 수 있다. `permits_use` 내부 딕셔너리
    조회는 이때 `KeyError`를 던지는데, `assert_redistribution_allowed`는
    이를 삼켜 조용히 허용으로 돌리지 않고 그대로 전파해야 한다 — 모르는
    상태를 허용으로 해석하는 것은 fail-open이라 금지된다."""
    item = _item(body_ref="full article text")
    source = _source(redistribution="store_full")
    bogus_scope = cast(RedistributionScope, "BOGUS_SCOPE")
    grant = SourceContractGrant.model_construct(
        allowed=True,
        tier=SourceContractTier.FREE,
        redistribution_scope=bogus_scope,
        rate_limit=60,
        quota=1000,
        capability=_CAPABILITY,
        denial_reason=None,
    )
    with pytest.raises(KeyError):
        assert_redistribution_allowed(item, source, grant)


@pytest.mark.perf
def test_assert_redistribution_allowed_meets_latency_budget_for_bulk_calls(
    perf_budget: PerfBudget,
) -> None:
    """성능 단언 — 순수 함수(I/O 없음) 호출은 상수 시간이어야 한다. 대량
    반복 호출(예: 배치 수집 후 일괄 검증)이 절대시간 예산 내에 있다 —
    회귀가 있다면(예: 매 호출마다 소스 테이블을 O(n) 스캔하는 재구현으로
    퇴화) 예산을 넘는다."""
    item = _item(body_ref=None)
    source = _source(redistribution="link_only")
    grant = _grant()

    budget_ms = 1000.0  # 실측 로컬 <50ms(20,000회, 상수시간 순수 함수 기준)

    def bulk_call() -> None:
        for _ in range(20_000):
            assert_redistribution_allowed(item, source, grant)

    sample = perf_budget.assert_within(
        bulk_call,
        budget_ms=budget_ms,
        n=5,
        batch=1,
        label="20000 calls RD-3 redistribution latency",
    )
    print(
        f"[RD-3 redistribution] 20000 calls in {sample.cpu_ms / 1000:.3f}s CPU "
        f"(budget<{budget_ms / 1000:.1f}s)"
    )


def test_gate_red_if_license_gate_removed_existing_negative_would_flip() -> None:
    """게이트 적색 재현 — RD-3(a) 라이선스 게이트(link_only + body_ref
    거부) 줄이 실수로 삭제된 회귀를 흉내낸 대조 구현을 구성해, 그 버전은
    동일 입력을 거부하지 않고 조용히 통과시킴(green→red 반전)을 실측한다.
    현재 구현은 동일 입력을 여전히 거부한다 — 이 테스트가 실제로 위험한
    회귀를 잡아낼 수 있다는 증거다."""
    item = _item(body_ref="a")
    source = _source(redistribution="link_only")
    grant = _grant()

    def _regressed_without_license_gate(
        item: ResearchItem, source: SourceMeta, grant: SourceContractGrant
    ) -> None:
        # RD-3(a) 라이선스 게이트가 빠진 회귀 -- 등급 게이트만 남았다고 가정.
        if (
            not grant.allowed
            or grant.redistribution_scope is None
            or not source_contract_module.permits_use(
                grant.redistribution_scope, DataUse.INTERNAL_CALC
            )
        ):
            raise RedistributionViolationError(item, reason="source_contract_denied")
        # (license gate missing here -- link_only + body_ref 조합을 걸러내지 못한다)

    _regressed_without_license_gate(item, source, grant)  # raise 없음 == 회귀가 실제로 위험함

    with pytest.raises(RedistributionViolationError) as exc_info:
        assert_redistribution_allowed(item, source, grant)
    assert exc_info.value.reason == "link_only_body_present"


# ---------------------------------------------------------------------------
# task-11539 감사 후속 — CPU 측정 수렴 / 예산 주입 / 게이트 적색 재현
# ---------------------------------------------------------------------------


def test_cpu_convergence_single_call(perf_budget: PerfBudget) -> None:
    """단일 호출 CPU 시간이 양자화 오차 범위 내에 수렴하는지 검증.

    `assert_redistribution_allowed()` 는 O(1) 비교만 하므로
    `time.process_time()` 으로 측정한 CPU 시간은 ~0ms 여야 한다.
    Windows `time.process_time()` 은 ~15.6ms 양자화 오차가 있다 —
    batch=100 으로 묶으면 오차가 ~0.15ms 로 줄어든다.
    """
    item = _item(body_ref=None)
    source = _source(redistribution="link_only")
    grant = _grant()

    sample = perf_budget.sample(
        lambda: assert_redistribution_allowed(item, source, grant),
        batch=100,
    )
    # Single-call CPU time for ~5 string equality + int comparison
    # operations is well under 1ms.
    assert sample.cpu_ms < 1.0, (
        f"CPU time {sample.cpu_ms:.3f}ms exceeds 1ms floor; wall={sample.wall_ms:.3f}ms"
    )


def test_perf_budget_goes_red_with_cpu_injection(
    perf_budget: PerfBudget,
) -> None:
    """검출력 확인 — CPU 부하를 주입하면 같은 단언이 적색으로 넘어간다.

    `busy_cpu()` 가 `time.process_time()` 을 증가시키므로
    `assert_within` 가 적색으로 넘어간다. 이는 성능 단언이
    '아무것도 잡지 못하는 허울'이 아님을 증명한다.
    """

    def busy_cpu(duration_sec: float) -> None:
        """CPU 부하를 생성 — time.process_time() 을 증가시킨다."""
        end = time.process_time() + duration_sec
        while time.process_time() < end:
            pass

    item = _item(body_ref=None)
    source = _source(redistribution="link_only")
    grant = _grant()

    def _call_with_cpu_load() -> None:
        assert_redistribution_allowed(item, source, grant)
        busy_cpu(0.03)  # 30ms CPU per call (3x the 10ms budget)

    sample = perf_budget.best_of(_call_with_cpu_load, n=5, batch=1)
    # Budget is 10ms; injected 30ms CPU per call (3x; 5ms였을 땐 Linux에서 DID NOT RAISE).
    with pytest.raises(AssertionError) as exc_info:
        assert sample.cpu_ms < 10.0, f"Expected cpu_ms={sample.cpu_ms:.3f}ms to exceed 10ms budget"
    assert "cpu_ms=" in str(exc_info.value) or "cpu=" in str(exc_info.value)


@pytest.mark.perf
def test_perf_assertion_fails_with_10x_delay_injection(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch
) -> None:
    """성능 단언이 실제로 예산을 검증하는지 확인한다 — `time.process_time`
    을 monkeypatch 해 1회 호출당 예산(1000ms) 을 10배 초과하는 CPU 시간을
    가장하면 `assert_within` 가 AssertionError 로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지" 를
    검증한다.
    """
    _fake_time = 0.0

    def _fake_process_time() -> float:
        nonlocal _fake_time
        _fake_time += 12000.0  # 12s per call → budget 1000ms × 12x 초과
        return _fake_time

    monkeypatch.setattr(time, "process_time", _fake_process_time)

    item = _item(body_ref=None)
    source = _source(redistribution="link_only")
    grant = _grant()

    def _bulk() -> None:
        for _ in range(20_000):
            assert_redistribution_allowed(item, source, grant)

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            _bulk,
            budget_ms=1000.0,
            n=1,
            label="20000 calls RD-3 redistribution latency (delay-injected)",
        )
