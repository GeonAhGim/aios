"""AIOS test-process bootstrap.

Integration tests must never read developer or production credentials, and they
must never fall back to ``aios_dev``.  Many existing integration modules read
the project-root ``.env`` through ``dotenv_values``; this bootstrap is imported
before those modules and supplies one deterministic test-only view instead.
"""

from __future__ import annotations

import os
from pathlib import Path

import dotenv

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_ROOT_ENV_PATH = (_PROJECT_ROOT / ".env").resolve()

_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not _TEST_DATABASE_URL:
    raise RuntimeError(
        "TEST_DATABASE_URL이 필요합니다. 테스트는 development/production DB에 연결할 수 없습니다."
    )

# Do not use an operator's credentials even if their shell or local .env has
# them. External exchange calls in tests are mocked; these values exist only so
# the application can construct its validated SecretBundle during router import.
_TEST_ENV = {
    "DATABASE_URL": _TEST_DATABASE_URL,
    "JWT_SECRET_KEY": "aios-test-only-jwt-secret-must-be-at-least-32-bytes",
    "JWT_ALGORITHM": "HS256",
    "JWT_EXPIRE_MINUTES": "60",
    "CREDENTIAL_ENCRYPTION_KEY": "22" * 32,
    "BITGET_API_KEY": "aios-test-only-bitget-key",
    "BITGET_API_SECRET": "aios-test-only-bitget-secret",
    "KIS_APP_KEY": "aios-test-only-kis-key",
    "KIS_APP_SECRET": "aios-test-only-kis-secret",
    "SMTP_HOST": "",
    "SMTP_USER": "",
    "SMTP_PASSWORD": "",
    "FCM_SERVER_KEY": "",
    "APNS_KEY_ID": "",
    "CORS_ALLOWED_ORIGINS": "http://testserver",
}
os.environ.update(_TEST_ENV)

_original_dotenv_values = dotenv.dotenv_values


def _test_dotenv_values(dotenv_path: str | os.PathLike[str] | None = None, *args, **kwargs):
    """Return test-only settings whenever legacy tests request root ``.env``."""
    if dotenv_path is not None and Path(dotenv_path).resolve() == _ROOT_ENV_PATH:
        return dict(_TEST_ENV)
    return _original_dotenv_values(dotenv_path, *args, **kwargs)


dotenv.dotenv_values = _test_dotenv_values
