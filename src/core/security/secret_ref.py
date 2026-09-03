"""불투명 비밀 참조 — 평문·암호문 대신 로그·이벤트·API에 노출해도 되는 값.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-32
(+ §3.6). 문자열 표현 `secref://<scope>/<kind>/<id>@<kid>`는 이미 프론트에
배포된 계약(`frontend/packages/shared-types/src/secretRef.ts`)과 반드시
일치해야 한다 — scope는 소문자(`paper`/`live`)로 직렬화한다.
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
    """`SecretRef.parse` 형식/값 오류(fail-closed). 원본 문자열은 평문을
    담고 있을 가능성(호출부 실수)이 있으므로 메시지에 echo하지 않는다."""


class SecretRef(BaseModel):
    """`frontend/.../secretRef.ts`의 `SecretRef`와 1:1 대응하는 불투명 참조.
    비밀 원문을 담지 않으므로 그대로 로그·이벤트·API에 실어도 된다."""

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
