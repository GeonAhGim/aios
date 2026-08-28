"""11.6 — 회원탈퇴 API (AccountDeletionService).

Spec: 기능설계문서_v1.20.md#FD-11.4, FD-16, 정책문서 8.10

작업트리 11번 그룹 서문이 명시적으로 FD-16(전략 실행 제어판) 완료
이후로 미뤄뒀던 유일한 리프 — RUNNING 실행 존재 확인이 그 대상 테이블
(strategy_executions)이 있어야 가능했기 때문. FD-16이 끝났으니 이제
착수한다.

재인증(password)은 로그인 시도가 아니라 이미 인증된 세션의 민감 액션
확인이다 — AuthService.authenticate()의 잠금 카운터/MFA 흐름을 타지
않고 비밀번호 해시만 직접 검증한다(재인증 실패가 로그인 실패 잠금
카운터를 건드리면 안 됨 — 별개 관심사).

유예기간(Draft 30일) 중 재로그인 시 탈퇴가 자동 취소된다 —
AuthService.authenticate()가 이 leaf에서 함께 갱신되어 PENDING_DELETION
상태를 감지하면 ACTIVE로 되돌린다.

실제 파기 절차(유예기간 경과 후 PII 익명화 등)는 19.4 법률검토 대상
이라 스콥 밖 — 여기서는 PENDING_DELETION 전이까지만 다룬다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from pydantic import BaseModel

DELETION_GRACE_PERIOD_DAYS = 30  # Draft

_hasher = PasswordHasher()


class AccountDeletionError(Exception):
    """FD-11.4 실패 — 라우터가 400/403/404로 변환."""


class DeletionResult(BaseModel):
    status: str
    deletion_effective_at: datetime


class AccountDeletionService:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def request_deletion(self, user_id: UUID, password: str) -> DeletionResult:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT password_hash FROM users WHERE user_id = $1", user_id
            )
            if row is None:
                raise AccountDeletionError("존재하지 않는 사용자입니다.")

            try:
                _hasher.verify(row["password_hash"], password)
            except VerifyMismatchError as exc:
                raise AccountDeletionError("비밀번호가 일치하지 않습니다.") from exc

            running = await conn.fetch(
                "SELECT id, strategy_id FROM strategy_executions "
                "WHERE user_id = $1 AND status = 'RUNNING'",
                user_id,
            )
            if running:
                blocking = ", ".join(f"{r['strategy_id']}(실행#{r['id']})" for r in running)
                raise AccountDeletionError(
                    f"실행중인 전략을 먼저 중지(FD-16.3)해주세요: {blocking}"
                )

            now = datetime.now(timezone.utc)
            await conn.execute(
                "UPDATE users SET status = 'PENDING_DELETION', deletion_requested_at = $2 "
                "WHERE user_id = $1",
                user_id,
                now,
            )

        return DeletionResult(
            status="PENDING_DELETION",
            deletion_effective_at=now + timedelta(days=DELETION_GRACE_PERIOD_DAYS),
        )
