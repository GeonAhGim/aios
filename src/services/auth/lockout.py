"""PLT-22 — 로그인 실패 잠금 원자화.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md#§9 PLT-22

기존 AuthService._register_failed_attempt()는 SELECT로 읽어둔
failed_login_attempts를 파이썬에서 +1 계산해 UPDATE했다(읽고-쓰기
2왕복 = TOCTOU). 동시 요청 N개가 전부 같은 스냅샷을 읽으면 그중
일부의 증가분이 서로 덮어써져 소실된다(task-329 mandate 동시
activate 경합과 같은 결함 유형). 여기서는 단일 `UPDATE ... RETURNING`
으로 증가와 잠금 판정을 한 왕복에 끝내 그 경합을 제거한다 — Postgres가
대상 행에 잠금을 잡은 채 값을 읽고 쓰므로 동시 UPDATE는 DB 레벨에서
직렬화된다.

라우터 계층(§9 PLT-24, `routers/auth.py` 재작성 예정)이 아직
`AccountLockedError`를 423 + `retry_after_seconds` 응답으로 변환하지
않는다 — 지금은 `AuthError`를 상속해 기존 계정열거 방지 매핑
(`exception_mapping.py`: 모든 AuthError → 401 AUTH_INVALID_CREDENTIALS)
을 그대로 타므로 동작 회귀는 없다. error_code/retry_after_seconds
속성 이름은 이미 머지된 §3.3 error taxonomy·프론트 deriveLockout
(task-387)과 맞춰뒀다 — 라우터 이관 리프가 이름을 바꾸지 않고 그대로
쓸 수 있게 하기 위함이다.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

import asyncpg

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


@dataclass(frozen=True)
class LockoutState:
    failed_attempts: int
    locked: bool
    retry_after_seconds: int | None


def retry_after_seconds(locked_until: datetime | None, now: datetime) -> int | None:
    """잠금 해제까지 남은 초. 잠겨있지 않으면 None.

    프론트 deriveLockout(task-387, accountLockout.ts)이 0 이하 값을 기본
    60초로 클램프하므로, 여기서는 "막 잠긴 순간" 반올림 오차로 0이 나가는
    것을 막기 위해 최소 1초를 보장한다.
    """
    if locked_until is None or locked_until <= now:
        return None
    return max(1, int((locked_until - now).total_seconds()))


async def register_failed_attempt(
    conn: asyncpg.Connection, user_id: UUID, *, now: datetime | None = None
) -> LockoutState:
    """실패 1회를 원자적으로 반영한다.

    `failed_login_attempts + 1`과 잠금 임계값 비교를 SQL 안에서 수행해,
    Python이 미리 읽어둔 카운트를 쓰지 않는다 — 동시 호출 N개가 모두
    이 함수를 호출해도 UPDATE 문 자체가 행 잠금으로 직렬화되므로 최종
    카운트는 정확히 N이 된다(경합 손실 0).
    """
    now = now if now is not None else datetime.now(timezone.utc)
    candidate_locked_until = now + timedelta(minutes=LOCKOUT_MINUTES)
    row = await conn.fetchrow(
        """
        UPDATE users
        SET failed_login_attempts = failed_login_attempts + 1,
            locked_until = CASE
                WHEN failed_login_attempts + 1 >= $2
                     AND (locked_until IS NULL OR locked_until <= $3)
                THEN $4
                ELSE locked_until
            END
        WHERE user_id = $1
        RETURNING failed_login_attempts, locked_until
        """,
        user_id,
        MAX_FAILED_ATTEMPTS,
        now,
        candidate_locked_until,
    )
    if row is None:
        raise ValueError(f"lockout 대상 user_id가 존재하지 않습니다: {user_id}")

    attempts: int = row["failed_login_attempts"]
    locked_until: datetime | None = row["locked_until"]
    return LockoutState(
        failed_attempts=attempts,
        locked=locked_until is not None and locked_until > now,
        retry_after_seconds=retry_after_seconds(locked_until, now),
    )
