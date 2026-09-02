"""L0-5 — Prometheus 텍스트 노출 형식 `/metrics` 엔드포인트.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md §9 L0-5.

`MetricsRegistry.render_text()`(L0-1, src/core/observability/metrics_registry.py)를
그대로 응답 본문으로 반환한다 — 이 라우터에는 관측 로직이 없다.

인증 없음: Prometheus 스크레이핑은 보통 클러스터 내부망 전용이다. 이
프로젝트는 아직 스크레이프 네트워크 격리(리버스 프록시/방화벽)를 설정하지
않았다 — 미검증, 운영 배포 전 별도로 확인 필요.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from src.core.observability.metrics_registry import get_registry

router = APIRouter(tags=["metrics"])


@router.get("/metrics")
async def get_metrics() -> PlainTextResponse:
    body = get_registry().render_text()
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4; charset=utf-8")
