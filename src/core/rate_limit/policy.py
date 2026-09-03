"""L4 §9 PLT-25 — rate limit 정책 값의 단일 출처.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-25, §10.4

정책(무엇을 얼마나 허용하는가)과 판정 알고리즘(limiter.py)·라우트 매핑
(api/middleware/rate_limit.py)을 분리한다 — 수치 조정은 이 파일만 바꾸면 되고,
버킷 알고리즘이나 라우트→정책 매핑을 건드리지 않는다.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class RateLimitPolicy(BaseModel):
    name: str
    limit: int
    window_seconds: int
    key: Literal["ip", "subject", "tenant"]


POLICIES: dict[str, RateLimitPolicy] = {
    "auth_login": RateLimitPolicy(name="auth_login", limit=10, window_seconds=60, key="ip"),
    "read": RateLimitPolicy(name="read", limit=120, window_seconds=60, key="subject"),
    "mutation": RateLimitPolicy(name="mutation", limit=10, window_seconds=60, key="subject"),
    "admin": RateLimitPolicy(name="admin", limit=30, window_seconds=60, key="tenant"),
    "metrics": RateLimitPolicy(name="metrics", limit=30, window_seconds=60, key="ip"),
}
