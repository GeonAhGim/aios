"""FA-13/FA-15 DEEPEN(task-9228) -- negative/실패주입 보강.

원 리프 task-6704(고아 산출물 회수)가 패키지 마커만 남기고 비워 둔 파일이다.
`test_append.py`가 이미 이 디렉터리의 주된 negative/perf/동시성 증거를 갖고
있으므로, 여기서는 그 파일이 다루지 않은 두 공백만 메운다:

1. `append()`가 검증하는 두 번째 tz-aware 필드(`recorded_at`)와 `expected_seq`
   경계값(0) -- `test_append.py`는 `occurred_at` naive·`expected_seq` 2/3만
   다룬다.
2. `append()`가 예상치 못한 의존성 예외(DB 자체가 아니라 드라이버/네트워크
   계층에서 터지는 에러)를 삼키지 않고 그대로 전파하는지(fail-closed).
3. `replay.py`(FA-15)의 `verify_replay`가 한쪽 상태가 통째로 사라진 경우를
   "스킵"이 아니라 mismatch로 잡는지 -- 순수 함수라 실 DB 없이도 검증 가능.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import asyncpg
import pytest

from src.core.eventstore.append import SequenceConflictError, append
from src.core.eventstore.replay import verify_replay

_OCCURRED_AT = datetime(2026, 1, 1, tzinfo=timezone.utc)
_RECORDED_AT = datetime(2026, 1, 1, 0, 0, 1, tzinfo=timezone.utc)


def _stream() -> str:
    return f"order:{uuid4().hex}"


# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


async def test_append_rejects_naive_recorded_at(pool: asyncpg.Pool):
    """`occurred_at`는 aware여도 `recorded_at`가 naive면 거부돼야 한다 --
    `test_append.py::test_append_rejects_naive_datetime`은 `occurred_at`만
    naive로 만들어 본다."""
    stream_id = _stream()

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(ValueError, match="tz-aware"):
            await append(
                conn,
                stream_id=stream_id,
                expected_seq=1,
                type="OrderPlaced",
                payload={"qty": 1},
                occurred_at=_OCCURRED_AT,
                recorded_at=datetime(2026, 1, 1),
            )


async def test_append_rejects_zero_expected_seq_on_empty_stream(pool: asyncpg.Pool):
    """빈 스트림의 head는 seq=0으로 취급되므로, `expected_seq=0`도 `1`이 아닌
    이상 거부돼야 한다 -- 기존 테스트는 2/3(건너뛰기)만 다루고 0/음수
    경계는 다루지 않는다."""
    stream_id = _stream()

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(SequenceConflictError):
            await append(
                conn,
                stream_id=stream_id,
                expected_seq=0,
                type="OrderPlaced",
                payload={"qty": 1},
                occurred_at=_OCCURRED_AT,
                recorded_at=_RECORDED_AT,
            )


async def test_verify_replay_flags_missing_actual_state_as_mismatch():
    """`actual`이 통째로 없어진(`None`) 스트림은 조용히 건너뛰지 않고
    mismatch로 잡혀야 한다 -- replay.py 모듈 docstring의 fail-closed 계약을
    지키는 순수 함수 단위 증거(실 DB 불필요)."""
    report = verify_replay(
        {
            ("orders", "ord-missing"): ({"id": "ord-missing", "qty": 1}, None),
        }
    )

    assert report.ok is False
    assert len(report.mismatches) == 1
    assert report.mismatches[0].domain == "orders"
    assert report.mismatches[0].key == "ord-missing"


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------


async def test_append_propagates_unexpected_dependency_error(
    pool: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
):
    """`append()`가 잡는 예외는 `asyncpg.UniqueViolationError`뿐이다 --
    드라이버/네트워크 계층에서 터지는 다른 예외(예: 커넥션 끊김)는 삼키지
    않고 그대로 호출자에게 전파돼야 한다(fail-closed, 조용한 성공으로
    위장 금지)."""
    stream_id = _stream()

    async def _boom(self: asyncpg.Connection, *args: object, **kwargs: object) -> None:
        raise RuntimeError("dependency exploded")

    monkeypatch.setattr(asyncpg.Connection, "fetchrow", _boom)

    async with pool.acquire() as conn, conn.transaction():
        with pytest.raises(RuntimeError, match="dependency exploded"):
            await append(
                conn,
                stream_id=stream_id,
                expected_seq=1,
                type="OrderPlaced",
                payload={"qty": 1},
                occurred_at=_OCCURRED_AT,
                recorded_at=_RECORDED_AT,
            )
