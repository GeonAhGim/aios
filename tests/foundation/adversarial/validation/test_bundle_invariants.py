"""Strategy Validation adversarial 테스트 — 번들 I6 불변식(hard_fail_reasons ⇔ FAIL)과
check 결과 라벨 위조 차단. DB 없이 도메인 객체만 검증한다.

task-21915 DEEPEN: 부모 task-7937이 추가한 negative 테스트가 __init__.py에 있었으나
pytest는 __init__.py를 수집하지 않아 실행되지 않았다(b15746a8c가 제거). 같은 검증을
수집되는 sibling 파일로 옮겨 추가한다."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from pydantic import ValidationError

from src.foundation.validation.domain import rules
from src.foundation.validation.domain.check_result import CheckResult
from src.foundation.validation.domain.models import Outcome, ValidationBundle
from tests.support.perf_budget import PerfBudget


def _bundle(outcome: Outcome, hard: tuple[str, ...] = (), obligations: tuple[str, ...] = ()) -> Any:
    return ValidationBundle(
        id=uuid4(),
        artifact_hash="artifact-hash",
        policy_version="vp-v1",
        data_snapshot_hash="snapshot-hash",
        outcome=outcome,
        check_run_ids=(uuid4(),),
        bundle_hash="bundle-hash",
        hard_fail_reasons=hard,
        obligations=obligations,
    )


def _check(
    outcome: Outcome = Outcome.PASS,
    hard: list[str] | None = None,
    obligations: list[str] | None = None,
) -> CheckResult:
    return CheckResult(
        check_type="backtest",
        outcome=outcome,
        metrics={},
        hard_fail_reasons=hard or [],
        obligations=obligations or [],
        result_hash="check-hash",
        policy_version="vp-v1",
    )


@pytest.mark.negative
def test_bundle_pass_with_hard_fail_reason_is_rejected() -> None:
    """불변식 I6(정방향): hard_fail_reasons가 있는데 outcome=PASS인 번들은 만들 수 없다."""
    with pytest.raises(ValueError, match="I6 violation"):
        _bundle(Outcome.PASS, hard=("VALIDATION_OOS_LEAKAGE",))


@pytest.mark.negative
def test_bundle_pass_with_obligations_and_hard_fail_is_rejected() -> None:
    """불변식 I6(정방향): PASS_WITH_OBLIGATIONS 라벨도 hard fail을 숨길 수 없다."""
    with pytest.raises(ValueError, match="I6 violation"):
        _bundle(
            Outcome.PASS_WITH_OBLIGATIONS,
            hard=("BACKTEST_LOOKAHEAD_VIOLATION",),
            obligations=("re-run with fresh snapshot",),
        )


@pytest.mark.negative
def test_bundle_fail_without_recorded_reason_is_rejected() -> None:
    """불변식 I6(역방향): FAIL인데 사유가 비어 있는 번들은 만들 수 없다(코드 전용 검사)."""
    with pytest.raises(ValueError, match="I6 violation"):
        _bundle(Outcome.FAIL, hard=())


@pytest.mark.negative
def test_check_result_rejects_unknown_hard_fail_code() -> None:
    """I-07: 허용 집합 밖의 hard-fail 코드는 CheckResult 생성 시점에 거부되어야 한다."""
    with pytest.raises(ValidationError):
        _check(outcome=Outcome.FAIL, hard=["MADE_UP_SOFT_CODE"])


@pytest.mark.negative
def test_evaluate_bundle_ignores_pass_label_on_hard_failing_check() -> None:
    """불변식 I6: check가 outcome=PASS라고 거짓 라벨을 붙여도 hard_fail_reasons가
    있으면 번들 결과는 FAIL이어야 한다(라벨이 아니라 사유 목록만 신뢰)."""
    results = [
        _check(Outcome.PASS),
        _check(Outcome.PASS, hard=["VALIDATION_OOS_LEAKAGE"]),
    ]

    outcome, _, hard = rules.evaluate_bundle(results)

    assert outcome is Outcome.FAIL
    assert "VALIDATION_OOS_LEAKAGE" in hard


@pytest.mark.failure_injection
def test_evaluate_bundle_propagates_decider_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패 주입: 번들 판정기가 예외를 내면 PASS로 기본값을 내보내지 않고 그대로
    전파해야 한다(fail-closed)."""

    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated decider failure")

    monkeypatch.setattr(rules, "_decide", _boom)

    with pytest.raises(RuntimeError, match="simulated decider failure"):
        rules.evaluate_bundle([_check(Outcome.PASS)])


@pytest.mark.perf
def test_evaluate_bundle_within_cpu_budget(perf_budget: PerfBudget) -> None:
    """성능 단언: 6개 check 번들 판정이 perf_budget 예산(20ms CPU) 안에 끝나야 한다."""
    results = [_check(Outcome.PASS) for _ in range(6)]

    perf_budget.assert_within(
        lambda: rules.evaluate_bundle(results), budget_ms=20.0, label="evaluate_bundle"
    )
