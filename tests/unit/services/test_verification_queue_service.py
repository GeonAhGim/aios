"""18.1 VerificationQueueService 단위테스트 (task-4665) — 실DB 없이 커버리지 확보.

conflict-of-interest 필터(verifier != seller)는 SQL WHERE 절 자체에 있어
실DB 없이는 필터링 동작 자체를 검증할 수 없다(tests/integration/
test_verification_queue_service.py가 그 부분을 담당). 여기서는 fake asyncpg
pool/connection으로 (1) 쿼리에 verifier_user_id가 정확히 전달되는지,
(2) row -> QueuedListing 매핑, (3) 빈 결과·null price 등 경계값,
(4) DB 계층 실패주입 시 전파를 고정한다.
"""

from __future__ import annotations

import time
from typing import Any, cast
from uuid import uuid4

import asyncpg
import pytest

from src.services.verification_queue_service import (
    QueuedListing,
    VerificationQueueService,
)


class _FakeConnection:
    def __init__(
        self,
        *,
        fetch_result: list[dict[str, Any]] | None = None,
        raise_on_fetch: Exception | None = None,
    ) -> None:
        self._fetch_result = fetch_result if fetch_result is not None else []
        self._raise_on_fetch = raise_on_fetch
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, query: str, *args: object) -> list[dict[str, Any]]:
        self.fetch_calls.append((query, args))
        if self._raise_on_fetch is not None:
            raise self._raise_on_fetch
        return self._fetch_result


class _FakeAcquire:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConnection:
        return self._conn

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakePool:
    def __init__(self, conn: _FakeConnection) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


def _service(conn: _FakeConnection) -> VerificationQueueService:
    return VerificationQueueService(cast(asyncpg.Pool, _FakePool(conn)))


def _row(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": 1,
        "strategy_id": "test-strategy-abc",
        "strategy_version": "1.0.0",
        "seller_user_id": uuid4(),
        "price": None,
        "created_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# happy path — row -> QueuedListing 매핑, 쿼리 파라미터 전달
# ---------------------------------------------------------------------------


async def test_list_pending_maps_row_fields_and_passes_verifier_id() -> None:
    row = _row()
    conn = _FakeConnection(fetch_result=[row])
    service = _service(conn)
    verifier_id = uuid4()

    queue = await service.list_pending(verifier_id)

    assert len(queue) == 1
    item = queue[0]
    assert isinstance(item, QueuedListing)
    assert item.listing_id == row["id"]
    assert item.strategy_id == row["strategy_id"]
    assert item.strategy_version == row["strategy_version"]
    assert item.seller_user_id == row["seller_user_id"]
    assert item.submitted_at == row["created_at"]
    assert len(conn.fetch_calls) == 1
    _, args = conn.fetch_calls[0]
    assert args == (verifier_id,)


# ---------------------------------------------------------------------------
# negative tests (D2, >=3, 경계값/잘못된 입력 포함)
# ---------------------------------------------------------------------------


async def test_list_pending_returns_empty_list_when_no_pending_rows() -> None:
    """경계값 — 대기중인 리스팅이 없거나 전부 self-listing으로 걸러진
    경우도 빈 리스트이지 오류가 아니다(모듈 docstring 명시 사양)."""
    conn = _FakeConnection(fetch_result=[])
    service = _service(conn)

    queue = await service.list_pending(uuid4())

    assert queue == []


async def test_list_pending_preserves_null_price_boundary() -> None:
    """price는 nullable(QueuedListing.price: Decimal | None) — 경계값으로
    None이 그대로 통과해야 하며 0이나 예외로 치환되면 안 된다."""
    conn = _FakeConnection(fetch_result=[_row(price=None)])
    service = _service(conn)

    queue = await service.list_pending(uuid4())

    assert queue[0].price is None


async def test_list_pending_row_missing_required_field_raises() -> None:
    """잘못된 입력 — DB 스키마와 어긋난 row(필수 컬럼 누락)는 조용히
    무시되지 않고 KeyError로 드러나야 한다(성공 위장 금지)."""
    malformed_row = _row()
    del malformed_row["strategy_id"]
    conn = _FakeConnection(fetch_result=[malformed_row])
    service = _service(conn)

    with pytest.raises(KeyError):
        await service.list_pending(uuid4())


async def test_list_pending_row_with_invalid_seller_user_id_type_raises() -> None:
    """잘못된 입력 — seller_user_id가 UUID로 파싱 불가능한 값이면
    pydantic validation error로 실패해야 한다(경계 밖 데이터가 그대로
    QueuedListing으로 위장 통과하면 안 된다)."""
    conn = _FakeConnection(fetch_result=[_row(seller_user_id="not-a-uuid")])
    service = _service(conn)

    with pytest.raises(Exception):  # noqa: B017 - pydantic ValidationError
        await service.list_pending(uuid4())


# ---------------------------------------------------------------------------
# failure injection (D2)
# ---------------------------------------------------------------------------


async def test_list_pending_propagates_db_fetch_failure() -> None:
    """conn.fetch 자체가 실패(DB 드롭 등)하면 삼키지 않고 그대로 전파한다
    — fail-closed 기본 태세(CLAUDE.md §3)."""
    conn = _FakeConnection(raise_on_fetch=ConnectionError("connection lost"))
    service = _service(conn)

    with pytest.raises(ConnectionError):
        await service.list_pending(uuid4())


# ---------------------------------------------------------------------------
# perf assertion (D2)
# ---------------------------------------------------------------------------


@pytest.mark.perf
async def test_list_pending_perf_budget_p95_latency() -> None:
    """FD-18.1에 전용 예산이 없어 가장 가까운 공개 예산("order submit ->
    ACK p95 50ms, paper", ADR-2026-09-09-C Decision 1)과 동일 자릿수를
    기준으로 삼는다 — 단일 SELECT + in-memory 매핑, fake pool로 서빙."""
    rows = [_row(id=i) for i in range(20)]
    samples = 50
    durations_ms: list[float] = []

    for _ in range(samples):
        conn = _FakeConnection(fetch_result=list(rows))
        service = _service(conn)

        start = time.perf_counter()
        await service.list_pending(uuid4())
        durations_ms.append((time.perf_counter() - start) * 1000)

    durations_ms.sort()
    p95 = durations_ms[int(samples * 0.95) - 1]
    assert p95 < 50.0, (
        f"VerificationQueueService.list_pending p95 latency {p95:.3f}ms exceeded 50ms budget"
    )
