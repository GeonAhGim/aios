"""SecretHandle 단위 테스트 — 복호 왕복 + 컨텍스트 종료 후 zero-fill.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-32.
핵심 불변: `async with` 블록 안에서는 평문이 정확히 복호되고, 블록을 나가면
내부 bytearray가 전부 0으로 덮이며(정상 종료·예외 종료 모두), 블록 밖에서의
접근은 예외로 막힌다.
"""
from __future__ import annotations

import pytest

from src.core.observability.metric_names import SECURITY_SECRET_DECRYPT_COUNT_TOTAL
from src.core.security.envelope import seal
from src.core.security.key_ring import KeyRing
from src.core.security.secret_handle import SecretHandle, SecretHandleClosedError
from src.core.security.secret_ref import SecretRef

KEY_V1 = "11" * 32


def _ring() -> KeyRing:
    return KeyRing.from_env(
        "PAPER",
        env={
            "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
        },
    )


def _ref() -> SecretRef:
    return SecretRef(scope="PAPER", kind="exchange_credential", id="42", kid="v1")


class _CountingMetrics:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def counter(self, name: str, labels: dict[str, str] | None = None) -> None:
        self.calls.append((name, labels))

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        raise AssertionError("observe는 이 테스트에서 쓰이지 않는다")

    def gauge(self, name: str, value: float, labels: dict[str, str] | None = None) -> None:
        raise AssertionError("gauge는 이 테스트에서 쓰이지 않는다")


async def test_enter_decrypts_api_key_secret_and_extra() -> None:
    ring = _ring()
    handle = SecretHandle(
        _ref(),
        ring,
        api_key=seal(b"key-bytes", ring),
        api_secret=seal(b"secret-bytes", ring),
        extra={"passphrase": seal(b"extra-bytes", ring)},
        metrics_port=_CountingMetrics(),
    )
    async with handle as h:
        assert h.api_key == b"key-bytes"
        assert h.api_secret == b"secret-bytes"
        assert h.extra == {"passphrase": b"extra-bytes"}


async def test_enter_increments_secret_decrypt_counter_once() -> None:
    ring = _ring()
    metrics_port = _CountingMetrics()
    handle = SecretHandle(
        _ref(), ring, api_key=seal(b"key-bytes", ring), metrics_port=metrics_port
    )
    async with handle:
        pass
    assert metrics_port.calls == [
        (SECURITY_SECRET_DECRYPT_COUNT_TOTAL, {"scope": "PAPER", "kind": "exchange_credential"})
    ]


async def test_aexit_zero_fills_internal_bytearrays() -> None:
    ring = _ring()
    handle = SecretHandle(
        _ref(),
        ring,
        api_key=seal(b"key-bytes", ring),
        api_secret=seal(b"secret-bytes", ring),
        extra={"passphrase": seal(b"extra-bytes", ring)},
        metrics_port=_CountingMetrics(),
    )
    async with handle:
        api_key_buf = handle._api_key
        api_secret_buf = handle._api_secret
        extra_buf = handle._extra["passphrase"]
        assert api_key_buf is not None and any(b != 0 for b in api_key_buf)

    assert api_key_buf is not None and all(b == 0 for b in api_key_buf)
    assert api_secret_buf is not None and all(b == 0 for b in api_secret_buf)
    assert all(b == 0 for b in extra_buf)


async def test_aexit_zero_fills_even_when_body_raises() -> None:
    ring = _ring()
    handle = SecretHandle(
        _ref(), ring, api_key=seal(b"key-bytes", ring), metrics_port=_CountingMetrics()
    )
    with pytest.raises(RuntimeError):
        async with handle as h:
            api_key_buf = h._api_key
            raise RuntimeError("boom")
    assert api_key_buf is not None and all(b == 0 for b in api_key_buf)
    assert handle._api_key is None


async def test_property_access_before_enter_raises() -> None:
    ring = _ring()
    handle = SecretHandle(
        _ref(), ring, api_key=seal(b"key-bytes", ring), metrics_port=_CountingMetrics()
    )
    with pytest.raises(SecretHandleClosedError):
        _ = handle.api_key


async def test_property_access_after_exit_raises() -> None:
    ring = _ring()
    handle = SecretHandle(
        _ref(), ring, api_key=seal(b"key-bytes", ring), metrics_port=_CountingMetrics()
    )
    async with handle:
        pass
    with pytest.raises(SecretHandleClosedError):
        _ = handle.api_key


async def test_api_secret_missing_raises_value_error() -> None:
    ring = _ring()
    handle = SecretHandle(
        _ref(), ring, api_key=seal(b"key-bytes", ring), metrics_port=_CountingMetrics()
    )
    async with handle as h:
        with pytest.raises(ValueError):
            _ = h.api_secret
