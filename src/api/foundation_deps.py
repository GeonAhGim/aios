"""foundation/* 라우터 공용 의존성 — src/api/suitability_deps.py와 동일 패턴."""
from __future__ import annotations

import asyncpg
from fastapi import Depends

from src.api.deps import get_current_user, get_pool
from src.foundation.trust.adapters.postgres_repository import PostgresTrustRepository
from src.foundation.trust.contracts.v1 import TenantContext
from src.foundation.trust.ports.repository import TrustRepository
from src.services.auth_service import User


def get_trust_repository(pool: asyncpg.Pool = Depends(get_pool)) -> TrustRepository:
    return PostgresTrustRepository(pool)


def get_tenant_context(user: User = Depends(get_current_user)) -> TenantContext:
    """71번 §4 "API body에서 생성 금지" — 게이트웨이 인증(get_current_user)에서만
    발급한다. P0 스콥은 tenant_id == subject_id == user_id다(84b7d0faf14f 마이그레이션
    편차 설명 참조 — organization/household tenant는 아직 없음).

    `mfa_verified`는 계정에 MFA가 켜져 있는지를 그대로 옮긴다 — 로그인 자체가
    mfa_enabled=True인 계정에는 TOTP 통과를 강제하므로(auth_service.py), 유효한
    세션이 있다는 것 자체가 "이 세션은 MFA를 통과했다"는 뜻이다. 민감 커맨드별
    step-up 재인증(73번 §6 규칙 2)은 이 리프의 스콥이 아니다 — 기존
    `reauthenticate()`(deps.py) 패턴을 개별 라우터가 필요할 때 그대로 쓴다."""
    return TenantContext(
        tenant_id=user.user_id,
        subject_id=user.user_id,
        role="OWNER",
        mfa_verified=user.mfa_enabled,
    )
