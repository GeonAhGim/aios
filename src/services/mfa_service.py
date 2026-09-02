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

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

import asyncpg
import pyotp
from pydantic import BaseModel

from src.core.logging.audit_log import record_audit_log
from src.core.security.encryption import decrypt, encrypt

ISSUER_NAME = "AIOS"


class MfaError(Exception):
    """FD-11.2 실패 — 검증 코드 불일치 등. 라우터가 400으로 변환."""


class MfaSetupResult(BaseModel):
    secret: str
    provisioning_uri: str


class MfaService:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        encryption_key: str,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._pool = pool
        self._encryption_key = encryption_key
        # #13 재사용 방지 테스트가 실제로 30초를 기다리지 않고도 "다음
        # 구간"을 결정적으로 재현할 수 있도록 시계를 주입 가능하게 둔다
        # (watchdog.py의 clock 주입과 동일 원칙, 운영 동작은 기본값 그대로).
        self._now = now

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
            # secret/provisioning_uri은 여기 절대 남기지 않는다 — 설정을
            # 시도했다는 사실 자체만 기록.
            await record_audit_log(
                conn, actor_agent=str(user_id), action_type="mfa.setup",
                user_id=user_id, decision_data={},
            )

        return MfaSetupResult(secret=secret, provisioning_uri=provisioning_uri)

    async def verify(self, user_id: UUID, totp_code: str) -> None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT mfa_secret, mfa_enabled FROM users WHERE user_id = $1", user_id
            )
            encrypted_secret: str | None = row["mfa_secret"] if row is not None else None
            already_enabled = bool(row["mfa_enabled"]) if row is not None else False

            valid = False
            replayed = False
            if encrypted_secret is not None and self._totp_code_valid(
                encrypted_secret, totp_code
            ):
                # 레드팀 감사(#13) — 코드 자체가 유효해도 이미 한 번 성공한
                # 타임코드와 같으면(재사용) 거부한다.
                timecode = self._current_timecode(encrypted_secret)
                valid = await self._consume_timecode(conn, user_id, timecode)
                replayed = not valid

            if not valid:
                if not already_enabled:
                    # 레드팀 감사(#11) — 최초 설정(mfa_enabled=false, 검증
                    # 대기 중) 실패만 secret을 폐기한다("반쯤 활성화된 상태로
                    # 남기지 않는다"는 FD-11.2 원칙은 이 경우에만 적용된다.
                    await conn.execute(
                        "UPDATE users SET mfa_secret = NULL, mfa_enabled = false "
                        "WHERE user_id = $1",
                        user_id,
                    )
                    reset_reason = "replayed" if replayed else "invalid_code"
                    await record_audit_log(
                        conn, actor_agent=str(user_id), action_type="mfa.reset",
                        user_id=user_id,
                        decision_data={"reason": reset_reason, "stage": "initial_setup"},
                    )
                else:
                    # already_enabled=true일 때는 행을 절대 건드리지 않는다 —
                    # 탈취한 Bearer 토큰만으로(비밀번호 없이) 아무 틀린 코드나
                    # 보내 이미 켜진 MFA를 원격으로 영구 비활성화시킬 수 있던
                    # 인증 우회 구멍을 막는다.
                    pass
                await record_audit_log(
                    conn, actor_agent=str(user_id), action_type="mfa.verify_failed",
                    user_id=user_id,
                    decision_data={"reason": "replayed" if replayed else "invalid_code"},
                )
                # 이 엔드포인트(/auth/mfa/verify)는 이미 Bearer 토큰으로 인증된
                # 사용자만 호출하므로(#12의 로그인 타이밍 사이드채널과 무관),
                # "재사용"과 "코드 자체가 틀림"을 구분해줘도 계정 존재 여부 등이
                # 새어나가지 않는다 — 오히려 구분 없이 뭉뚱그리면 사용자가 (특히
                # 방금 전송이 실패해 같은 코드로 재시도했을 때) 정상 코드인데도
                # "코드가 틀렸다"고 오인해 2단계 인증이 고장난 것처럼 보인다.
                if replayed:
                    raise MfaError(
                        "이미 사용한 코드입니다. 인증 앱에 새로 표시되는 코드로 "
                        "다시 시도해주세요."
                    )
                raise MfaError("인증 코드가 올바르지 않습니다.")

            await conn.execute(
                "UPDATE users SET mfa_enabled = true WHERE user_id = $1", user_id
            )
            await record_audit_log(
                conn, actor_agent=str(user_id), action_type="mfa.verify_success",
                user_id=user_id, decision_data={"already_enabled": already_enabled},
            )

    def _totp_code_valid(self, encrypted_secret: str, totp_code: str) -> bool:
        secret = decrypt(encrypted_secret, self._encryption_key)
        return bool(pyotp.totp.TOTP(secret).verify(totp_code, for_time=self._now()))

    def _current_timecode(self, encrypted_secret: str) -> int:
        secret = decrypt(encrypted_secret, self._encryption_key)
        return int(pyotp.totp.TOTP(secret).timecode(self._now()))

    async def _consume_timecode(
        self, conn: asyncpg.Connection, user_id: UUID, timecode: int
    ) -> bool:
        """레드팀 감사(docs/RED_TEAM_FINDINGS.md #13) — 이 타임코드가 이미
        사용된 적이 있으면(재사용) False. 원자적 조건부 UPDATE라 동시에
        같은 코드로 두 번 요청해도 하나만 통과한다."""
        row = await conn.fetchrow(
            "UPDATE users SET mfa_last_used_timecode = $2 "
            "WHERE user_id = $1 "
            "AND (mfa_last_used_timecode IS NULL OR mfa_last_used_timecode < $2) "
            "RETURNING user_id",
            user_id,
            timecode,
        )
        return row is not None

    async def verify_totp_for_login(
        self, user_id: UUID, encrypted_secret: str, totp_code: str
    ) -> bool:
        """AuthService.authenticate()의 verify_totp DI 콜백으로 그대로 주입되는
        진입점."""
        if not self._totp_code_valid(encrypted_secret, totp_code):
            return False
        timecode = self._current_timecode(encrypted_secret)
        async with self._pool.acquire() as conn:
            return await self._consume_timecode(conn, user_id, timecode)
