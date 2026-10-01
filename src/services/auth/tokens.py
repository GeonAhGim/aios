"""JWT issue/verify — fixed HS256 (algorithm negotiation prohibited),
kid rotation + refresh rotation hash.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §2.2, §3.4, §9 PLT-23.

`TokenVerifier.verify()` fixes the allowed algorithm via
`jwt.decode(algorithms=["HS256"])` — trusting the header's `alg` as-is
enables `alg=none`/HMAC/RSA confusion attacks (§9 test_token_tamper.py),
so PyJWT rejects signatures outside this list with `InvalidAlgorithmError`.
Refresh tokens never store plaintext (only sha256 hex in DB) — rotation/reuse
detection is performed by `session_repository.py` using hashes only.
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
    tid: UUID  # tenant_id (equals sub for personal)
    sid: UUID  # auth_session.id
    jti: UUID  # Token unique id — multiple access tokens per session (new jti per refresh)
    iat: int
    exp: int
    nbf: int
    auth_level: AuthLevel
    schema_version: Literal["v1"] = "v1"


class TokenPairResponse(BaseModel):  # /auth/login, /auth/refresh response data
    access_token: str
    refresh_token: str  # Exposed once in response, never logged (DENY_KEYS "token")
    token_type: Literal["bearer"] = "bearer"
    expires_in: int  # access TTL in seconds
    session_id: UUID


class ClientInfo(BaseModel):  # Request metadata passed to login() — only hash is stored
    ip: str | None = None
    user_agent: str | None = None


class SigningKeyConfigError(ValueError):
    """JWT_SIGNING_KEYS/JWT_ACTIVE_KID environment variable format/content error (fail-closed)."""


class TokenInvalidError(Exception):
    """Signature, claims, or kid verification failure. HTTP mapping (§3.3 AUTH_TOKEN_INVALID)
    is the router's responsibility (PLT-24) — here we raise only stateless exceptions."""


class TokenExpiredError(TokenInvalidError):
    """Token expiration only — allows callers to distinguish refresh hint (§3.3 AUTH_TOKEN_EXPIRED)
    from re-login hint (other AUTH_TOKEN_INVALID) separately."""


def hash_refresh_token(plaintext: str) -> str:
    """SHA256 hex (64 chars) for DB storage. Caller must discard plaintext after response."""
    return hashlib.sha256(plaintext.encode("ascii")).hexdigest()


def _parse_signing_keys(raw: str) -> dict[str, bytes]:
    keys: dict[str, bytes] = {}
    seen: set[str] = set()
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if ":" not in entry:
            raise SigningKeyConfigError("Invalid JWT_SIGNING_KEYS format (expected kid:hex)")
        kid, hex_key = entry.split(":", 1)
        kid = kid.strip()
        hex_key = hex_key.strip()
        if not kid:
            raise SigningKeyConfigError("JWT_SIGNING_KEYS contains an empty kid")
        if kid in seen:
            raise SigningKeyConfigError(f"Duplicate kid={kid!r} in JWT_SIGNING_KEYS")
        seen.add(kid)
        try:
            key_bytes = bytes.fromhex(hex_key)
        except ValueError as exc:
            raise SigningKeyConfigError(f"Key for kid={kid!r} is not a valid hex string") from exc
        if not key_bytes:
            raise SigningKeyConfigError(f"Key for kid={kid!r} is empty")
        keys[kid] = key_bytes
    return keys


def _load_signing_keys(source: Mapping[str, str]) -> tuple[dict[str, bytes], str]:
    keys = _parse_signing_keys(source.get("JWT_SIGNING_KEYS", ""))
    if not keys:
        raise SigningKeyConfigError("JWT_SIGNING_KEYS is not configured")
    active_kid = source.get("JWT_ACTIVE_KID", "")
    if not active_kid:
        raise SigningKeyConfigError("JWT_ACTIVE_KID is not configured")
    if active_kid not in keys:
        raise SigningKeyConfigError(f"JWT_ACTIVE_KID={active_kid!r} is not in JWT_SIGNING_KEYS")
    return keys, active_kid


class TokenIssuer:
    """Issue access JWT + refresh token pair. Signs only with the `active_kid` key."""

    def __init__(self, keys: Mapping[str, bytes], active_kid: str) -> None:
        if active_kid not in keys:
            raise SigningKeyConfigError(f"active_kid={active_kid!r} is not in keys")
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
        """`(plaintext, sha256 hex)` — plaintext for response only, hex stored in DB."""
        plaintext = secrets.token_urlsafe(_REFRESH_BYTES)
        return plaintext, hash_refresh_token(plaintext)


class TokenVerifier:
    """Verify access JWT with kid-keyed keys. alg is always fixed to HS256."""

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
            raise TokenInvalidError("Cannot read token header") from exc

        kid = header.get("kid")
        if not kid or kid not in self._keys:
            raise TokenInvalidError(f"Unknown kid: {kid!r}")

        try:
            payload = jwt.decode(
                token,
                self._keys[kid],
                algorithms=[_ALGORITHM],
                options={"require": ["exp", "iat", "nbf"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise TokenExpiredError("Token has expired") from exc
        except jwt.PyJWTError as exc:
            raise TokenInvalidError("Token signature or claims are invalid") from exc

        try:
            return AccessClaims.model_validate(payload)
        except ValueError as exc:
            raise TokenInvalidError("Token claims schema is invalid") from exc
