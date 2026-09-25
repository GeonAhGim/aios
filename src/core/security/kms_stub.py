"""In-memory KMS stub (FA-23) — a `KmsPort` implementation that mints and
retains its own key material, standing in for a real managed KMS backend so
tests/dev environments do not need `LocalKeyRingKmsAdapter`'s pre-provisioned
env vars (FA-22, `key_ring.py`).

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-23
(task-5916, re-issue of task-2680 — the prior leaf was marked done without
the `files` it claimed to add). Depends on FA-22 (`kms_port.KmsPort`).
"""
from __future__ import annotations

import os

from src.core.security import encryption
from src.core.security.key_ring import KeyRing, UnknownKeyIdError

_KEY_BYTES = 32  # AES-256
_INITIAL_KID = "v1"


class KeyNotFoundError(KeyError):
    """`kid` requested from a `KmsStub` (via `get_key`/`decrypt`) is absent —
    e.g. a version minted on a *different* `KmsStub` instance, or a typo'd
    kid. Distinct from `key_ring.UnknownKeyIdError` so callers that only
    depend on `KmsPort` (not `KeyRing`) do not need to import `key_ring` to
    catch it."""


class KmsStubConfigError(ValueError):
    """`rotate()` was asked to mint a key under a `kid` that already exists
    in this stub's key material — rotation always introduces a new version,
    it never overwrites one (fail-closed: silently reusing a `kid` would let
    two different keys answer to the same identifier)."""


class KmsStub:
    """Self-provisioning `KmsPort` adapter: generates its own key material
    instead of reading it from `KeyRing`/env vars, so it needs no setup to
    use in tests or local dev.

    `rotate(new_active_kid)` mints a fresh random key under `new_active_kid`
    and returns a *new* `KmsStub` — mirroring `LocalKeyRingKmsAdapter`'s
    immutability (the receiver is left untouched). All previously minted
    keys are retained in the new instance, so ciphertext produced under an
    older kid stays decryptable after rotation (backward compatibility); the
    original (pre-rotation) instance keeps only the keys it started with, so
    it cannot decrypt ciphertext minted under a kid introduced later by a
    rotated copy.
    """

    def __init__(
        self,
        *,
        initial_kid: str = _INITIAL_KID,
        initial_key: bytes | None = None,
    ) -> None:
        key = initial_key if initial_key is not None else os.urandom(_KEY_BYTES)
        if len(key) != _KEY_BYTES:
            raise KmsStubConfigError(
                f"initial_key는 {_KEY_BYTES}바이트여야 합니다(실제 {len(key)}바이트)."
            )
        self._keys: dict[str, bytes] = {initial_kid: key}
        self._active_kid = initial_kid

    @property
    def active_kid(self) -> str:
        return self._active_kid

    def get_key(self, kid: str) -> bytes:
        try:
            return self._keys[kid]
        except KeyError:
            raise KeyNotFoundError(kid) from None

    def encrypt(self, plaintext: str) -> str:
        return encryption.encrypt(plaintext, self._as_key_ring())

    def decrypt(self, token: str) -> str:
        try:
            return encryption.decrypt(token, self._as_key_ring())
        except UnknownKeyIdError as exc:
            raise KeyNotFoundError(str(exc)) from exc

    def rotate(self, new_active_kid: str) -> KmsStub:
        if new_active_kid in self._keys:
            raise KmsStubConfigError(
                f"kid={new_active_kid!r}는 이미 존재합니다 — rotate()는 항상 새 버전을 "
                "발급해야 합니다(기존 키를 덮어쓰지 않음)."
            )
        rotated = KmsStub.__new__(KmsStub)
        rotated._keys = dict(self._keys)
        rotated._keys[new_active_kid] = os.urandom(_KEY_BYTES)
        rotated._active_kid = new_active_kid
        return rotated

    def _as_key_ring(self) -> KeyRing:
        return KeyRing(self._keys, self._active_kid)
