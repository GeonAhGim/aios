"""요청 컨텍스트 ContextVar(8필드) — 미들웨어·이벤트버스·백그라운드 루프 전파.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A), §3.1 PLT-01.

컨텍스트는 함수 시그니처에 주입되지 않고 `ContextVar` 하나로 전파된다(§1-1) — 기존
40여 서비스의 시그니처를 건드리지 않기 위해서다. `bind()`가 값을 설정하는 유일한
지점이고, 로거·감사·메트릭은 `current()`로 읽기만 한다.

`asyncio.create_task`는 생성 시점의 컨텍스트를 복제해 상속하므로(파이썬
contextvars 기본 동작), HTTP 요청 처리 중 만든 하위 태스크는 자동으로 같은
trace_id를 물려받는다. 반대로 백그라운드 루프처럼 상위 요청과 무관하게 도는
코드는 tick마다 `bind_system()`으로 명시적으로 새 컨텍스트를 만들어야
한다 — 그러지 않으면 이전에 그 태스크를 스케줄한 요청의 컨텍스트가 새어
들어온다(§8 표 "요청 컨텍스트" 행).
"""
from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from src.core.logging.request_context import request_id_var


class RequestContext(BaseModel):
    """§3.1 계약. `frozen=True` — 값을 바꾸려면 `bind()`로 새 컨텍스트를 만든다."""

    model_config = ConfigDict(frozen=True)

    trace_id: UUID
    request_id: str
    tenant_id: UUID | None = None
    actor_subject_id: UUID | Literal["system"] = "system"
    command_id: UUID | None = None
    component: str = "api.gateway"
    schema_version: Literal["v1"] = "v1"


_context_var: ContextVar[RequestContext | None] = ContextVar("request_context", default=None)


def _fallback_context() -> RequestContext:
    """아무것도 바인딩되지 않은 상태에서 `current()`가 호출됐을 때의 기본값.

    fail-open — 관측성 결함이 요청 처리를 막지 않는다(§8 "미들웨어 예외" 행과 동일한
    원칙). `request_id_var`에 이미 값이 있으면(타 세션 미들웨어가 먼저 set) 그대로
    이어받아 request_id가 두 축에서 어긋나지 않게 한다.
    """
    return RequestContext(
        trace_id=uuid.uuid4(),
        request_id=request_id_var.get() or uuid.uuid4().hex,
    )


def current() -> RequestContext:
    """현재 컨텍스트. 바인딩된 적이 없으면 새로 만들어 반환한다(저장하지 않음 —
    호출마다 다른 임시값일 수 있으므로 로깅 목적으로만 안전)."""
    ctx = _context_var.get()
    if ctx is None:
        return _fallback_context()
    return ctx


@contextmanager
def bind(**overrides: Any) -> Iterator[RequestContext]:
    """현재 컨텍스트에 `overrides`를 얹은 새 컨텍스트를 이 블록 동안 바인딩한다.

    `request_id_var`도 같은 request_id로 함께 set해, PLT-01 이전부터 있던
    `get_current_request_id()` 소비자(예: `schema.py`)가 계속 일치된 값을 본다.
    """
    new_ctx = current().model_copy(update=overrides) if overrides else current()
    ctx_token = _context_var.set(new_ctx)
    request_id_token = request_id_var.set(new_ctx.request_id)
    try:
        yield new_ctx
    finally:
        request_id_var.reset(request_id_token)
        _context_var.reset(ctx_token)


@contextmanager
def bind_system(component: str) -> Iterator[RequestContext]:
    """백그라운드 루프용 — 상위(요청) 컨텍스트를 물려받지 않고 새 trace_id로
    시스템 컨텍스트를 만든다(actor_subject_id="system", tenant_id=None,
    command_id=None). 루프가 매 tick 호출해야 부모 요청 컨텍스트 누수를 막는다."""
    with bind(
        trace_id=uuid.uuid4(),
        request_id=uuid.uuid4().hex,
        tenant_id=None,
        actor_subject_id="system",
        command_id=None,
        component=component,
    ) as ctx:
        yield ctx
