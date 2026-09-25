"""13.10 단위테스트 — DisputeService negative path + 실패주입.

pool/connection을 asyncpg 없이 흉내내는 이유: 실 DB 없이도 검증 로직
(빈 사유, 타인 구매 건, 존재하지 않는 구매 건)과 UniqueViolationError
매핑을 커버하기 위함. 실 DB 대상 통합테스트는
tests/integration/test_dispute_service.py 가 담당한다.
"""
from datetime import datetime, timezone
from uuid import uuid4

import asyncpg
import pytest

from src.services.dispute_service import Dispute, DisputeError, DisputeService


class _FakeAcquireCtx:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, *_exc_info):
        return False


class _FakeConn:
    def __init__(self, purchase_row=None, insert_row=None, insert_exc=None):
        self._purchase_row = purchase_row
        self._insert_row = insert_row
        self._insert_exc = insert_exc

    async def fetchrow(self, query, *args):
        if "strategy_purchases" in query:
            return self._purchase_row
        if "INSERT INTO disputes" in query:
            if self._insert_exc is not None:
                raise self._insert_exc
            return self._insert_row
        raise AssertionError(f"unexpected query: {query}")


class _FakePool:
    def __init__(self, conn):
        self._conn = conn

    def acquire(self):
        return _FakeAcquireCtx(self._conn)


def _service(conn: _FakeConn) -> DisputeService:
    return DisputeService(_FakePool(conn))


async def test_submit_rejects_empty_reason():
    service = _service(_FakeConn())

    with pytest.raises(DisputeError):
        await service.submit(uuid4(), 1, "")


async def test_submit_rejects_whitespace_only_reason():
    service = _service(_FakeConn())

    with pytest.raises(DisputeError):
        await service.submit(uuid4(), 1, "   ")


async def test_submit_rejects_nonexistent_purchase():
    service = _service(_FakeConn(purchase_row=None))

    with pytest.raises(DisputeError):
        await service.submit(uuid4(), 999999999, "사유")


async def test_submit_rejects_other_users_purchase():
    owner = uuid4()
    stranger = uuid4()
    service = _service(_FakeConn(purchase_row={"buyer_user_id": owner}))

    with pytest.raises(DisputeError):
        await service.submit(stranger, 1, "이건 내 구매가 아님")


async def test_submit_rejects_duplicate_open_dispute_via_unique_violation():
    """실패주입: DB의 partial unique index 위반을 asyncpg.UniqueViolationError로
    흉내내어 DisputeService가 이를 DisputeError로 변환하는지 검증한다."""
    buyer = uuid4()
    conn = _FakeConn(
        purchase_row={"buyer_user_id": buyer},
        insert_exc=asyncpg.UniqueViolationError("duplicate key value violates unique constraint"),
    )
    service = _service(conn)

    with pytest.raises(DisputeError):
        await service.submit(buyer, 1, "두 번째 분쟁 시도")


async def test_submit_returns_dispute_on_success():
    buyer = uuid4()
    purchase_id = 42
    insert_row = {
        "id": 7,
        "purchase_id": purchase_id,
        "submitted_by": buyer,
        "reason": "표시된 성과와 실제 결과가 다릅니다",
        "status": "OPEN",
        "created_at": datetime.now(timezone.utc),
    }
    conn = _FakeConn(purchase_row={"buyer_user_id": buyer}, insert_row=insert_row)
    service = _service(conn)

    dispute = await service.submit(buyer, purchase_id, "표시된 성과와 실제 결과가 다릅니다")

    assert isinstance(dispute, Dispute)
    assert dispute.status == "OPEN"
    assert dispute.purchase_id == purchase_id
