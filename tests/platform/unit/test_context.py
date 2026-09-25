import asyncio
import time
import uuid
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from src.core.logging.request_context import request_id_var
from src.core.observability.context import RequestContext, bind, bind_system, current

# ADR-2026-09-09-C Decision 1 예산표에 contextvar 전파 전용 항목은 없다. bind_system()은
# 매 백그라운드 루프 tick(또는 매 요청, bind() 경유)마다 I/O 없이 도는 인프로세스 핫패스라
# 가장 근접한 항목인 "사전거래 게이트 p99 5ms"(§Decision 1)를 차용한다.
_PRETRADE_GATE_P99_BUDGET_SEC = 0.005


def _measure_bind_system_cycle(n: int) -> list[float]:
    samples = []
    for _ in range(n):
        start = time.perf_counter()
        with bind_system("perf.probe"):
            current()
        samples.append(time.perf_counter() - start)
    return samples


def test_current_without_bind_returns_system_default():
    ctx = current()

    assert ctx.actor_subject_id == "system"
    assert ctx.component == "api.gateway"
    assert ctx.tenant_id is None
    assert ctx.schema_version == "v1"


def test_current_without_bind_is_not_cached_across_calls():
    """바인딩 전 current()는 매 호출마다 새 임시 컨텍스트를 만든다 — 서로 무관한
    두 호출이 같은 trace_id를 공유하면(캐싱) 관측되지 않은 요청들이 잘못
    상관관계로 묶인다."""
    first = current()
    second = current()

    assert first.trace_id != second.trace_id


def test_bind_overrides_given_fields_only():
    tenant_id = uuid.uuid4()
    with bind(tenant_id=tenant_id, component="api.executions") as ctx:
        assert ctx.tenant_id == tenant_id
        assert ctx.component == "api.executions"
        assert ctx.actor_subject_id == "system"
        assert current() is ctx


def test_bind_restores_previous_context_on_exit():
    before = current()
    with bind(component="api.executions"):
        pass
    after = current()

    assert after.component == before.component == "api.gateway"
    assert after.trace_id != before.trace_id  # 둘 다 미바인딩 임시값 — 우연 일치 아님 확인


def test_bind_sets_request_id_var_and_resets_it():
    assert request_id_var.get() is None

    with bind(request_id="req-fixed-1") as ctx:
        assert request_id_var.get() == "req-fixed-1"
        assert ctx.request_id == "req-fixed-1"

    assert request_id_var.get() is None


def test_nested_bind_inherits_unoverridden_fields():
    """상속 케이스 — 안쪽 bind가 component만 덮어써도 바깥 bind에서 설정한
    tenant_id는 그대로 이어받는다."""
    tenant_id = uuid.uuid4()
    with bind(tenant_id=tenant_id) as outer:
        with bind(component="api.orders") as inner:
            assert inner.tenant_id == tenant_id
            assert inner.trace_id == outer.trace_id
            assert inner.component == "api.orders"
        assert current().component == outer.component


def test_bind_system_ignores_parent_context():
    """비상속 케이스 — bind_system은 상위 요청 컨텍스트의 tenant_id/trace_id를
    물려받지 않고 완전히 새 컨텍스트를 만든다(루프 tick 격리)."""
    tenant_id = uuid.uuid4()
    with bind(tenant_id=tenant_id, trace_id=uuid.uuid4()) as outer:
        with bind_system("safety.watchdog") as sys_ctx:
            assert sys_ctx.tenant_id is None
            assert sys_ctx.actor_subject_id == "system"
            assert sys_ctx.component == "safety.watchdog"
            assert sys_ctx.trace_id != outer.trace_id
        assert current().tenant_id == tenant_id


async def test_asyncio_task_inherits_bound_context():
    """상속 케이스 — asyncio.create_task는 생성 시점 컨텍스트를 복제하므로,
    바인딩된 trace_id가 하위 태스크에도 그대로 전파된다."""
    captured: dict[str, uuid.UUID] = {}

    async def _child() -> None:
        captured["trace_id"] = current().trace_id

    with bind(trace_id=uuid.uuid4()) as ctx:
        task = asyncio.create_task(_child())
        await task

    assert captured["trace_id"] == ctx.trace_id


def test_request_context_is_frozen():
    ctx = current()

    with pytest.raises(ValidationError):
        ctx.tenant_id = uuid.uuid4()  # type: ignore[misc]


