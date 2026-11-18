#!/usr/bin/env python3
"""Seed a demo user into the UAT environment via public HTTP API only.

Creates a demo account (sign-up or login) and registers a demo exchange
connection.  Uses only the standard-library ``urllib`` — no third-party
HTTP client.  Idempotent: running twice produces the same state.

Usage::

    python scripts/uat/seed_demo.py                          # defaults
    python scripts/uat/seed_demo.py --base-url http://x:18000
    python scripts/uat/seed_demo.py --email user@example.com

Environment: UAT_DEMO_PASSWORD (preferred over --password CLI arg).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
DEFAULT_BASE_URL: str = "http://127.0.0.1:18000"
DEFAULT_EMAIL_DOMAIN: str = "demo.uat"
DEMO_EXCHANGES: list[dict[str, str]] = [
    # J1의 데모 키 경로 — demoDataset.ts의 3종목에 대응하는 거래소
    {"exchange": "bitget", "label": "BTC/USDT 데모"},
    {"exchange": "bitget", "label": "ETH/USDT 데모"},
    {"exchange": "bitget", "label": "SOL/USDT 데모"},
]

# ---------------------------------------------------------------------------
# HTTP helpers (urllib only)
# ---------------------------------------------------------------------------


def _url(path: str, base: str) -> str:
    """Return a full URL for *path* under *base*."""
    return base.rstrip("/") + path


def _request(
    method: str,
    url: str,
    body: Any | None = None,
    token: str | None = None,
) -> tuple[int, dict[str, Any] | str]:
    """Send an HTTP request and return (status_code, parsed_body_or_str)."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data: bytes | None = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=headers, method=method)  # noqa: S310

    try:
        with urllib.request.urlopen(req) as resp:  # noqa: S310
            payload = resp.read().decode("utf-8")
            return resp.status, json.loads(payload) if payload.strip() else {}
    except urllib.error.HTTPError as exc:
        payload = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(payload)
        except json.JSONDecodeError:
            return exc.code, payload
    except urllib.error.URLError as exc:
        return 0, f"URL error: {exc.reason}"


# ---------------------------------------------------------------------------
# Auth flow
# ---------------------------------------------------------------------------


def _signup(
    base_url: str,
    email: str,
    password: str,
) -> tuple[int, str | None]:
    """Register a new user. Returns (status, access_token_or_none)."""
    url = _url("/auth/register", base_url)
    status, body = _request("POST", url, body={"email": email, "password": password})
    if status == 201:
        token = _extract_token(body)
        return status, token
    # 409/400 — already exists; fall through to login
    return status, None


def _login(
    base_url: str,
    email: str,
    password: str,
) -> tuple[int, str | None]:
    """Log in. Returns (status, access_token_or_none)."""
    url = _url("/auth/login", base_url)
    status, body = _request("POST", url, body={"email": email, "password": password})
    token = _extract_token(body)
    return status, token


def _envelope_payload(body: dict[str, Any]) -> Any:
    """실제 API 응답 봉투는 {"data": ..., "meta": ...}. 예전 표기 {"ok": ...}도 받아 준다."""
    if "data" in body:
        return body["data"]
    return body.get("ok")


def _extract_token(body: dict[str, Any] | str) -> str | None:
    """Pull an access token from various response shapes."""
    if isinstance(body, str):
        return None
    # ApiResponse envelope: {"data": {"access_token": ...}, "meta": {...}} (구버전 표기 "ok"도 허용)
    payload = _envelope_payload(body)
    if isinstance(payload, dict):
        return payload.get("access_token")
    # Direct TokenPairResponse
    return body.get("access_token")


# ---------------------------------------------------------------------------
# Exchange credential registration
# ---------------------------------------------------------------------------

# Demo keys — these are the standard test credentials used throughout the
# codebase (e2e tests, demoDataset.ts, etc.). They are NOT secrets; they
# are public test fixtures.
_DEMO_API_KEY: str = "demo-api-key"  # noqa: S105
_DEMO_API_SECRET: str = "demo-api-secret"  # noqa: S105
_DEMO_API_PASSPHRASE: str = "demo-api-passphrase"  # noqa: S105


