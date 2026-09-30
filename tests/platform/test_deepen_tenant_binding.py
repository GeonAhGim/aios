"""tests/platform 패키지 경계 DEEPEN(task-9848, 원 리프 task-6704, 고아 산출물 회수
5828 qa-2) — `src/core/observability/tenant_binding.py`의 `rebind_tenant`와 그
입력 계약인 `TenantContext`(`src/foundation/trust/contracts/v1.py`) 테스트.

task-8658 선례: pytest 기본 `python_files`(=`test_*.py`)는 `__init__.py`를 test
모듈로 수집하지 않는다 — 여기 테스트를 `tests/platform/__init__.py`가 아니라 이
파일에 둔다. `rebind_tenant`는 저장소 전역에서 이제까지 단위 테스트가 없던 순수
in-process 함수(DB 없음)라 이 경로에 적합하다.
"""

from __future__ import annotations

import time
import uuid
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.core.observability import context
from src.core.observability.metric_names import AUTH_TENANT_MISMATCH_COUNT_TOTAL
from src.core.observability.metrics import NullMetrics, set_metrics
from src.core.observability.tenant_binding import rebind_tenant
from src.foundation.trust.contracts.v1 import TenantContext

_PRETRADE_GATE_P99_BUDGET_SEC = (
    0.005  # ADR-2026-09-09-C Decision 1 차용 (test_context.py와 동일 근거)
)


def _tenant_ctx(**overrides: object) -> TenantContext:
    """의도적으로 잘못된 타입/누락 필드를 주입하는 negative 테스트를 위해
    `cast`로 정적 타입 검사를 우회한다(런타임 pydantic 검증이 실제 방어선이다) --
    task-9847 선례를 따라 `type: ignore` 대신 `cast`를 쓴다(type_ignore 예산 게이트,
    pre_push_gate.py)."""
    defaults: dict[str, object] = dict(
        tenant_id=uuid.uuid4(),
        subject_id=uuid.uuid4(),
        mfa_verified=True,
    )
    defaults.update(overrides)
    return TenantContext(**cast("dict[str, Any]", defaults))


# ---------------------------------------------------------------------------
# Negative tests -- TenantContext(rebind_tenant의 입력 계약) 불변식 위반 거부
# ---------------------------------------------------------------------------


def test_tenant_context_rejects_missing_mfa_verified() -> None:
    """negative -- `mfa_verified`는 필수 필드(기본값 없음)다. 누락되면
    호출자가 MFA 상태를 안 거친 요청을 검증된 것으로 착각할 수 있으므로
    조용히 기본값을 채우지 않고 명시적으로 거부해야 한다."""
    with pytest.raises(ValidationError):
        TenantContext(
            **cast("dict[str, Any]", dict(tenant_id=uuid.uuid4(), subject_id=uuid.uuid4()))
        )


def test_tenant_context_rejects_invalid_tenant_id_type() -> None:
    """negative -- `tenant_id`는 UUID여야 한다. 임의 문자열이 통과하면
    `rebind_tenant`가 잘못된 값을 컨텍스트에 그대로 바인딩해 감사/격리
    경계가 깨진다."""
    with pytest.raises(ValidationError):
        _tenant_ctx(tenant_id="not-a-uuid")


def test_tenant_context_rejects_invalid_subject_id_type() -> None:
    """negative -- `subject_id`도 UUID여야 한다. 타입 검증이 없으면
    `rebind_tenant`가 설정하는 `actor_subject_id`가 오염된다."""
    with pytest.raises(ValidationError):
        _tenant_ctx(subject_id=12345)


# ---------------------------------------------------------------------------
# 실패주입 -- metrics().counter() 예외가 삼켜지지 않고 전파되는지
# ---------------------------------------------------------------------------


