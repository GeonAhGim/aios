"""라우터 통합테스트용 MFA 시계 이동 — 31초 실시간 sleep 제거.

레드팀 #13(TOTP 재사용 방지) 반영 이후 세 라우터 테스트가 "다음 30초
구간"을 얻기 위해 `asyncio.sleep(31)`을 썼다(CI마다 93초, 공유 DB 오염 창
확대 — 전수감사 §9). `MfaService`는 이미 `now=` 시계 주입을 지원하고
`test_mfa_service.py`는 그것을 쓰고 있었으므로, 라우터 경로에서도 DI
오버라이드로 같은 시계를 주입한다. `get_auth_service`는 `get_mfa_service`에
`Depends`로 의존하므로 로그인 경로에도 자동으로 전파된다.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import asyncpg
import pyotp
from fastapi import Depends, FastAPI, Request

from src.api.deps import get_mfa_service, get_pool
from src.services.mfa_service import MfaService

Now = Callable[[], datetime]


@contextmanager
def mfa_clock_shifted(app: FastAPI, seconds: int) -> Iterator[Now]:
    """블록 안에서 앱의 MfaService 시계를 `seconds`만큼 앞당긴다. 반환된
    `now()`로 `totp_at(secret, now())`를 만들면 그 시계 기준 유효 코드가 된다."""
    offset = timedelta(seconds=seconds)

    def now() -> datetime:
        return datetime.now(timezone.utc) + offset

    async def _shifted_mfa_service(
        request: Request, pool: asyncpg.Pool = Depends(get_pool)
    ) -> MfaService:
        secrets = request.app.state.secrets
        return MfaService(
            pool, encryption_key=secrets.credential_encryption_key.get_secret_value(), now=now
        )

    previous = app.dependency_overrides.get(get_mfa_service)
    app.dependency_overrides[get_mfa_service] = _shifted_mfa_service
    try:
        yield now
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_mfa_service, None)
        else:
            app.dependency_overrides[get_mfa_service] = previous


def totp_at(secret: str, when: datetime) -> str:
    return pyotp.totp.TOTP(secret).at(when)


@contextmanager
def mfa_clock_frozen(app: FastAPI, when: datetime) -> Iterator[None]:
    """앱의 MfaService 시계를 `when` 한 값으로 고정한다.

    `mfa_clock_shifted`는 매 호출마다 실시간을 다시 읽어 오프셋만 더하므로
    "코드 생성"과 "서버 검증" 사이에 실제 30초 TOTP 구간 경계를 넘으면
    (esc-ci-cbb8b9c62497) 같은 코드가 우연히 무효 처리될 수 있다 — 이
    헬퍼는 실시간을 아예 참조하지 않아 그 레이스를 구조적으로 없앤다.
    """

    async def _frozen_mfa_service(
        request: Request, pool: asyncpg.Pool = Depends(get_pool)
    ) -> MfaService:
        secrets = request.app.state.secrets
        return MfaService(
            pool,
            encryption_key=secrets.credential_encryption_key.get_secret_value(),
            now=lambda: when,
        )

    previous = app.dependency_overrides.get(get_mfa_service)
    app.dependency_overrides[get_mfa_service] = _frozen_mfa_service
    try:
        yield
    finally:
        if previous is None:
            app.dependency_overrides.pop(get_mfa_service, None)
        else:
            app.dependency_overrides[get_mfa_service] = previous
