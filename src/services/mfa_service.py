"""11.3 — MFA(TOTP) 설정 (필수 게이트).

Spec: 기능설계문서_v1.20.md#FD-11.2, 13_multi_tenancy_auth_v1.4.md#§13.2

정책문서 §4.10 교차테넌트 리스크3 — MFA는 사용자 레벨에서도 예외 없이
강제한다(자율화 대상은 '승인자 수'이지 '인증 강도'가 아니다). TOTP
secret은 절대 평문 저장하지 않고 AES-256-GCM으로 암호화한다(07번 §7.3
CREDENTIAL_ENCRYPTION_KEY 재사용, src/core/security/encryption.py 공용
유틸).

편차(해석): FD-11.2 원문은 "코드 검증 → mfa_secret 암호화 저장" 순서로
서술하지만, MfaVerifyRequest(15/16번 문서)에는 secret을 다시 실어보낼
필드가 없다 — 즉 verify 시점에 서버가 secret을 어디선가 이미 기억하고
있어야 한다. 여기서는 setup() 시점에 암호화된 secret을 즉시
users.mfa_secret에 저장하되 mfa_enabled=false로 "검증 대기" 상태를
표현하고, verify()가 성공하면 mfa_enabled=true로 확정, 실패하면
mfa_secret을 NULL로 되돌려 폐기한다(반쯤 활성화된 상태로 남기지
않는다는 FD-11.2 예외상황 원칙은 그대로 지킨다).
"""
from __future__ import annotations

from uuid import UUID

import asyncpg
import pyotp
from pydantic import BaseModel

from src.core.security.encryption import decrypt, encrypt

ISSUER_NAME = "AIOS"


class MfaError(Exception):
    """FD-11.2 실패 — 검증 코드 불일치 등. 라우터가 400으로 변환."""


class MfaSetupResult(BaseModel):
    secret: str
    provisioning_uri: str


class MfaService:
    def __init__(self, pool: asyncpg.Pool, *, encryption_key: str) -> None:
        self._pool = pool
        self._encryption_key = encryption_key

    async def setup(self, user_id: UUID, email: str) -> MfaSetupResult:
        secret = pyotp.random_base32()
        provisioning_uri = pyotp.totp.TOTP(secret).provisioning_uri(
            name=email, issuer_name=ISSUER_NAME
        )
        encrypted_secret = encrypt(secret, self._encryption_key)

        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE users SET mfa_secret = $2, mfa_enabled = false WHERE user_id = $1",
                user_id,
                encrypted_secret,
            )

        return MfaSetupResult(secret=secret, provisioning_uri=provisioning_uri)

    async def verify(self, user_id: UUID, totp_code: str) -> None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT mfa_secret, mfa_enabled FROM users WHERE user_id = $1", user_id
            )
            encrypted_secret = row["mfa_secret"] if row is not None else None
            already_enabled = bool(row["mfa_enabled"]) if row is not None else False

            if encrypted_secret is None or not self._check_code(encrypted_secret, totp_code):
                if not already_enabled:
                    # 레드팀 감사(#11) — 최초 설정(mfa_enabled=false, 검증
                    # 대기 중) 실패만 secret을 폐기한다("반쯤 활성화된 상태로
                    # 남기지 않는다"는 FD-11.2 원칙은 이 경우에만 적용된다.
                    await conn.execute(
                        "UPDATE users SET mfa_secret = NULL, mfa_enabled = false "
                        "WHERE user_id = $1",
                        user_id,
                    )
                else:
                    # already_enabled=true일 때는 행을 절대 건드리지 않는다 —
                    # 탈취한 Bearer 토큰만으로(비밀번호 없이) 아무 틀린 코드나
                    # 보내 이미 켜진 MFA를 원격으로 영구 비활성화시킬 수 있던
                    # 인증 우회 구멍을 막는다.
                    pass
                raise MfaError("인증 코드가 올바르지 않습니다.")

            await conn.execute(
                "UPDATE users SET mfa_enabled = true WHERE user_id = $1", user_id
            )

    def _check_code(self, encrypted_secret: str, totp_code: str) -> bool:
        secret = decrypt(encrypted_secret, self._encryption_key)
        return bool(pyotp.totp.TOTP(secret).verify(totp_code))

    async def verify_totp_for_login(self, encrypted_secret: str, totp_code: str) -> bool:
        """AuthService.authenticate()의 verify_totp DI 콜백으로 그대로 주입되는
        진입점 — mfa_secret 컬럼 값(암호화된 채)과 코드만 받아 bool을 반환."""
        return self._check_code(encrypted_secret, totp_code)
