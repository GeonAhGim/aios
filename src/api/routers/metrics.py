"""L0-5/PLT-09 — Prometheus 텍스트 노출 형식 `/metrics` 엔드포인트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 L0-5,
docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-09.

`MetricsRegistry.render_text()`(L0-1, src/core/observability/metrics_registry.py)를
그대로 응답 본문으로 반환한다 — 이 라우터에는 관측 로직이 없다.

PLT-09: `AIOS_METRICS_TOKEN` 환경변수를 fail-closed 토큰으로 강제한다 —
env가 비어 있거나(운영 배포 전 설정 누락) `X-Metrics-Token` 헤더가 없거나
값이 다르면 403. 매 요청마다 `os.environ`에서 다시 읽는다(재기동 없이 값이
바뀌는 배포 스크립트를 가정하지 않지만, 프로세스 시작 시 캐싱하면 테스트가
매번 재기동해야 하므로 그러지 않는다). `hmac.compare_digest`로 비교해 타이밍
사이드채널을 막고, 토큰 값 자체는 로그·응답 어디에도 싣지 않는다(PLT-02
레닥션).
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
