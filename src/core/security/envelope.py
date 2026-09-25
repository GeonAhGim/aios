"""Envelope encryption — per-record DEK.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-32
(+ §2 line 102, §3.6). Generates a fresh DEK (Data Encryption Key) per
record to encrypt the payload, then wraps the DEK itself with one more
layer using the `KeyRing`'s kid key (KEK). Rotation (`rewrap`) only
re-wraps the DEK under a new kid — it does not re-encrypt the payload,
because re-encrypting large payloads on every rotation would be costly
(§9 PLT-32 decision).

AAD design: the wrapped DEK binds `kid` as AAD to prevent kid-confusion
(kid substitution attacks). The payload is encrypted only with a DEK
generated fresh per record (no key sharing across records), so AAD is
not needed for the payload — `rewrap` changes only the kid and leaves
the payload nonce/ciphertext untouched, so binding payload AAD to kid
would break decryption immediately after rotation.
"""
from __future__ import annotations

import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import BaseModel, ConfigDict

from src.core.security.key_ring import KeyRing

_NONCE_SIZE = 12  # AES-GCM 표준 96비트 nonce
_DEK_SIZE = 32  # AES-256


class SealedRecord(BaseModel):
    """Envelope encryption output. `wrapped_dek` is `wrap_nonce(12) +
    wrap_ciphertext` concatenated (nonce-prefix convention matching legacy
    `encryption.py`). `nonce`/`ciphertext` are the payload encrypted with
    the DEK."""

    model_config = ConfigDict(frozen=True)

    kid: str
    wrapped_dek: bytes
    nonce: bytes
    ciphertext: bytes


def seal(plaintext: bytes, ring: KeyRing) -> SealedRecord:
    """Generate a fresh DEK, encrypt `plaintext` with it, and wrap the DEK
    with `ring.active_kid`."""
    kid = ring.active_kid
    dek = os.urandom(_DEK_SIZE)

    body_nonce = os.urandom(_NONCE_SIZE)
    ciphertext = AESGCM(dek).encrypt(body_nonce, plaintext, None)

    wrapped_dek = _wrap_dek(dek, kid, ring)
    return SealedRecord(kid=kid, wrapped_dek=wrapped_dek, nonce=body_nonce, ciphertext=ciphertext)


def open_(rec: SealedRecord, ring: KeyRing) -> bytes:
    """Unwrap the DEK with `rec.kid` and decrypt the payload."""
    dek = _unwrap_dek(rec.wrapped_dek, rec.kid, ring)
    return AESGCM(dek).decrypt(rec.nonce, rec.ciphertext, None)


def rewrap(rec: SealedRecord, ring: KeyRing) -> SealedRecord:
    """Re-wrap only the DEK under `ring.active_kid` — the payload
    (nonce/ciphertext) is copied as-is with no re-encryption cost. The
    returned record can only be decrypted with the new kid (the old
    kid-wrapped `wrapped_dek` is discarded)."""
    dek = _unwrap_dek(rec.wrapped_dek, rec.kid, ring)
    new_kid = ring.active_kid
    new_wrapped_dek = _wrap_dek(dek, new_kid, ring)
    return SealedRecord(
        kid=new_kid, wrapped_dek=new_wrapped_dek, nonce=rec.nonce, ciphertext=rec.ciphertext
    )


def _wrap_dek(dek: bytes, kid: str, ring: KeyRing) -> bytes:
    aad = kid.encode("ascii")
    wrap_nonce = os.urandom(_NONCE_SIZE)
    wrapped = AESGCM(ring.key(kid)).encrypt(wrap_nonce, dek, aad)
    return wrap_nonce + wrapped


def _unwrap_dek(wrapped_dek: bytes, kid: str, ring: KeyRing) -> bytes:
    aad = kid.encode("ascii")
    wrap_nonce, wrapped = wrapped_dek[:_NONCE_SIZE], wrapped_dek[_NONCE_SIZE:]
    return AESGCM(ring.key(kid)).decrypt(wrap_nonce, wrapped, aad)
