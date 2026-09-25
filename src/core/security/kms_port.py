"""KMS port — abstracts symmetric key custody behind a `Protocol` so callers
depend on a capability (get_key/encrypt/decrypt/rotate) instead of on
`KeyRing` directly, letting the local env-var-backed `KeyRing` be swapped for
a managed KMS adapter later without touching call sites.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md#§9 FA-22
(task-5915, re-issue of task-2679 — the prior leaf was marked done without
the `files` it claimed to add).
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class KmsPort(Protocol):
    """Symmetric key custody + encrypt/decrypt/rotate contract.

    `encrypt`/`decrypt` operate on the adapter's own active key material
    (mirrors `key_ring.encrypt`/`decrypt`'s `KeyRing` parameter, but bound to
    the adapter instance instead of passed per call). `rotate` returns a new
    port instance pointed at `new_active_kid` — implementations must not
    mutate `self`, matching `KeyRing`'s existing immutability.
    """

    def get_key(self, kid: str) -> bytes:
        """Raise `UnknownKeyIdError` (or an adapter-specific subclass) if `kid` is absent."""
        ...

    def encrypt(self, plaintext: str) -> str:
        ...

    def decrypt(self, token: str) -> str:
        ...

    def rotate(self, new_active_kid: str) -> KmsPort:
        ...
