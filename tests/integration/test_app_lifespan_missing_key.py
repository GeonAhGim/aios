"""lifespan negative test: missing credential encryption key (split from test_app_lifespan.py)."""

from __future__ import annotations

from unittest.mock import patch

import pytest


async def test_lifespan_rejects_missing_credential_encryption_key() -> None:
    """불변식 위반 입력: credential encryption key가 비어 있으면 lifespan이
    거부해야 한다. key_ring.from_legacy_hex()가 빈 키를 받지 않도록
    방어한다.

    P6 위반 해소: src/main.py — 예외 범위를 ValueError에서 KeyRingConfigError로
    좁히고, pool 생성 이후 실패한 경우에도(pool은 이미 만들어졌다) finally
    블록이 정상 실행되어 pool.close()가 호출되고 app.state에 pool/event_bus가
    부분 등록되지 않음을 확인한다.
    """
    from pydantic import SecretStr

    from src.data.models.trading import SecretBundle

    def _empty_key_secrets() -> SecretBundle:
        return SecretBundle(
            database_url=SecretStr("postgresql://x"),
            jwt_secret_key=SecretStr("x"),
            credential_encryption_key=SecretStr(""),
            # 빈 키 → _decode_key에서 0바이트 → KeyRingConfigError
            bitget_api_key=SecretStr("x"),
            bitget_api_secret=SecretStr("x"),
            kis_app_key=SecretStr("x"),
            kis_app_secret=SecretStr("x"),
        )

    class _FakePool:
        def __init__(self) -> None:
            self.close_called = False

        async def close(self) -> None:
            self.close_called = True

    fake_pool = _FakePool()

    async def _fake_create_pool(*args: object, **kwargs: object) -> _FakePool:
        return fake_pool

    with patch("src.main.load_env_secrets", _empty_key_secrets):
        from fastapi import FastAPI

        from src.main import lifespan as _lifespan

        test_app = FastAPI(lifespan=_lifespan)
        # 빈 키는 KeyRing.from_legacy_hex()에서 KeyRingConfigError를 raise해야 함.
        from src.core.security.key_ring import KeyRingConfigError

        with patch("src.main.asyncpg.create_pool", _fake_create_pool):
            with pytest.raises(KeyRingConfigError):
                async with test_app.router.lifespan_context(test_app):
                    pass
        # finally 블록이 굴러가므로 pool.close()가 실제로 호출되고,
        # pool/event_bus가 app.state에 세팅되지 않는다.
        assert fake_pool.close_called
        assert not hasattr(test_app.state, "pool")
        assert not hasattr(test_app.state, "event_bus")
