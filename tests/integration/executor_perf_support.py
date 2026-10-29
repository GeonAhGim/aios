"""Executor ledger query-count measurement proxies."""

import asyncpg


class _QueryCountingConnectionCtx:
    """acquire() 컨텍스트 프록시 — 내부 connection에 query logger를 달아
    순차 DB 왕복 수를 센다(test_perf_journal_append.py와 동일 기법, 단
    record_fill_in_position_ledger는 connection을 노출하지 않고 자체
    pool.acquire()를 여는 어댑터라 pool 자체를 얇게 감싼다)."""

    def __init__(self, inner_ctx: object, sink: list[str]) -> None:
        self._inner_ctx = inner_ctx
        self._sink = sink
        self._conn = None
        self._log = None

    async def __aenter__(self):
        self._conn = await self._inner_ctx.__aenter__()
        self._log = lambda record: self._sink.append(getattr(record, "query", ""))
        self._conn.add_query_logger(self._log)
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        if self._conn is not None and self._log is not None:
            self._conn.remove_query_logger(self._log)
        return await self._inner_ctx.__aexit__(exc_type, exc, tb)


class _QueryCountingPool:
    """`record_fill_in_position_ledger(pool, order)`가 유일하게 쓰는
    `pool.acquire()`만 감싼다 — 저장소들(snapshots/journal/audit)은 pool을
    보관만 하고 재획득하지 않는다(postgres_{snapshot,journal}_repository.py
    확인됨), 그래서 이 얇은 프록시로 전체 호출의 순차 왕복을 빠짐없이 잡는다."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self.queries: list[str] = []

    def acquire(self) -> _QueryCountingConnectionCtx:
        return _QueryCountingConnectionCtx(self._pool.acquire(), self.queries)


