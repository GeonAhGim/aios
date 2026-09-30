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
import pytest
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


# task-9375: helper contracts are exercised without a database or real-time sleep.
@pytest.mark.parametrize("seconds", ["31", float("nan"), float("inf")])
def test_negative_invalid_shift_preserves_override(seconds: object) -> None:
    """Invalid offsets must fail before modifying application dependencies."""
    app = FastAPI()
    app.dependency_overrides[get_mfa_service] = get_mfa_service
    before = app.dependency_overrides.copy()
    with pytest.raises((TypeError, ValueError, OverflowError)):
        with mfa_clock_shifted(app, seconds):
            pytest.fail("invalid clock offset was accepted")
    assert app.dependency_overrides == before


def test_negative_invalid_totp_secret() -> None:
    """Malformed base32 material must never produce an authentication code."""
    from binascii import Error

    with pytest.raises(Error, match="Non-base32"):
        totp_at("!invalid!", datetime(2026, 1, 1, tzinfo=timezone.utc))


@pytest.mark.parametrize("frozen", [False, True])
@pytest.mark.parametrize("existing", [False, True])
async def test_failure_injection_dependency_restores_override(
    monkeypatch: pytest.MonkeyPatch, frozen: bool, existing: bool,
) -> None:
    """A failed secret provider must propagate and leave no clock override behind."""
    from types import SimpleNamespace

    app = FastAPI()
    if existing:
        app.dependency_overrides[get_mfa_service] = get_mfa_service
    before = app.dependency_overrides.copy()
    secret = SimpleNamespace(get_secret_value=lambda: "unused")
    app.state.secrets = SimpleNamespace(credential_encryption_key=secret)
    failure = RuntimeError("injected secret provider outage")

    def fail_secret() -> str:
        raise failure

    monkeypatch.setattr(secret, "get_secret_value", fail_secret)
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    context = mfa_clock_frozen(app, when) if frozen else mfa_clock_shifted(app, 31)
    with pytest.raises(RuntimeError, match="injected secret provider outage") as caught:
        with context:
            factory = app.dependency_overrides[get_mfa_service]
            await factory(Request({"type": "http", "app": app}), pool=None)
    assert caught.value is failure
    assert app.dependency_overrides == before


async def test_nested_clock_wiring_restores_outer_service() -> None:
    """I-10: resolve the real dependency factory and verify its injected clock."""
    from types import SimpleNamespace

    app = FastAPI()
    app.state.secrets = SimpleNamespace(
        credential_encryption_key=SimpleNamespace(get_secret_value=lambda: "test-only")
    )
    request = Request({"type": "http", "app": app})
    when = datetime(2026, 1, 1, tzinfo=timezone.utc)
    with mfa_clock_shifted(app, 31) as now:
        outer = app.dependency_overrides[get_mfa_service]
        shifted = await outer(request, pool=None)
        assert shifted._now is now
        before = datetime.now(timezone.utc) + timedelta(seconds=31)
        assert before <= now() <= datetime.now(timezone.utc) + timedelta(seconds=31)
        with mfa_clock_frozen(app, when):
            inner = app.dependency_overrides[get_mfa_service]
            service = await inner(request, pool=None)
            assert service._now() == when
            assert service._now().tzinfo is timezone.utc
        assert app.dependency_overrides[get_mfa_service] is outer
    assert get_mfa_service not in app.dependency_overrides


def test_totp_known_vector_and_step_boundary() -> None:
    """RFC 6238 SHA1 vector (six digits), including the next 30-second boundary."""
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    when = datetime.fromtimestamp(59, tz=timezone.utc)
    assert totp_at(secret, when) == "287082"
    assert totp_at(secret, when - timedelta(seconds=29)) == "287082"
    assert totp_at(secret, when + timedelta(seconds=1)) == "359152"
