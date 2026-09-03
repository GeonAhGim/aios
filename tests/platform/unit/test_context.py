import asyncio
import uuid

import pytest
from pydantic import ValidationError

from src.core.logging.request_context import request_id_var
from src.core.observability.context import bind, bind_system, current


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
