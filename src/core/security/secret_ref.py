"""Opaque secret reference — a value safe to expose in logs, events, and APIs
instead of plaintext or ciphertext.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-32
(+ §3.6). The string representation `secref://<scope>/<kind>/<id>@<kid>` must
match the contract already deployed to the frontend
(`frontend/packages/shared-types/src/secretRef.ts`); serialize scope in
lowercase (`paper`/`live`).
"""

from __future__ import annotations

import re
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict

SecretScope = Literal["PAPER", "LIVE"]
SecretKind = Literal["exchange_credential", "mfa_secret", "withdrawal_dest"]

_SCOPES: dict[str, SecretScope] = {"paper": "PAPER", "live": "LIVE"}
_KINDS: frozenset[str] = frozenset({"exchange_credential", "mfa_secret", "withdrawal_dest"})

_PATTERN = re.compile(r"^secref://([^/]+)/([^/]+)/([^@]+)@(.+)$")


class SecretRefParseError(ValueError):
    """Format/value error from `SecretRef.parse` (fail-closed). The original
    string may contain plaintext (caller mistake), so never echo it in the message."""


class SecretRef(BaseModel):
    """Opaque reference that maps 1:1 to `SecretRef` in
    `frontend/.../secretRef.ts`. Contains no secret material, so safe to
    include directly in logs, events, and API responses."""

    model_config = ConfigDict(frozen=True)

    scope: SecretScope
    kind: SecretKind
    id: str
    kid: str

    def __str__(self) -> str:
        return f"secref://{self.scope.lower()}/{self.kind}/{self.id}@{self.kid}"

    @classmethod
    def parse(cls, s: str) -> SecretRef:
        match = _PATTERN.match(s)
        if match is None:
            raise SecretRefParseError(f"secref 형식이 아닙니다(len={len(s)}).")
        scope_raw, kind_raw, id_, kid = match.groups()
        scope = _SCOPES.get(scope_raw.lower())
        if scope is None:
            raise SecretRefParseError(f"알 수 없는 scope입니다(len={len(scope_raw)}).")
        if kind_raw not in _KINDS:
            raise SecretRefParseError(f"알 수 없는 kind입니다(len={len(kind_raw)}).")
        if not id_ or not kid:
            raise SecretRefParseError("id/kid가 비어 있습니다.")
        return cls(scope=scope, kind=cast(SecretKind, kind_raw), id=id_, kid=kid)
