"""5.3 — Loader.load_env_secrets() + SecretBundle 마스킹.

Spec: 03_core_modules_v1.1.md#§3.1, 07_logging_config_v1.3.md#§7.3
(.env.example 전체 목록과 1:1 대응)

7.4 원칙 — 이 함수의 반환값(SecretBundle)은 절대 로그에 평문 출력되지
않는다(SecretBundle.__repr__이 이미 마스킹 처리, 01번 §1.4).
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr

from src.data.models.trading import SecretBundle

_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _merged_environment() -> Mapping[str, str]:
    """os.environ이 .env 파일보다 우선한다(실제 배포 환경변수가 로컬 .env를 덮어씀)."""
    file_values = dotenv_values(_PROJECT_ROOT / ".env")
    merged: dict[str, str] = {k: v for k, v in file_values.items() if v is not None}
    merged.update(os.environ)
    return merged


def load_env_secrets(source: Mapping[str, str] | None = None) -> SecretBundle:
    """.env(+ 실제 환경변수)를 읽어 SecretBundle로 검증·반환한다.

    `source`를 명시하면 그 매핑만 사용한다(테스트 전용 — 실제 .env 파일에
    의존하지 않고 격리된 값으로 검증 가능하게 함).
    """
    env = source if source is not None else _merged_environment()

    smtp_password = env.get("SMTP_PASSWORD") or None
    fcm_server_key = env.get("FCM_SERVER_KEY") or None
    apns_key_id = env.get("APNS_KEY_ID") or None

    return SecretBundle(
        database_url=SecretStr(env["DATABASE_URL"]),
        jwt_secret_key=SecretStr(env["JWT_SECRET_KEY"]),
        jwt_algorithm=env.get("JWT_ALGORITHM", "HS256"),
        jwt_expire_minutes=int(env.get("JWT_EXPIRE_MINUTES", "60")),
        credential_encryption_key=SecretStr(env["CREDENTIAL_ENCRYPTION_KEY"]),
        bitget_api_key=SecretStr(env["BITGET_API_KEY"]),
        bitget_api_secret=SecretStr(env["BITGET_API_SECRET"]),
        kis_app_key=SecretStr(env["KIS_APP_KEY"]),
        kis_app_secret=SecretStr(env["KIS_APP_SECRET"]),
        smtp_host=env.get("SMTP_HOST") or None,
        smtp_port=int(env.get("SMTP_PORT", "587")),
        smtp_user=env.get("SMTP_USER") or None,
        smtp_password=SecretStr(smtp_password) if smtp_password is not None else None,
        fcm_server_key=SecretStr(fcm_server_key) if fcm_server_key is not None else None,
        apns_key_id=SecretStr(apns_key_id) if apns_key_id is not None else None,
        cors_allowed_origins=[
            origin.strip()
            for origin in env.get("CORS_ALLOWED_ORIGINS", "").split(",")
            if origin.strip()
        ],
    )