def _register_exchange(
    base_url: str,
    token: str,
    exchange: str,
) -> tuple[int, str | None]:
    """Register a demo exchange credential. Returns (status, error_code_or_none)."""
    url = _url("/exchange-credentials", base_url)
    body = {
        "exchange": exchange,
        "api_key": _DEMO_API_KEY,
        "api_secret": _DEMO_API_SECRET,
        "api_passphrase": _DEMO_API_PASSPHRASE,
    }
    status, resp = _request("POST", url, body=body, token=token)
    error_code: str | None = None
    if isinstance(resp, dict):
        error_code = resp.get("error_code") or resp.get("error")
    return status, error_code


def _list_exchanges(
    base_url: str,
    token: str,
) -> tuple[int, list[dict[str, Any]]]:
    """List registered exchange credentials. Returns (status, list)."""
    url = _url("/exchange-credentials", base_url)
    status, body = _request("GET", url, token=token)
    if isinstance(body, list):
        return status, body
    # ApiResponse envelope
    payload = _envelope_payload(body) if isinstance(body, dict) else None
    if isinstance(payload, list):
        return status, payload
    return status, []


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def _resolve_password(cli_password: str | None) -> str:
    """Return password from env var UAT_DEMO_PASSWORD, falling back to CLI arg."""
    env_pw = os.environ.get("UAT_DEMO_PASSWORD")
    if env_pw:
        return env_pw
    if cli_password:
        return cli_password
    # Generate a random one for non-interactive use
    import secrets

    return f"demo-{secrets.token_urlsafe(16)}"


def run(
    base_url: str = DEFAULT_BASE_URL,
    email: str | None = None,
    password: str | None = None,
) -> int:
    """Seed demo user and exchange credentials. Returns 0 on success, 1 on failure."""
    pw = _resolve_password(password)
    if email is None:
        import secrets

        email = f"demo-{secrets.token_urlsafe(6)}@{DEFAULT_EMAIL_DOMAIN}"

    print(f"Seed user: {email}")
    print(f"Base URL: {base_url}")

    # Step 1: Sign up (or already exists)
    print("Step 1: sign up ...", end=" ")
    status, token = _signup(base_url, email, pw)
    if status == 201 and token:
        print(f"OK (signup, {status})")
    elif status in (400, 409):
        print(f"Already exists ({status}), logging in ...", end=" ")
        status, token = _login(base_url, email, pw)
        if status == 200 and token:
            print(f"OK (login, {status})")
        else:
            print(f"FAIL status={status} body={token}")
            return 1
    else:
        print(f"FAIL status={status} body={token}")
        return 1

    # Step 2: Register demo exchanges (idempotent — skip if already registered)
    for demo in DEMO_EXCHANGES:
        exchange = demo["exchange"]
        label = demo["label"]
        print(f"Step 2: check {label} ({exchange}) ...", end=" ")

        # Check if already registered
        status, creds = _list_exchanges(base_url, token)
        if status != 200:
            print(f"FAIL list status={status}")
            return 1

        already = any(c.get("exchange") == exchange for c in creds)
        if already:
            print("OK (already registered)")
            continue

        # Register
        status, err = _register_exchange(base_url, token, exchange)
        if status == 201:
            print(f"OK (registered, {status})")
        else:
            print(f"FAIL status={status} error={err}")
            return 1

    print("Done.")
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Seed a demo user into the UAT environment.")
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Base URL of the UAT API (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--email",
        default=None,
        help="Email address for the demo user (default: auto-generated)",
    )
    parser.add_argument(
        "--password",
        default=None,
        help="Password (falls back to UAT_DEMO_PASSWORD env var)",
    )
    args = parser.parse_args(argv)
    return run(base_url=args.base_url, email=args.email, password=args.password)


if __name__ == "__main__":
    sys.exit(main())
