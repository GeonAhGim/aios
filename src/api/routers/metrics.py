"""L0-5/PLT-09 — `/metrics` endpoint exposing Prometheus text format.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 L0-5,
docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-09.

Calls `MetricsRegistry.render_text()` (L0-1, src/core/observability/metrics_registry.py)
and returns the result verbatim as the response body — this router contains no
observability logic of its own.

PLT-09: Enforces `AIOS_METRICS_TOKEN` as a fail-closed token via environment variable —
returns 403 when the env var is empty (configuration missing before production deploy),
the `X-Metrics-Token` header is absent, or the values do not match. Re-reads from
`os.environ` on every request (we do not cache at process startup, which would require
a full restart for token rotation; this design assumes no hot-reload deployment scripts,
though it remains compatible with them). Uses `hmac.compare_digest` to prevent timing
side-channel attacks, and never includes the token value itself in logs or responses
(PLT-02 redaction).
"""
from __future__ import annotations

import hmac
import os

from fastapi import APIRouter, Header, HTTPException, status
from fastapi.responses import PlainTextResponse

from src.core.observability.metrics_registry import get_registry

router = APIRouter(tags=["metrics"])


def _require_metrics_token(x_metrics_token: str | None) -> None:
    expected = os.environ.get("AIOS_METRICS_TOKEN")
    if not expected or not x_metrics_token or not hmac.compare_digest(x_metrics_token, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "메트릭 접근이 거부되었습니다.")


@router.get("/metrics")
async def get_metrics(
    x_metrics_token: str | None = Header(default=None, alias="X-Metrics-Token"),
) -> PlainTextResponse:
    _require_metrics_token(x_metrics_token)
    body = get_registry().render_text()
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")