def test_rebind_tenant_propagates_metrics_counter_failure_on_mismatch() -> None:
    """실패주입 -- 불일치 감지 시 `rebind_tenant`는 `metrics().counter(...)`를
    직접 호출한다(safe_counter를 쓰지 않음). 이 호출이 예외를 던지면
    fail-closed로 그대로 전파되어야 한다 -- 조용히 삼키면 불일치 계측 자체가
    유실되고도 정상 처리로 보인다(관측 결손 은폐 방지)."""
    broken_metrics = MagicMock()
    broken_metrics.counter.side_effect = RuntimeError("injected counter failure")
    set_metrics(broken_metrics)

    original_tenant = uuid.uuid4()
    mismatched_ctx = _tenant_ctx(tenant_id=uuid.uuid4())
    try:
        with context.bind(tenant_id=original_tenant):
            with pytest.raises(RuntimeError, match="injected counter failure"):
                rebind_tenant(mismatched_ctx)
    finally:
        set_metrics(NullMetrics())

    broken_metrics.counter.assert_called_once_with(AUTH_TENANT_MISMATCH_COUNT_TOTAL)


def test_rebind_tenant_does_not_call_counter_when_no_prior_tenant_bound() -> None:
    """대조군 -- 아직 tenant_id가 바인딩되지 않은 최초 인증 경로에서는
    불일치가 없으므로 counter()가 전혀 호출되지 않아야 한다(오탐 방지)."""
    recording_metrics = MagicMock()
    set_metrics(recording_metrics)

    ctx = _tenant_ctx()
    try:
        with context.bind():
            rebind_tenant(ctx)
            assert context.current().tenant_id == ctx.tenant_id
            assert context.current().actor_subject_id == ctx.subject_id
    finally:
        set_metrics(NullMetrics())

    recording_metrics.counter.assert_not_called()


# ---------------------------------------------------------------------------
# 성능 단언 + 게이트 적색 재현
# ---------------------------------------------------------------------------


def _measure_rebind_cycle(n: int) -> list[float]:
    samples = []
    for _ in range(n):
        ctx = _tenant_ctx()
        with context.bind():
            start = time.perf_counter()
            rebind_tenant(ctx)
            samples.append(time.perf_counter() - start)
    return samples


@pytest.mark.perf
def test_rebind_tenant_meets_pretrade_gate_budget() -> None:
    """수치 성능 단언 -- `rebind_tenant`는 요청당 1회 인증 직후 호출되는
    인프로세스 핫패스다. 예산 근거는 모듈 상단 주석(test_context.py와 동일)."""
    samples = sorted(_measure_rebind_cycle(500))
    p99 = samples[int(len(samples) * 0.99)]

    assert p99 < _PRETRADE_GATE_P99_BUDGET_SEC


@pytest.mark.perf
def test_rebind_tenant_budget_assertion_catches_regression(perf_budget) -> None:
    """게이트 적색 재현 -- `RequestContext.model_copy`(rebind_tenant 내부에서
    새 컨텍스트를 만드는 데 쓰는 호출)에 인위 지연을 주입해 위 p99 예산 단언이
    실제로 AssertionError를 내는지 확인한다(타우톨로지 아님을 증명).
    `ContextVar.set`은 C 레벨 메서드라 인스턴스 속성으로 monkeypatch할 수 없어
    (`AttributeError: attribute 'set' is read-only`), 그 대신 `model_copy`를
    patch 대상으로 쓴다(test_context.py의 `_broken_copy` 패턴과 동일 근거)."""
    real_model_copy = context.RequestContext.model_copy

    def _slow_model_copy(
        self: context.RequestContext, *, update: dict[str, object] | None = None, deep: bool = False
    ) -> context.RequestContext:
        time.sleep(0.01)
        return real_model_copy(self, update=update, deep=deep)

    ctx = _tenant_ctx()
    with context.bind():
        samples = []
        for _ in range(20):
            with pytest.MonkeyPatch.context() as mp:
                mp.setattr(context.RequestContext, "model_copy", _slow_model_copy)
                measured = perf_budget.sample(lambda: rebind_tenant(ctx))
            samples.append(measured.wall_ms / 1000)

    p99 = sorted(samples)[int(len(samples) * 0.99)]
    with pytest.raises(AssertionError):
        assert p99 < _PRETRADE_GATE_P99_BUDGET_SEC
