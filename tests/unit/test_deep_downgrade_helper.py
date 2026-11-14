"""tests/support/deep_downgrade.py 헬퍼 단위 테스트 -- 가짜 풀/alembic 러너만 쓰고 DB는 필요 없다.

2026-11-14: 이 테스트들이 support 모듈 안에 있어 pytest가 수집하지 않았고,
`tests/unit/meta/test_no_uncollected_tests.py` 가드가 main을 적색으로 만들었다.
수집되는 이 모듈로 옮겼다(헬퍼 파일은 이전 형태로 되돌렸다).
"""

from __future__ import annotations

import pytest

from tests.support.deep_downgrade import (
    _EM3_CHILD_QTY_BACKFILL_PARENT_REVISION,
    _EM3_CHILD_QTY_BACKFILL_REVISION,
    downgrade_past_irreversible_em3_backfill,
    purge_position_snapshots,
)


class _FakeConn:
    def __init__(self, status: str | BaseException) -> None:
        self._status = status
        self.statements: list[str] = []

    async def execute(self, sql: str) -> str:
        self.statements.append(sql)
        if isinstance(self._status, BaseException):
            raise self._status
        return self._status


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *exc: object) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


async def test_purge_returns_deleted_row_count() -> None:
    conn = _FakeConn("DELETE 7")
    assert await purge_position_snapshots(_FakePool(conn)) == 7
    assert conn.statements == ["DELETE FROM pos_snapshot"]


async def test_purge_rejects_malformed_driver_status() -> None:
    # Fail closed: a status without a trailing row count must not be read as 0 rows.
    with pytest.raises(ValueError):
        await purge_position_snapshots(_FakePool(_FakeConn("")))


async def test_purge_failure_injection_propagates_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken_acquire(self: _FakePool) -> None:
        raise ConnectionError("pool exhausted")

    monkeypatch.setattr(_FakePool, "acquire", broken_acquire)
    with pytest.raises(ConnectionError):
        await purge_position_snapshots(_FakePool(_FakeConn("DELETE 0")))


def test_downgrade_stops_before_stamp_when_first_downgrade_fails() -> None:
    calls: list[tuple[str, str]] = []

    def run_alembic(command: str, revision: str) -> None:
        calls.append((command, revision))
        raise RuntimeError("irreversible step")

    with pytest.raises(RuntimeError):
        downgrade_past_irreversible_em3_backfill(run_alembic, "0000aaaa0000")
    # No stamp and no further downgrade: the schema is left where it was.
    assert calls == [("downgrade", _EM3_CHILD_QTY_BACKFILL_REVISION)]


def test_downgrade_does_not_stamp_past_failed_real_downgrade_to_target() -> None:
    calls: list[tuple[str, str]] = []

    def run_alembic(command: str, revision: str) -> None:
        calls.append((command, revision))
        if command == "downgrade" and revision == "0000aaaa0000":
            raise RuntimeError("target downgrade failed")

    with pytest.raises(RuntimeError):
        downgrade_past_irreversible_em3_backfill(run_alembic, "0000aaaa0000")
    # The stamp happens exactly once, before the final real downgrade, never after a failure.
    assert calls.count(("stamp", _EM3_CHILD_QTY_BACKFILL_PARENT_REVISION)) == 1
    assert calls[-1] == ("downgrade", "0000aaaa0000")


def test_downgrade_runs_fixed_three_step_sequence() -> None:
    calls: list[tuple[str, str]] = []

    def run_alembic(command: str, revision: str) -> None:
        calls.append((command, revision))

    downgrade_past_irreversible_em3_backfill(run_alembic, "0000aaaa0000")
    assert calls == [
        ("downgrade", _EM3_CHILD_QTY_BACKFILL_REVISION),
        ("stamp", _EM3_CHILD_QTY_BACKFILL_PARENT_REVISION),
        ("downgrade", "0000aaaa0000"),
    ]
