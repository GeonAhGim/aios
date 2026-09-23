"""Symmetric key management per key version (kid) + rejection of LIVE keys at PAPER runtime.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-31
(+ §2 100 lines, §6 I7). The legacy single `CREDENTIAL_ENCRYPTION_KEY` (07th §7.3) is
absorbed as `kid="legacy"` so existing ciphertexts continue to decrypt.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Literal

from src.core.exceptions import FrozenZoneLiveModeBlockedError

SecretScope = Literal["PAPER", "LIVE"]

_LEGACY_KID = "legacy"
_KEY_BYTES = 32  # AES-256


class KeyRingConfigError(ValueError):
    """CREDENTIAL_ENCRYPTION_KEYS_* environment variable format/content error (fail-closed)."""


class UnknownKeyIdError(KeyError):
    """The kid in the token does not exist in this KeyRing (e.g., old kid lost during rotation)."""


class KeyRing:
    """kid -> 32-byte key mapping + active kid. Immutable (key list cannot change after
    construction)."""

    def __init__(self, keys: Mapping[str, bytes], active_kid: str) -> None:
        if active_kid not in keys:
            raise KeyRingConfigError(
                f"active_kid={active_kid!r}가 keys에 없습니다: {sorted(keys)}"
            )
        self._keys = dict(keys)
        self._active_kid = active_kid

    @property
    def active_kid(self) -> str:
        return self._active_kid

    def key(self, kid: str) -> bytes:
        try:
            return self._keys[kid]
        except KeyError:
            raise UnknownKeyIdError(kid) from None

    def kids(self) -> tuple[str, ...]:
        return tuple(self._keys)

    def with_active_kid(self, new_active_kid: str) -> KeyRing:
        """Return a new `KeyRing` sharing the same key material with a different
        `active_kid` (used by `LocalKeyRingKmsAdapter.rotate`). Raises
        `KeyRingConfigError` if `new_active_kid` is not among the existing keys —
        rotation selects among already-provisioned keys, it does not mint one."""
        return KeyRing(self._keys, new_active_kid)

    @classmethod
    def from_env(cls, scope: SecretScope, *, env: Mapping[str, str] | None = None) -> KeyRing:
        source = env if env is not None else os.environ
        _reject_live_keys_in_paper_runtime(source)

        keys: dict[str, bytes] = {}
        legacy_raw = source.get("CREDENTIAL_ENCRYPTION_KEY")
        if legacy_raw:
            keys[_LEGACY_KID] = _decode_key(legacy_raw, _LEGACY_KID)

        keys_var = f"CREDENTIAL_ENCRYPTION_KEYS_{scope}"
        raw_keys = source.get(keys_var, "")
        for kid, hex_key in _parse_kid_pairs(raw_keys, keys_var):
            keys[kid] = _decode_key(hex_key, kid)

        if not keys:
            raise KeyRingConfigError(
                f"{keys_var} 또는 CREDENTIAL_ENCRYPTION_KEY 중 하나는 설정되어야 합니다."
            )

        kid_var = f"CREDENTIAL_ENCRYPTION_ACTIVE_KID_{scope}"
        active_kid = source.get(kid_var)
        if not active_kid:
            if raw_keys.strip():
                raise KeyRingConfigError(f"{kid_var}가 설정되지 않았습니다.")
            # Environment with only legacy single key (transition) — use as-is
            active_kid = _LEGACY_KID

        return cls(keys, active_kid)

    @classmethod
    def from_legacy_hex(cls, hex_key: str) -> KeyRing:
        """Construct from a single legacy key (`CREDENTIAL_ENCRYPTION_KEY`) only (kid="legacy").

        Allows consumers (PLT-33) whose rotation infrastructure (`CREDENTIAL_ENCRYPTION_KEYS_*`)
        has not yet been wired into `.env.example` to still use the KeyRing contract
        (encrypt/decrypt) with the existing single `SecretBundle.credential_encryption_key`."""
        return cls({_LEGACY_KID: _decode_key(hex_key, _LEGACY_KID)}, active_kid=_LEGACY_KID)


def _reject_live_keys_in_paper_runtime(source: Mapping[str, str]) -> None:
    """fail-closed: skip the guard only when AIOS_RUNTIME_MODE is exactly 'LIVE' (case-insensitive).
    All other values (unset, typo, case variations, etc.) are treated as PAPER,
    so LIVE key presence is checked — preventing I7 from being silently bypassed."""
    runtime_mode = source.get("AIOS_RUNTIME_MODE", "PAPER").strip().upper()
    if runtime_mode == "LIVE":
        return
    if source.get("CREDENTIAL_ENCRYPTION_KEYS_LIVE") or source.get(
        "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE"
    ):
        raise FrozenZoneLiveModeBlockedError(
            "PAPER 런타임(AIOS_RUNTIME_MODE!=LIVE)에는 LIVE 암호화 키가 존재해서는 "
            "안 됩니다(I7, ADR-2026-08-29-E) — CREDENTIAL_ENCRYPTION_KEYS_LIVE/"
            "CREDENTIAL_ENCRYPTION_ACTIVE_KID_LIVE를 제거하세요."
        )


def _redact_entry(entry: str) -> str:
    """Do not leave the raw kid:hex entry in exception messages (prevent secret leakage
    to logs/error trackers). Preserves the kid for identification but keeps only the
    length of the value."""
    if ":" in entry:
        kid_part, _, value_part = entry.partition(":")
        return f"{kid_part.strip()!r}:<REDACTED len={len(value_part.strip())}>"
    return f"<REDACTED len={len(entry)}>"


def _parse_kid_pairs(raw: str, var_name: str) -> list[tuple[str, str]]:
    if not raw.strip():
        return []
    pairs: list[tuple[str, str]] = []
    seen: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise KeyRingConfigError(
                f"{var_name} 형식 오류(kid:hex64 아님): {_redact_entry(entry)}"
            )
        kid, hex_key = entry.split(":", 1)
        kid = kid.strip()
        if not kid:
            raise KeyRingConfigError(
                f"{var_name}에 빈 kid가 있습니다: {_redact_entry(entry)}"
            )
        if kid == _LEGACY_KID:
            raise KeyRingConfigError(f"{var_name}: kid={_LEGACY_KID!r}는 예약어입니다.")
        if kid in seen:
            raise KeyRingConfigError(f"{var_name}에 kid={kid!r}가 중복됩니다.")
        seen.add(kid)
        pairs.append((kid, hex_key.strip()))
    return pairs


def _decode_key(hex_key: str, kid: str) -> bytes:
    try:
        raw = bytes.fromhex(hex_key)
    except ValueError as exc:
        raise KeyRingConfigError(f"kid={kid!r} 키가 유효한 hex 문자열이 아닙니다.") from exc
    if len(raw) != _KEY_BYTES:
        raise KeyRingConfigError(
            f"kid={kid!r} 키는 32바이트(hex 64자)여야 합니다(실제 {len(raw)}바이트)."
        )
    return raw
