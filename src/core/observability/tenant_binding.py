"""인증 후 tenant/actor 재바인딩.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A), §9 PLT-05.

`RequestContextMiddleware`는 인증 이전(HTTP 진입 시점)에 바인딩되므로 `tenant_id`를
아직 모른다(§3.1 계약 — 인증 전 None). `get_tenant_context` 의존성이 성공적으로
`TenantContext`를 발급한 직후 이 함수를 호출해 같은 trace_id 위에 tenant_id/
actor_subject_id만 채운다.

`bind()`(contextmanager)와 달리 이 함수는 블록이 끝나도 값을 되돌리지 않는다 —
호출 시점에는 아직 요청이 끝나지 않았고, 실제 복원(reset)은 `RequestContextMiddleware`
가 쥐고 있는 바깥쪽 `with bind(...)` 블록이 요청 종료 시 수행한다. 이 함수가 만든
값은 그 reset이 복원할 "이전 값" 위에 얹히는 한 겹일 뿐이라 안전하다.
"""
from __future__ import annotations

import logging

from src.core.observability import context
from src.core.observability.metric_names import AUTH_TENANT_MISMATCH_COUNT_TOTAL
from src.core.observability.metrics import metrics
from src.foundation.trust.contracts.v1 import TenantContext

logger = logging.getLogger(__name__)


def rebind_tenant(ctx: TenantContext) -> None:
    """이미 바인딩된 trace_id에 다른 tenant_id가 오면(같은 요청 안에서 인증
    의존성이 두 번 이상 다른 tenant를 반환하는 비정상 상황) `tenant_mismatch`
    카운터를 올리고 경고 로그를 남긴다(108 §5-4). 요청 자체는 막지 않는다
    (fail-open — 접근 차단은 인가 계층의 책임, 여기는 관측성뿐)."""
    current_ctx = context.current()
    if current_ctx.tenant_id is not None and current_ctx.tenant_id != ctx.tenant_id:
        metrics().counter(AUTH_TENANT_MISMATCH_COUNT_TOTAL)
        logger.warning(
            "tenant_mismatch",
            extra={
                "event": "tenant_mismatch",
                "payload": {
                    "previous_tenant_id": str(current_ctx.tenant_id),
                    "new_tenant_id": str(ctx.tenant_id),
                },
            },
        )
    context._context_var.set(
        current_ctx.model_copy(
            update={"tenant_id": ctx.tenant_id, "actor_subject_id": ctx.subject_id}
        )
    )
