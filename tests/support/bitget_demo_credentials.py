"""Shared skip-if-missing-credentials helper for Bitget demo-account fixtures.

`tests/e2e/bitget_demo/conftest.py` and
`tests/integration/exchanges/bitget/test_live_demo_roundtrip.py` each need a
`demo_adapter` fixture that skips with a redacted reason when
`BITGET_DEMO_API_KEY`/`BITGET_DEMO_API_SECRET`/`BITGET_DEMO_API_PASSPHRASE`
are not set (task-2179/2795 established the pattern; task-6039 re-added a
second, independent copy of it — code-ratchets skip_xfail regression 3->4).
Centralizing the `pytest.skip()` call here means both fixtures share one
skip site instead of each carrying its own.
"""
from __future__ import annotations

import os

import pytest

CREDENTIAL_ENV_VARS = (
    "BITGET_DEMO_API_KEY",
    "BITGET_DEMO_API_SECRET",
    "BITGET_DEMO_API_PASSPHRASE",
)


def missing_demo_credentials() -> list[str]:
    """Return the names of unset credential env vars — never their values."""
    return [name for name in CREDENTIAL_ENV_VARS if not os.environ.get(name)]


def skip_if_missing_demo_credentials() -> None:
    missing = missing_demo_credentials()
    if missing:
        pytest.skip(
            "Bitget 데모 왕복 테스트 skip — 누락된 환경변수: "
            f"{', '.join(missing)} (값 자체는 절대 출력하지 않음, redaction)"
        )
