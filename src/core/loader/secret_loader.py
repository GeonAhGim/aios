"""5.3 — Loader.load_env_secrets() + SecretBundle masking.

Spec: 03_core_modules_v1.1.md#§3.1, 07_logging_config_v1.3.md#§7.3
(.env.example full list — 1:1 correspondence)

7.4 Principle — this function's return value (SecretBundle) is never printed
in plaintext to logs (SecretBundle.__repr__ already masks, 01 §1.4).
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
    """os.environ takes precedence over .env file (real deployment vars override local .env)."""
    file_values = dotenv_values(_PROJECT_ROOT / ".env")
    merged: dict[str, str] = {k: v for k, v in file_values.items() if v is not None}
    merged.update(os.environ)
    return merged


def load_env_secrets(source: Mapping[str, str] | None = None) -> SecretBundle:
    """Read .env (+ real env vars) and validate/return as SecretBundle.

    When `source` is provided, use only that mapping (test-only — allows
    validation with isolated values without depending on actual .env file).
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
