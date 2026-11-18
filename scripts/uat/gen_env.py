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

# Keys that this script will fill in (order matters for readability).
_TARGET_KEYS: list[str] = [
    "DATABASE_URL",
    "POSTGRES_USER",
    "POSTGRES_DB",
    "POSTGRES_PASSWORD",
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
    }


NL = chr(10)


def build_env(
    template_text: str,
    web_origin: str,
    secrets_data: dict[str, str],
) -> str:
    """Return a new .env string with target keys replaced.

    Lines not matching a target key are kept verbatim (including comments
    and blank lines).  This makes unit testing trivial — just assert on
    the output string.

    Args:
        template_text:  Contents of .env.example.
        web_origin:     Value for CORS_ALLOWED_ORIGINS.
        secrets_data:   Dict from make_secrets().

    Returns:
        A complete .env string.
    """
    lines: list[str] = template_text.splitlines(keepends=True)
    result: list[str] = []

    for line in lines:
        stripped = line.split("=", 1)[0].strip() if "=" in line else ""
        if stripped == "DATABASE_URL":
            result.append(
                f"DATABASE_URL=postgresql+asyncpg://{DB_USER}:"
                f"{secrets_data['POSTGRES_PASSWORD']}"
                f"@postgres:5432/{DB_NAME}\n"
            )
        elif stripped == "POSTGRES_USER":
            result.append(f"POSTGRES_USER={DB_USER}\n")
        elif stripped == "POSTGRES_DB":
            result.append(f"POSTGRES_DB={DB_NAME}\n")
        elif stripped == "POSTGRES_PASSWORD":
            result.append(f"POSTGRES_PASSWORD={secrets_data['POSTGRES_PASSWORD']}\n")
        elif stripped == "JWT_SIGNING_KEYS":
            result.append(
                f"JWT_SIGNING_KEYS={secrets_data['JWT_KID']}:{secrets_data['JWT_HEX64']}\n"
            )
        elif stripped == "JWT_ACTIVE_KID":
            result.append(f"JWT_ACTIVE_KID={secrets_data['JWT_KID']}\n")
        elif stripped == "CREDENTIAL_ENCRYPTION_KEYS_PAPER":
            result.append(
                f"CREDENTIAL_ENCRYPTION_KEYS_PAPER={secrets_data['CRED_KID']}:"
                f"{secrets_data['CRED_HEX64']}\n"
            )
        elif stripped == "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER":
            result.append(f"CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER={secrets_data['CRED_KID']}\n")
        elif stripped == "AIOS_RUNTIME_MODE":
            result.append("AIOS_RUNTIME_MODE=PAPER\n")
        elif stripped == "CORS_ALLOWED_ORIGINS":
            result.append(f"CORS_ALLOWED_ORIGINS={web_origin}\n")
        # Explicitly drop LIVE / legacy keys — never emit them.
        elif stripped in (
            "CREDENTIAL_ENCRYPTION_KEYS_LIVE",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE",
            "CREDENTIAL_ENCRYPTION_KEY",
        ):
            pass  # drop
        else:
            result.append(line)

    seen = {ln.split("=", 1)[0].strip() for ln in result if "=" in ln}
    if result and not result[-1].endswith(NL):
        result.append(NL)
    # postgres 컨테이너가 DATABASE_URL과 같은 사용자·DB로 초기화되도록 항상 명시한다(미지정이면
    # 이미지 기본값 postgres로 만들어져 api/migrate가 "password authentication failed"가 된다).
    if "POSTGRES_USER" not in seen:
        result.append(f"POSTGRES_USER={DB_USER}" + NL)
    if "POSTGRES_DB" not in seen:
        result.append(f"POSTGRES_DB={DB_NAME}" + NL)
    if "POSTGRES_PASSWORD" not in seen:
        result.append(f"POSTGRES_PASSWORD={secrets_data['POSTGRES_PASSWORD']}" + NL)
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
