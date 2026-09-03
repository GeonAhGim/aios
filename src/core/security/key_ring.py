"""키 버전(kid)별 대칭키 관리 + PAPER 런타임 LIVE 키 기동 거부.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-31
(+ §2 100행, §6 I7). 레거시 단일 `CREDENTIAL_ENCRYPTION_KEY`(07번 §7.3)는
`kid="legacy"`로 흡수해 기존 암호문이 계속 복호되도록 한다.
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
    """CREDENTIAL_ENCRYPTION_KEYS_* 환경변수 형식/내용 오류(fail-closed)."""


class UnknownKeyIdError(KeyError):
    """토큰에 담긴 kid가 이 KeyRing에 없음(회전 중 구버전 kid 소실 등)."""


class KeyRing:
    """kid -> 32바이트 키 매핑 + 활성 kid. 불변(구성 후 키 목록 변경 불가)."""

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
            active_kid = _LEGACY_KID  # 레거시 단일 키만 있는 환경(전환기) — 그대로 활성 사용

        return cls(keys, active_kid)

    @classmethod
    def from_legacy_hex(cls, hex_key: str) -> KeyRing:
        """단일 레거시 키(`CREDENTIAL_ENCRYPTION_KEY`)만으로 구성한다(kid="legacy").

        회전 인프라(`CREDENTIAL_ENCRYPTION_KEYS_*`)가 아직 `.env.example`에
        배선되지 않은 소비자(PLT-33)가 기존 `SecretBundle.credential_encryption_key`
        하나로도 KeyRing 계약(encrypt/decrypt)을 쓸 수 있게 한다."""
        return cls({_LEGACY_KID: _decode_key(hex_key, _LEGACY_KID)}, active_kid=_LEGACY_KID)


def _reject_live_keys_in_paper_runtime(source: Mapping[str, str]) -> None:
    """fail-closed: AIOS_RUNTIME_MODE가 정확히 'LIVE'(대소문자 무관)로 확인될 때만
    가드를 건너뛴다. 그 외(미설정·오탈자·대소문자 변형 등 애매한 값)는 전부
    PAPER로 취급해 LIVE 키 존재 여부를 검사한다 — I7이 조용히 우회되지 않도록."""
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
    """kid:hex 원본 항목을 예외 메시지에 그대로 남기지 않는다(로그·에러트래커
    시크릿 유출 방지). kid는 식별에 필요하므로 보존하고 값은 길이만 남긴다."""
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
