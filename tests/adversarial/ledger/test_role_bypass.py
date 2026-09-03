"""LC-17 적대적 — `aios_app` 권한 우회 시도: WORM 트리거 자체를 DISABLE.

Spec: docs/specs/L4_market_data_positions_ledger_v1.0.md#§8.3 LC-17,
L0-3(`src/core/db/append_only.py`)·L0-5(`src/core/db/roles.py`).

`tests/integration/test_db_roles.py`가 이미 `aios_app`으로 WORM 대상
테이블의 UPDATE/DELETE 자체가 막히는지 확인했다(REVOKE·트리거 두 방어층,
그 파일 `_assert_append_only_violation` docstring). 이 리프는 한 단계 더
나아가 "트리거를 꺼서 우회를 시도하면?"을 확인한다. `aios_app`은
`roles.py::ensure_roles_sql`이 부여하는 스키마 기본 DML 권한(SELECT/
INSERT/UPDATE/DELETE)만 갖고 테이블 소유권이 없어 `ALTER TABLE`(DDL,
소유자 전용) 자체가 권한 검사에서 막혀야 한다 — REVOKE·트리거 우회
이전에 애초에 DDL을 실행할 자격이 없다는 뜻으로, 두 방어층보다 더
근본적인 계층에서 막히는지를 검증한다.

`SET ROLE`은 트랜잭션 안에서만 하고, 예외로 트랜잭션이 롤백되면 역할도
함께 원복된다(`test_db_roles.py`와 동일 근거 — PostgreSQL 문서, `SET
ROLE`은 트랜잭션 로컬).
"""
from __future__ import annotations

import asyncpg
import pytest

_WORM_TRIGGER_JOURNAL_ENTRY = "ledger_journal_entry_worm_guard_trg"
_WORM_TRIGGER_POSTING_LINE = "ledger_posting_line_worm_guard_trg"


async def test_aios_app_cannot_disable_worm_trigger_on_journal_entry(pool) -> None:
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                f"ALTER TABLE ledger_journal_entry DISABLE TRIGGER {_WORM_TRIGGER_JOURNAL_ENTRY}"
            )


async def test_aios_app_cannot_disable_worm_trigger_on_posting_line(pool) -> None:
    """분개행 테이블도 저널 헤더와 별도 WORM 트리거를 갖는다(L0-3) — 헤더만
    지키고 행은 놓치는 회귀를 잡기 위해 별도로 확인한다."""
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                f"ALTER TABLE ledger_posting_line DISABLE TRIGGER {_WORM_TRIGGER_POSTING_LINE}"
            )


async def test_aios_app_cannot_drop_worm_trigger(pool) -> None:
    """DISABLE보다 더 파괴적인 DROP TRIGGER도 같은 이유로 막혀야 한다."""
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SET ROLE aios_app")
            await conn.execute(
                f"DROP TRIGGER {_WORM_TRIGGER_JOURNAL_ENTRY} ON ledger_journal_entry"
            )
