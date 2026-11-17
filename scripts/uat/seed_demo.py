#!/usr/bin/env python3
"""Seed a demo user into the UAT environment via public HTTP API only.

Creates a demo user (signup or login if already exists) and registers a
Bitget demo exchange credential — idempotent (safe to run multiple times).

Uses only the standard library ``urllib`` (no third-party HTTP client).
Never prints secret values to stdout or logs.

Usage:
    python scripts/uat/seed_demo.py                        # defaults
    python scripts/uat/seed_demo.py --base-url http://x    # explicit URL
    UAT_DEMO_PASSWORD=xxx python scripts/uat/seed_demo.py  # env override
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_BASE_URL: str = "http://127.0.0.1:18000"
DEFAULT_EMAIL: str = "demo@aios-uat.local"
# Demo password — not a real secret; UAT_DEMO_PASSWORD env var overrides at runtime.
DEFAULT_PASSWORD: str = "UatDemo123456!"  # noqa: S105
BITGET_EXCHANGE: str = "bitget"

# Bitget demo credentials (publicly documented demo keys — not real trading keys)
BITGET_DEMO_API_KEY: str = os.environ.get("BITGET_DEMO_API_KEY", "a1b2c3d4e5f6g7h8i9j0")
BITGET_DEMO_API_SECRET: str = os.environ.get("BITGET_DEMO_API_SECRET", "k1l2m3n4o5p6q7r8s9t0")
BITGET_DEMO_API_PASSPHRASE: str = os.environ.get("BITGET_DEMO_API_PASSPHRASE", "u1v2w3x4y5z6")


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _url(path: str, base: str) -> str:
    """Build a full URL from *base* and *path*."""
    return base.rstrip("/") + path


def _request(
    method: str,
    url: str,
    body: dict | None = None,
    token: str | None = None,
) -> tuple[int, dict]:
    """Send an HTTP request and return (status_code, parsed_json_body).

    Raises ``SystemExit`` on network errors (DNS failure, connection refused,
    etc.) so the caller can report the failure stage.
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)  # noqa: S310

    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            raw = resp.read()
            return resp.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        return exc.code, json.loads(raw) if raw else {}
    except urllib.error.URLError as exc:
        raise SystemExit(f"Network error: {exc.reason}") from exc


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _signup(base: str, email: str, password: str) -> tuple[int, str]:
    """Step 1: register the user. Returns (status, 'signup'|'exists')."""
    status, body = _request(
        "POST",
        _url("/auth/register", base),
        {
            "email": email,
            "password": password,
        },
    )
    if status == 201:
        return status, "signup"
    if status == 409:
        # Already registered — proceed to login
        return status, "exists"
    return status, "signup"


def _login(base: str, email: str, password: str) -> tuple[int, dict]:
    """Step 2: login. Returns (status, response_body)."""
    return _request(
        "POST",
        _url("/auth/login", base),
        {
            "email": email,
            "password": password,
        },
    )


def _register_exchange(base: str, token: str) -> tuple[int, str]:
    """Step 3: register Bitget demo credential. Returns (status, 'ok'|'fail')."""
    status, body = _request(
        "POST",
        _url("/exchange-credentials", base),
        {
            "exchange": BITGET_EXCHANGE,
            "api_key": BITGET_DEMO_API_KEY,
            "api_secret": BITGET_DEMO_API_SECRET,
            "api_passphrase": BITGET_DEMO_API_PASSPHRASE,
        },
        token=token,
    )
    if status == 201:
        return status, "ok"
    # 409 = already registered (same user_id + exchange UNIQUE constraint)
    if status == 409:
        return status, "exists"
    return status, "fail"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed a demo user into the UAT environment.")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="API base URL (default: http://127.0.0.1:18000)",
    )
    parser.add_argument(
        "--email", default=DEFAULT_EMAIL, help="Demo user email (default: demo@aios-uat.local)"
    )
    parser.add_argument(
        "--password",
        default=DEFAULT_PASSWORD,
        help="Demo user password (env UAT_DEMO_PASSWORD takes priority)",
    )
    args = parser.parse_args(argv)

    # Password: env var overrides CLI arg
    password = os.environ.get("UAT_DEMO_PASSWORD") or args.password

    base = args.base_url
    email = args.email

    # Step 1: signup
    print("Step 1/3: signup ...", end=" ", flush=True)
    status, detail = _signup(base, email, password)
    if status == 201:
        print(f"OK ({status} created)")
    elif status == 409:
        print(f"OK ({status} already exists — proceeding to login)")
    else:
        print(f"FAIL ({status})")
        return 1

    # Step 2: login
    print("Step 2/3: login ...", end=" ", flush=True)
    status, body = _login(base, email, password)
    if status != 200:
        print(f"FAIL ({status})")
        return 1
    access_token = body.get("data", {}).get("access_token", "")
    print("OK")

    # Step 3: register exchange credential
    print("Step 3/3: register demo exchange ...", end=" ", flush=True)
    status, _ = _register_exchange(base, access_token)
    if status in (201, 409):
        label = "created" if status == 201 else "already registered"
        print(f"OK ({status} {label})")
    else:
        print(f"FAIL ({status})")
        return 1

    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
