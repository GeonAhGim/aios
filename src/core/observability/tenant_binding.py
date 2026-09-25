"""Post-auth tenant/actor re-binding.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.1(A), §9 PLT-05.

`RequestContextMiddleware` binds at the HTTP entry point before authentication, so it
does not yet know the `tenant_id` (contract §3.1 — None pre-auth). After
`get_tenant_context` successfully resolves a `TenantContext`, call this function to
populate only tenant_id / actor_subject_id on the same trace_id.

Unlike `bind()` (contextmanager), this function does not restore values when the
block exits — at call time the request is not yet complete, and the actual reset is
performed by the outer `with bind(...)` block held by `RequestContextMiddleware` when
the request finishes. Values created by this function are safe because they sit as
one layer on top of the "previous value" that the reset will restore.
"""
from __future__ import annotations

import logging

from src.core.observability import context
from src.core.observability.metric_names import AUTH_TENANT_MISMATCH_COUNT_TOTAL
from src.core.observability.metrics import metrics
from src.foundation.trust.contracts.v1 import TenantContext

logger = logging.getLogger(__name__)


def rebind_tenant(ctx: TenantContext) -> None:
    """When a different tenant_id arrives on an already-bound trace_id (abnormal: the
    auth dependency returned different tenants more than once within the same request),
    increments the `tenant_mismatch` counter and logs a warning (108 §5-4). Does not
    block the request itself (fail-open — access denial is the authorization layer's
    responsibility; this module is observability only)."""
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
