#!/usr/bin/env python3
"""Generate a .env.uat file for the UAT (experience) stack.

Creates a safe, reproducible .env.uat from .env.example.  Only standard
library is used.  Secrets are generated at runtime; no secret values are
printed to stdout.

Usage:
    python scripts/uat/gen_env.py                  # writes .env.uat in repo root
    python scripts/uat/gen_env.py --force           # overwrite existing file
    python scripts/uat/gen_env.py --out /tmp/.env   # write to custom path
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Constants (UAT-specific ports / project name)
# ---------------------------------------------------------------------------
PROJECT_NAME: str = "aios-uat"
DB_PORT: int = 15432
REDIS_PORT: int = 16379
API_PORT: int = 18000
WEB_PORT: int = 18080
DEFAULT_WEB_ORIGIN: str = "http://127.0.0.1:18080"
DEFAULT_OUT: str = ".env.uat"
DB_USER: str = "aios"
DB_NAME: str = "aios"
DEMO_EMAIL: str = "demo@demo.uat"

# Keys that this script will fill in (order matters for readability).
_TARGET_KEYS: list[str] = [
    "DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_PASSWORD",
    "JWT_SECRET_KEY",
    "CREDENTIAL_ENCRYPTION_KEY",
    "JWT_SIGNING_KEYS",
    "JWT_ACTIVE_KID",
    "CREDENTIAL_ENCRYPTION_KEYS_PAPER",
    "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER",
    "AIOS_RUNTIME_MODE",
    "CORS_ALLOWED_ORIGINS",
]

# Hex-unsafe characters to avoid in passwords (already handled by token_urlsafe).
_KEY_BYTES: int = 32  # AES-256; matches key_ring.py


def make_secrets() -> dict[str, str]:
    """Return a dict of generated secret values.

    Returns:
        {
            "POSTGRES_PASSWORD": <url-safe token>,
            "JWT_KID": "uat1",
            "JWT_HEX64": <hex64>,
            "CRED_KID": "uat1",
            "CRED_HEX64": <hex64>,
        }
    """
    postgres_password: str = secrets.token_urlsafe(32)
    hex64: str = secrets.token_hex(_KEY_BYTES)  # 64 hex chars
    return {
        "POSTGRES_PASSWORD": postgres_password,
        "JWT_KID": "uat1",
        "JWT_HEX64": hex64,
        "CRED_KID": "uat1",
        "CRED_HEX64": secrets.token_hex(_KEY_BYTES),
        "JWT_SECRET_KEY": secrets.token_hex(_KEY_BYTES),
        "CREDENTIAL_ENCRYPTION_KEY": secrets.token_hex(_KEY_BYTES),
        # 서버 정책(12자 이상, 대·소문자·숫자·특수문자)을 항상 만족하도록 고정 접두를 둔다.
        "UAT_DEMO_PASSWORD": "Aa1!" + secrets.token_urlsafe(16),
        "AIOS_LIQUIDATION_SEED_KEY": secrets.token_hex(_KEY_BYTES),
    }


NL = chr(10)

# secret_loader.py(SecretBundle)가 키 존재를 요구하는 구형 슬롯. 앱 기동(api/worker)에 필요하지만
# 모의 스택은 실거래소를 호출하지 않으므로 실키가 아닌 명백한 자리표시자를 쓴다.
PLACEHOLDER = "uat-paper-placeholder-not-a-real-key"
EXCHANGE_PLACEHOLDER_KEYS: tuple[str, ...] = (
    "BITGET_API_KEY",
    "BITGET_API_SECRET",
    "KIS_APP_KEY",
    "KIS_APP_SECRET",
)


# LIVE-scope keys are never emitted (this stack is PAPER only).
_DROPPED_KEYS: frozenset[str] = frozenset(
    {"CREDENTIAL_ENCRYPTION_KEYS_LIVE", "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE"}
)

# Keys appended when the template does not define them (the app needs them to start).
_APPEND_IF_MISSING: tuple[str, ...] = (
    "JWT_SECRET_KEY",
    "CREDENTIAL_ENCRYPTION_KEY",
    *EXCHANGE_PLACEHOLDER_KEYS,
    "AIOS_LIQUIDATION_SEED_KEY",
    "UAT_DEMO_EMAIL",
    "UAT_DEMO_PASSWORD",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_PASSWORD",
)


def _assignments(web_origin: str, secrets_data: dict[str, str]) -> dict[str, str]:
    """Return every key this script owns and the value it must carry."""
    password = secrets_data["POSTGRES_PASSWORD"]
    return {
        "DATABASE_URL": f"postgresql+asyncpg://{DB_USER}:{password}@postgres:5432/{DB_NAME}",
        # Without these the postgres container initialises as the image default user and
        # api/migrate fail with "password authentication failed" (seen on first boot).
        "POSTGRES_USER": DB_USER,
        "POSTGRES_DB": DB_NAME,
        "POSTGRES_PASSWORD": password,
        "JWT_SECRET_KEY": secrets_data["JWT_SECRET_KEY"],
        "CREDENTIAL_ENCRYPTION_KEY": secrets_data["CREDENTIAL_ENCRYPTION_KEY"],
        "JWT_SIGNING_KEYS": f"{secrets_data['JWT_KID']}:{secrets_data['JWT_HEX64']}",
        "JWT_ACTIVE_KID": secrets_data["JWT_KID"],
        "CREDENTIAL_ENCRYPTION_KEYS_PAPER": (
            f"{secrets_data['CRED_KID']}:{secrets_data['CRED_HEX64']}"
        ),
        "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": secrets_data["CRED_KID"],
        "AIOS_RUNTIME_MODE": "PAPER",
        "CORS_ALLOWED_ORIGINS": web_origin,
        # Demo account: seed_demo signs up with it and the user logs in from .env.uat.
        # Without it the liquidation worker fails closed on every tick and /readyz stays 503.
        "AIOS_LIQUIDATION_SEED_KEY": secrets_data["AIOS_LIQUIDATION_SEED_KEY"],
        "UAT_DEMO_EMAIL": DEMO_EMAIL,
        "UAT_DEMO_PASSWORD": secrets_data["UAT_DEMO_PASSWORD"],
        **{key: PLACEHOLDER for key in EXCHANGE_PLACEHOLDER_KEYS},
    }


def build_env(
    template_text: str,
    web_origin: str,
    secrets_data: dict[str, str],
) -> str:
    """Return a new .env string with target keys replaced.

    Lines not matching a target key are kept verbatim (including comments
    and blank lines), which keeps unit testing trivial: assert on the output.

    Args:
        template_text:  Contents of .env.example.
        web_origin:     Value for CORS_ALLOWED_ORIGINS.
        secrets_data:   Dict from make_secrets().

    Returns:
        A complete .env string.
    """
    assigned = _assignments(web_origin, secrets_data)
    result: list[str] = []
    for line in template_text.splitlines(keepends=True):
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in assigned:
            result.append(f"{key}={assigned[key]}\n")
        elif key not in _DROPPED_KEYS:
            result.append(line)

    seen = {ln.split("=", 1)[0].strip() for ln in result if "=" in ln}
    if result and not result[-1].endswith(NL):
        result.append(NL)
    for key in _APPEND_IF_MISSING:
        if key not in seen:
            result.append(f"{key}={assigned[key]}" + NL)
    return "".join(result)


def write_private(path: Path, text: str) -> None:
    """Write *text* to *path* with mode 0o600 (owner-only on POSIX).

    On Windows the permission bits are ignored; a comment in the source
    documents the intent.
    """
    fd: int = os.open(
        str(path),
        os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
        0o600,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _validate_web_origin(origin: str) -> None:
    """Raise ValueError if *origin* is not a valid URL origin.

    An origin must be ``scheme://host[:port]`` with no path component.
    """
    if not origin:
        raise ValueError("UAT_WEB_ORIGIN must not be empty")
    # Must start with http:// or https://
    if not (origin.startswith("http://") or origin.startswith("https://")):
        raise ValueError(f"UAT_WEB_ORIGIN must be http:// or https://, got: {origin}")
    # A valid origin has no path after ``scheme://host[:port]``.
    # Split on '://' first to get the authority part, then check for '/'.
    authority = origin.split("://", 1)[1]
    if "/" in authority:
        raise ValueError(f"UAT_WEB_ORIGIN must be an origin (no path), got: {origin}")


def main(argv: list[str] | None = None) -> int:
    """Entry point.  Returns 0 on success, 1 on error."""
    parser = argparse.ArgumentParser(description="Generate .env.uat for the UAT stack.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing .env.uat.",
    )
    parser.add_argument(
        "--out",
        default=DEFAULT_OUT,
        help="Output path (default: .env.uat in repo root).",
    )
    args = parser.parse_args(argv)

    out_path = Path(args.out)

    # --- pre-check: file exists? ---
    if out_path.exists() and not args.force:
        print(f"이미 존재: {out_path}")
        return 0

    # --- read template ---
    repo_root = Path(__file__).resolve().parent.parent.parent  # repo root
    template_path = repo_root / ".env.example"
    template_text = template_path.read_text(encoding="utf-8")

    # --- resolve web origin ---
    web_origin = os.environ.get("UAT_WEB_ORIGIN", DEFAULT_WEB_ORIGIN)
    try:
        _validate_web_origin(web_origin)
    except ValueError as exc:
        print(f"UAT_WEB_ORIGIN 오류: {exc}", file=sys.stderr)
        return 1

    # --- generate secrets ---
    secrets_data = make_secrets()

    # --- build env text ---
    env_text = build_env(template_text, web_origin, secrets_data)

    # --- write ---
    write_private(out_path, env_text)
    print(str(out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
