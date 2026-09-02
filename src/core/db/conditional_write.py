"""동시성/원자성 표준 공용 헬퍼.

Spec: AIOSproject 105_concurrency_and_atomicity_engineering_standard_v1.0.md

19개 서비스(dispute_resolution_service, portfolio_service, verification_service,
strategy_builder_service, wallet_service 등)가 "상태를 읽고 검증한 뒤, 그 상태를
UPDATE 조건에 다시 걸고 RETURNING으로 확인한다"는 같은 패턴을 각자 손으로
재구현해왔다. 이 헬퍼는 그 패턴을 한 곳으로 모은다 — 기존 서비스를 강제
마이그레이션하지는 않지만(동작은 이미 올바름), FND-01(src/foundation/) 이후 새
bounded context는 이 헬퍼를 통해서만 조건부 쓰기를 수행한다.
"""
from __future__ import annotations

from typing import Any

import asyncpg


class ConcurrencyConflictError(Exception):
    """읽은 상태와 쓰려는 시점의 실제 상태가 달랐다.

    호출자는 재조회 후 재시도하거나 사용자에게 409로 노출한다 — 이 예외를
    삼키지 않는다.
    """


async def conditional_update(
    conn: asyncpg.Connection,
    *,
    table: str,
    id_column: str,
    id_value: Any,
    expected_state_column: str,
    expected_state_value: Any,
    set_values: dict[str, Any],
    returning: str = "*",
) -> asyncpg.Record:
    """`WHERE <id_column> = $1 AND <expected_state_column> = $2`로 조건부 UPDATE하고
    RETURNING이 빈 결과면 ConcurrencyConflictError를 던진다.

    `table`/`id_column`/`expected_state_column`/`returning`과 `set_values`의
    **키**(컬럼명)는 호출자 코드에 상수로 박혀 있어야 한다(사용자 입력을 그대로
    받지 않는다). 값은 이 함수가 전부 위치 매개변수로 바인딩하므로 호출자가
    `$N` 번호를 직접 셀 필요가 없다 — 컬럼 순서를 잘못 세는 실수를 원천 차단한다.
    """
    set_columns = list(set_values.keys())
    set_clause = ", ".join(f"{col} = ${i + 3}" for i, col in enumerate(set_columns))
    sql = (
        f"UPDATE {table} SET {set_clause} "  # noqa: S608 — 컬럼명은 호출자 상수(위 docstring)
        f"WHERE {id_column} = $1 AND {expected_state_column} = $2 "
        f"RETURNING {returning}"
    )
    params = [id_value, expected_state_value, *(set_values[col] for col in set_columns)]
    row = await conn.fetchrow(sql, *params)
    if row is None:
        raise ConcurrencyConflictError(
            f"{table}.{id_column}={id_value}: 다른 요청이 먼저 처리했습니다"
            "(동시 처리 충돌) — 다시 조회 후 시도하세요."
        )
    return row
