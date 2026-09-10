"""L4_risk_and_safety_v1.0.md#9 R-44 — recovery_gate.can_reactivate 단위 테스트.

§8 "단위(안전)" 필수 negative: evidence 없음 / cooldown 미달 / approval
만료·미승인 / fresh DENY 각각 거부. 나머지 조건은 전부 통과하는 baseline을
만들어 두고 한 번에 하나씩만 무너뜨려 각 거부 사유를 독립적으로 검증한다.

DEEPEN(task-2827, DEPTH 감사 docs/audit/DEPTH_R_EO.md#1331) D2->D3 증빙:
9개 negative는 탄탄했으나 성능단언·적대적/리플레이/다중 인스턴스 증거가
없어 D3 미달이었다(안전축 R은 D3 하한). 아래에 추가한다: (1) 대량 이력
반복 호출 성능 예산, (2) RecoveryDecision 자체의 위조 방지(ALLOW/DENY
사유 불일치 재구성 시도) 적대적 테스트, (3) 크고 정상인 이력 한가운데
표본 하나만 오염시켜도 거부되는 적대적 배치, (4) 재생(replay) 결정론,
(5) 다중 스레드(다중 인스턴스 시뮬레이션) 동시 호출 교차오염 없음.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.core.risk.decision import RiskOutcome
from src.core.safety.circuit_breaker import CircuitBreakerLevel, CircuitBreakerMetrics
from src.core.safety.recovery_gate import RecoveryDecision, can_reactivate

COOLDOWN_SEC = 3


def _clean_history(n: int = COOLDOWN_SEC) -> list[CircuitBreakerMetrics]:
    return [CircuitBreakerMetrics() for _ in range(n)]


def _base_kwargs() -> dict:
    return dict(
        current_level=CircuitBreakerLevel.HALTED,
        metrics_history=_clean_history(),
        cooldown_sec=COOLDOWN_SEC,
        evidence_ref="evidence://ref-1",
        approval_status="APPROVED",
        fresh_risk_outcome=RiskOutcome.ALLOW,
    )


def test_all_conditions_met_allows() -> None:
    decision = can_reactivate(**_base_kwargs())
    assert decision.outcome == RiskOutcome.ALLOW
    assert decision.reason_code is None


@pytest.mark.parametrize("evidence_ref", [None, ""])
def test_missing_evidence_denies(evidence_ref: str | None) -> None:
    kwargs = _base_kwargs()
    kwargs["evidence_ref"] = evidence_ref
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_EVIDENCE_MISSING"


def test_cooldown_not_met_by_short_history_denies() -> None:
    kwargs = _base_kwargs()
    kwargs["metrics_history"] = _clean_history(COOLDOWN_SEC - 1)
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_COOLDOWN_NOT_MET"


def test_cooldown_not_met_by_degraded_sample_denies() -> None:
    kwargs = _base_kwargs()
    history = _clean_history()
    history[-1] = CircuitBreakerMetrics(api_error_rate_pct=Decimal("0.01"))
    kwargs["metrics_history"] = history
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_COOLDOWN_NOT_MET"


def test_cooldown_not_met_by_unknown_data_delay_denies() -> None:
    """R-43 — data_delay_sec=None("모름")은 baseline이 아니다. 관측이 없는
    표본을 "지연 없음"으로 읽으면 재가동 판정이 fail-open된다."""
    kwargs = _base_kwargs()
    history = _clean_history()
    history[-1] = CircuitBreakerMetrics(data_delay_sec=None)
    kwargs["metrics_history"] = history
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_COOLDOWN_NOT_MET"


def test_cooldown_sec_not_positive_denies() -> None:
    kwargs = _base_kwargs()
    kwargs["cooldown_sec"] = 0
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_COOLDOWN_NOT_MET"


@pytest.mark.parametrize(
    "approval_status", ["EXPIRED", "PENDING", "REJECTED", "CANCELLED", "unknown"]
)
def test_approval_not_approved_denies(approval_status: str) -> None:
    kwargs = _base_kwargs()
    kwargs["approval_status"] = approval_status
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_APPROVAL_NOT_APPROVED"


@pytest.mark.parametrize(
    "outcome",
    [RiskOutcome.DENY, RiskOutcome.REDUCE, RiskOutcome.PAUSE, RiskOutcome.ESCALATE],
)
def test_fresh_risk_outcome_not_allow_denies(outcome: RiskOutcome) -> None:
    kwargs = _base_kwargs()
    kwargs["fresh_risk_outcome"] = outcome
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_FRESH_RISK_DENY"


@pytest.mark.parametrize(
    "current_level",
    [
        CircuitBreakerLevel.NORMAL,
        CircuitBreakerLevel.WARNING,
        CircuitBreakerLevel.RESTRICTED,
    ],
)
def test_non_degraded_level_denies(current_level: CircuitBreakerLevel) -> None:
    kwargs = _base_kwargs()
    kwargs["current_level"] = current_level
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_LEVEL_NOT_DEGRADED"


def test_exact_cooldown_boundary_allows_when_all_clean() -> None:
    kwargs = _base_kwargs()
    kwargs["metrics_history"] = _clean_history(COOLDOWN_SEC)
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.ALLOW


def test_empty_history_never_allows_regardless_of_cooldown() -> None:
    kwargs = _base_kwargs()
    kwargs["metrics_history"] = []
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_COOLDOWN_NOT_MET"


# ---- 적대적(D3) — RecoveryDecision 자체의 위조 방지 ----


def test_deny_without_reason_code_rejected_as_forged_decision() -> None:
    """I2와 동일한 fail-closed 계약 — can_reactivate 호출 경로를 우회해
    RecoveryDecision(outcome=DENY)을 직접 만들어도(사유 없는 거부) 방어선이
    하나 더 있다는 증거(감사로그에 근거 없는 DENY가 남는 것을 막는다)."""
    with pytest.raises(ValidationError):
        RecoveryDecision(outcome=RiskOutcome.DENY)


def test_allow_with_reason_code_rejected_as_forged_decision() -> None:
    """반대 방향 위조 — ALLOW인데 reason_code를 남겨 감사로그에 거부사유가
    실제로 있었던 것처럼 꾸미는 시도도 막는다."""
    with pytest.raises(ValidationError):
        RecoveryDecision(outcome=RiskOutcome.ALLOW, reason_code="SOMETHING")


def test_adversarial_single_tainted_sample_deep_in_large_clean_history_denies() -> None:
    """적대적 배치 — 공격자/오작동 센서가 긴 정상 이력 한가운데 단 하나의
    오염된 표본만 섞어 넣어도(나머지는 전부 baseline) 재가동을 통과시키지
    않는다. 짧은 이력으로만 검증하면 우연히 앞부분만 스캔하고 통과하는
    구현 결함을 놓칠 수 있어, 스캔 순서상 중간 지점에 표본을 심어 전체
    스캔이 실제로 일어남을 증명한다."""
    n = 5000
    history = _clean_history(n)
    history[n // 2] = CircuitBreakerMetrics(order_reject_rate_pct=Decimal("0.001"))
    kwargs = _base_kwargs()
    kwargs["metrics_history"] = history
    kwargs["cooldown_sec"] = n
    decision = can_reactivate(**kwargs)
    assert decision.outcome == RiskOutcome.DENY
    assert decision.reason_code == "RECOVERY_COOLDOWN_NOT_MET"


# ---- 성능 단언(D3) ----


@pytest.mark.perf
def test_can_reactivate_meets_latency_budget_under_repeated_large_history_calls() -> None:
    """순수 Decimal/불리언 비교 조합이라 매우 빨라야 한다 — 절대시간 예산은
    느린 CI 머신을 감안해 넉넉히 잡되(회귀만 잡는 목적), 큰 이력을 반복
    스캔하는 호출이 예산을 넘으면 baseline 스캔 비용이 O(n)에서 퇴화했다는
    신호다."""
    iterations = 300
    large_cooldown = 2000
    kwargs = _base_kwargs()
    kwargs["metrics_history"] = _clean_history(large_cooldown)
    kwargs["cooldown_sec"] = large_cooldown
    budget_sec = 2.0

    start = time.perf_counter()
    for _ in range(iterations):
        decision = can_reactivate(**kwargs)
    elapsed = time.perf_counter() - start

    assert decision.outcome == RiskOutcome.ALLOW
    assert elapsed < budget_sec, (
        f"can_reactivate {iterations}x(history={large_cooldown}) 가 예산"
        f"({budget_sec}s)을 넘었습니다({elapsed:.3f}s) — baseline 스캔 비용 "
        "회귀 확인 필요."
    )


# ---- 리플레이 결정론 + 동시 다중 인스턴스(D3) ----


def test_replay_is_deterministic_for_allow_and_deny() -> None:
    """같은 입력으로 반복 호출해도 완전히 동일한 RecoveryDecision이 나온다 —
    숨은 시계·난수·전역 가변 상태가 없다는 재생(replay) 안전성 증거(순수
    함수 계약)."""
    for kwargs in (_base_kwargs(), {**_base_kwargs(), "evidence_ref": None}):
        first = can_reactivate(**kwargs)
        second = can_reactivate(**kwargs)
        assert first == second


def test_concurrent_instances_do_not_cross_contaminate() -> None:
    """서로 다른 입력을 가진 다중 워커(스레드 — 여러 프로세스/엔진 인스턴스가
    동시에 재가동을 판정하는 상황의 시뮬레이션)가 같은 순수 함수를 동시에
    호출해도 서로의 결과를 오염시키지 않는다 — 모듈 레벨 가변 상태가 없다는
    동시성 증거(D3 다중 인스턴스 증명)."""

    def _run(i: int) -> tuple[int, RiskOutcome]:
        kwargs = _base_kwargs()
        if i % 2 == 1:
            kwargs["evidence_ref"] = None  # 홀수 인덱스는 DENY 경로
        decision = can_reactivate(**kwargs)
        return i, decision.outcome

    indices = list(range(80)) * 4  # 320회 동시 호출, 서로 다른 입력이 반복 교차
    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_run, indices))

    assert len(results) == len(indices)
    for i, outcome in results:
        expected = RiskOutcome.DENY if i % 2 == 1 else RiskOutcome.ALLOW
        assert outcome == expected, f"i={i} 스레드가 다른 입력의 결과와 섞였다: {outcome}"
