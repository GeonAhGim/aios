"""JWT 발급/검증 — HS256 고정(알고리즘 협상 금지), kid 회전 + refresh 회전용 해시.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.2, §3.4, §9 PLT-23.

`TokenVerifier.verify()`는 `jwt.decode(algorithms=["HS256"])`로 허용 알고리즘을
고정한다 — 헤더의 `alg`를 그대로 신뢰하면 `alg=none`·HMAC/RSA 혼동 공격이
가능해지므로(§9 test_token_tamper.py), 이 리스트 밖의 서명은 PyJWT가
`InvalidAlgorithmError`로 거부한다. refresh 토큰은 평문을 절대 저장하지
않는다(sha256 hex만 DB에) — 회전·재사용 감지는 `session_repository.py`가
해시만으로 수행한다.
"""
from __future__ import annotations

import hashlib
import os
import secrets
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

import jwt
from pydantic import BaseModel, ConfigDict

ACCESS_TTL_MINUTES = 15
REFRESH_TTL_DAYS = 14
_ALGORITHM = "HS256"
_REFRESH_BYTES = 32

AuthLevel = Literal["PASSWORD", "MFA_VERIFIED"]


class AccessClaims(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sub: UUID  # user_id
    tid: UUID  # tenant_id (personal이면 == sub)
    sid: UUID  # auth_session.id
    jti: UUID  # 토큰 고유 id — 세션당 access 토큰은 여러 개(refresh마다 새 jti)
    iat: int
    exp: int
    nbf: int
    auth_level: AuthLevel
    schema_version: Literal["v1"] = "v1"


class TokenPairResponse(BaseModel):  # /auth/login, /auth/refresh 응답 data
    access_token: str
    refresh_token: str  # 응답에 1회만 노출, 로그 금지(DENY_KEYS "token")
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # access TTL 초
    session_id: UUID


class ClientInfo(BaseModel):  # login()에 넘기는 요청 메타 — 해시만 저장
    ip: str | None = None
    user_agent: str | None = None


class SigningKeyConfigError(ValueError):
    """JWT_SIGNING_KEYS/JWT_ACTIVE_KID 환경변수 형식/내용 오류(fail-closed)."""


class TokenInvalidError(Exception):
    """서명·claims·kid 검증 실패. HTTP 매핑(§3.3 AUTH_TOKEN_INVALID)은 라우터
    책임(PLT-24) — 여기서는 상태를 갖지 않는 순수 예외만 던진다."""


class TokenExpiredError(TokenInvalidError):
    """exp 만료 전용 — 호출자가 refresh 유도(§3.3 AUTH_TOKEN_EXPIRED)와
    재로그인 유도(그 외 AUTH_TOKEN_INVALID)를 구분할 수 있게 분리한다."""


def hash_refresh_token(plaintext: str) -> str:
    """DB에 저장할 sha256 hex(64자). 평문은 호출자가 응답 이후 버려야 한다."""
    return hashlib.sha256(plaintext.encode("ascii")).hexdigest()


def _parse_signing_keys(raw: str) -> dict[str, bytes]:
    keys: dict[str, bytes] = {}
    seen: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise SigningKeyConfigError("JWT_SIGNING_KEYS 형식 오류(kid:hex 아님)")
        kid, hex_key = entry.split(":", 1)
        kid = kid.strip()
        hex_key = hex_key.strip()
        if not kid:
            raise SigningKeyConfigError("JWT_SIGNING_KEYS에 빈 kid가 있습니다")
        if kid in seen:
            raise SigningKeyConfigError(f"JWT_SIGNING_KEYS에 kid={kid!r}가 중복됩니다")
        seen.add(kid)
        try:
            key_bytes = bytes.fromhex(hex_key)
        except ValueError as exc:
            raise SigningKeyConfigError(
                f"kid={kid!r} 키가 유효한 hex 문자열이 아닙니다"
            ) from exc
        if not key_bytes:
            raise SigningKeyConfigError(f"kid={kid!r} 키가 비어 있습니다")
        keys[kid] = key_bytes
    return keys


def _load_signing_keys(source: Mapping[str, str]) -> tuple[dict[str, bytes], str]:
    keys = _parse_signing_keys(source.get("JWT_SIGNING_KEYS", ""))
    if not keys:
        raise SigningKeyConfigError("JWT_SIGNING_KEYS가 설정되지 않았습니다")
    active_kid = source.get("JWT_ACTIVE_KID", "")
    if not active_kid:
        raise SigningKeyConfigError("JWT_ACTIVE_KID가 설정되지 않았습니다")
    if active_kid not in keys:
        raise SigningKeyConfigError(
            f"JWT_ACTIVE_KID={active_kid!r}가 JWT_SIGNING_KEYS에 없습니다"
        )
    return keys, active_kid


class TokenIssuer:
    """access JWT + refresh 토큰 쌍 발급. `active_kid` 키로만 서명한다."""

    def __init__(self, keys: Mapping[str, bytes], active_kid: str) -> None:
        if active_kid not in keys:
            raise SigningKeyConfigError(f"active_kid={active_kid!r}가 keys에 없습니다")
        self._keys = dict(keys)
        self._active_kid = active_kid

    @property
    def active_kid(self) -> str:
        return self._active_kid

    @classmethod
    def from_env(cls, *, env: Mapping[str, str] | None = None) -> TokenIssuer:
        source = env if env is not None else os.environ
        keys, active_kid = _load_signing_keys(source)
        return cls(keys, active_kid)

    def issue_access(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        session_id: UUID,
        auth_level: AuthLevel,
        now: datetime | None = None,
    ) -> str:
        moment = now if now is not None else datetime.now(timezone.utc)
        claims = AccessClaims(
            sub=user_id,
            tid=tenant_id,
            sid=session_id,
            jti=uuid4(),
            iat=int(moment.timestamp()),
            exp=int((moment + timedelta(minutes=ACCESS_TTL_MINUTES)).timestamp()),
            nbf=int(moment.timestamp()),
            auth_level=auth_level,
        )
        payload = claims.model_dump(mode="json")
        return jwt.encode(
            payload,
            self._keys[self._active_kid],
            algorithm=_ALGORITHM,
            headers={"kid": self._active_kid},
        )

    @staticmethod
    def issue_refresh() -> tuple[str, str]:
        """`(평문, sha256 hex)` — 평문은 응답에만, hex는 DB에 저장한다."""
        plaintext = secrets.token_urlsafe(_REFRESH_BYTES)
        return plaintext, hash_refresh_token(plaintext)


class TokenVerifier:
    """kid별 키로 access JWT를 검증한다. alg는 항상 HS256으로 고정."""

    def __init__(self, keys: Mapping[str, bytes]) -> None:
        self._keys = dict(keys)

    @classmethod
    def from_env(cls, *, env: Mapping[str, str] | None = None) -> TokenVerifier:
        source = env if env is not None else os.environ
        keys, _ = _load_signing_keys(source)
        return cls(keys)

    def verify(self, token: str) -> AccessClaims:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise TokenInvalidError("토큰 헤더를 읽을 수 없습니다") from exc

        kid = header.get("kid")
        if not kid or kid not in self._keys:
            raise TokenInvalidError(f"알 수 없는 kid: {kid!r}")

        try:
            payload = jwt.decode(
                token,
                self._keys[kid],
                algorithms=[_ALGORITHM],
                options={"require": ["exp", "iat", "nbf"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError("토큰이 만료되었습니다") from exc
        except jwt.PyJWTError as exc:
            raise TokenInvalidError("토큰 서명 또는 claims가 유효하지 않습니다") from exc

        try:
            return AccessClaims.model_validate(payload)
        except ValueError as exc:
            raise TokenInvalidError("토큰 claims 스키마가 유효하지 않습니다") from exc
