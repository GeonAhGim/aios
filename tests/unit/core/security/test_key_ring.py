"""KeyRing 단위 테스트.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-31
(+ §6 I7). 핵심: 레거시 단일 키 흡수, 버전 키 파싱, PAPER 런타임 LIVE 키
기동 거부(fail-closed).
"""
from __future__ import annotations

import pytest

from src.core.exceptions import FrozenZoneLiveModeBlockedError
from src.core.security.key_ring import KeyRing, KeyRingConfigError, UnknownKeyIdError

KEY_V1 = "11" * 32
KEY_V2 = "22" * 32
KEY_LEGACY = "33" * 32


def test_from_env_parses_versioned_keys_and_active_kid() -> None:
    ring = KeyRing.from_env(
        "PAPER",
        env={
            "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1},v2:{KEY_V2}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v2",
        },
    )
    assert ring.active_kid == "v2"
    assert set(ring.kids()) == {"v1", "v2"}
    assert ring.key("v1") == bytes.fromhex(KEY_V1)
    assert ring.key("v2") == bytes.fromhex(KEY_V2)


def test_from_env_absorbs_legacy_single_key_as_legacy_kid() -> None:
    ring = KeyRing.from_env(
        "PAPER",
        env={
            "CREDENTIAL_ENCRYPTION_KEY": KEY_LEGACY,
            "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
        },
    )
    assert "legacy" in ring.kids()
    assert ring.key("legacy") == bytes.fromhex(KEY_LEGACY)
    assert ring.active_kid == "v1"


def test_from_env_legacy_only_environment_defaults_active_kid_to_legacy() -> None:
    """전환기: 아직 CREDENTIAL_ENCRYPTION_KEYS_PAPER를 안 쓰는 기존 배포."""
    ring = KeyRing.from_env("PAPER", env={"CREDENTIAL_ENCRYPTION_KEY": KEY_LEGACY})
    assert ring.active_kid == "legacy"
    assert ring.key("legacy") == bytes.fromhex(KEY_LEGACY)


def test_key_unknown_kid_raises() -> None:
    ring = KeyRing.from_env("PAPER", env={"CREDENTIAL_ENCRYPTION_KEY": KEY_LEGACY})
    with pytest.raises(UnknownKeyIdError):
        ring.key("v99")


def test_from_env_no_keys_at_all_raises_config_error() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env("PAPER", env={})


def test_from_env_versioned_keys_without_active_kid_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env(
            "PAPER", env={"CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}"}
        )


def test_from_env_active_kid_not_in_keys_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v2",
            },
        )


def test_from_env_bad_hex_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": "v1:not-hex",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
            },
        )


def test_from_env_wrong_length_key_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": "v1:aabbcc",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
            },
        )


def test_from_env_duplicate_kid_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1},v1:{KEY_V2}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
            },
        )


def test_from_env_reserved_legacy_kid_in_versioned_var_raises() -> None:
    with pytest.raises(KeyRingConfigError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"legacy:{KEY_V1}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "legacy",
            },
        )


def test_from_env_rejects_live_keys_when_paper_runtime_explicit() -> None:
    """I7: PAPER 런타임 프로세스에 LIVE KEK가 있으면 기동 거부(fail-closed)."""
    with pytest.raises(FrozenZoneLiveModeBlockedError):
        KeyRing.from_env(
            "PAPER",
            env={
                "AIOS_RUNTIME_MODE": "PAPER",
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
                "CREDENTIAL_ENCRYPTION_KEYS_LIVE": f"v1:{KEY_V2}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE": "v1",
            },
        )


def test_from_env_rejects_live_keys_when_runtime_mode_unset_defaults_paper() -> None:
    """AIOS_RUNTIME_MODE 미설정은 fail-closed 기본값 PAPER로 취급한다."""
    with pytest.raises(FrozenZoneLiveModeBlockedError):
        KeyRing.from_env(
            "PAPER",
            env={
                "CREDENTIAL_ENCRYPTION_KEYS_PAPER": f"v1:{KEY_V1}",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_PAPER": "v1",
                "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE": "v1",
            },
        )


def test_from_env_allows_live_keys_in_live_runtime() -> None:
    ring = KeyRing.from_env(
        "LIVE",
        env={
            "AIOS_RUNTIME_MODE": "LIVE",
            "CREDENTIAL_ENCRYPTION_KEYS_LIVE": f"v1:{KEY_V2}",
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE": "v1",
        },
    )
    assert ring.active_kid == "v1"
    assert ring.key("v1") == bytes.fromhex(KEY_V2)