def test_bind_exception_still_restores_context():
    before = current()

    with pytest.raises(RuntimeError):
        with bind(component="api.will-fail"):
            raise RuntimeError("boom")

    assert current().component == before.component
    assert request_id_var.get() is None


def test_request_context_rejects_invalid_actor_subject_id():
    """negative — actor_subject_id는 UUID이거나 리터럴 "system"이어야 한다. 이 두 형태를
    벗어난 값(예: 임의 문자열)이 검증 없이 통과하면 감사 로그의 행위자 식별이 깨진다."""
    with pytest.raises(ValidationError):
        RequestContext(
            trace_id=uuid.uuid4(),
            request_id="req-invalid-actor",
            actor_subject_id="not-system-and-not-a-uuid",
        )


def test_request_context_rejects_invalid_schema_version():
    """불변식 위반 입력 — schema_version은 Literal["v1"]여야 한다.
    다른 버전(예: "v2")을 생성하면 ValidationError를 던진다.
    bind()가 model_copy(update=...)로 복사할 때 검증이 우회되므로,
    새 RequestContext 생성 시 불변식이 반드시 걸러지는지를 직접 검증한다.
    이는 버전 마이그레이션 중 새 컨텍스트 생성을 막는 불변식이다."""
    with pytest.raises(ValidationError):
        RequestContext(
            trace_id=uuid.uuid4(),
            request_id="test",
            schema_version="v2",
        )


def test_bind_failure_in_component_copy_does_not_leak_partial_context():
    """실패 주입 — bind() 내부에서 model_copy(update=overrides)가 예외를
    던지면 _context_var/request_id_var 어느 쪽도 set되지 않아야 한다.
    fail-closed: 예외가 인자 평가 단계가 아닌 컨텍스트 복사 단계에서 나더라도
    원본 컨텍스트는 변경되지 않는다."""
    before_ctx = current()
    before_rid = request_id_var.get()

    def _broken_copy(self, /, **kwargs):
        raise RuntimeError("model_copy boom")

    with patch.object(RequestContext, "model_copy", _broken_copy):
        with pytest.raises(RuntimeError):
            with bind(component="api.broken"):
                pass

    # 바깥 컨텍스트가 손상되지 않았는지 확인
    assert current().component == before_ctx.component
    assert request_id_var.get() == before_rid


def test_bind_system_failure_during_trace_id_generation_leaves_no_partial_context():
    """실패 주입 — bind_system이 새 trace_id/request_id를 만드는 도중(uuid.uuid4) 예외가
    나면 fail-closed여야 한다: 예외가 인자 평가 단계에서 나므로 `_context_var`/
    `request_id_var` 어느 쪽도 set되지 않은 채 그대로 원복(무영향) 상태로 남아야 한다."""
    before = current()
    assert request_id_var.get() is None

    with patch("src.core.observability.context.uuid.uuid4", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            with bind_system("safety.watchdog"):
                pass

    assert current().component == before.component
    assert request_id_var.get() is None


def test_bind_system_cycle_meets_pretrade_gate_budget():
    """수치 성능 단언 — bind_system()+current() 사이클(트레이스 생성 2회 + contextvar
    set/reset 2쌍)의 p99가 예산 안에 들어오는지 확인한다. 예산 근거는 모듈 상단 주석."""
    samples = sorted(_measure_bind_system_cycle(500))
    p99 = samples[int(len(samples) * 0.99)]

    assert p99 < _PRETRADE_GATE_P99_BUDGET_SEC


def test_bind_system_cycle_budget_assertion_catches_regression():
    """게이트 적색 재현 — uuid.uuid4에 10ms 인위 지연을 주입해 위 p99 예산 단언이 실제로
    AssertionError를 내는지 확인한다(타우톨로지가 아님을 증명)."""
    real_uuid4 = uuid.uuid4

    def _slow_uuid4() -> uuid.UUID:
        time.sleep(0.01)
        return real_uuid4()

    with patch("src.core.observability.context.uuid.uuid4", side_effect=_slow_uuid4):
        samples = sorted(_measure_bind_system_cycle(20))
    p99 = samples[int(len(samples) * 0.99)]

    with pytest.raises(AssertionError):
        assert p99 < _PRETRADE_GATE_P99_BUDGET_SEC
