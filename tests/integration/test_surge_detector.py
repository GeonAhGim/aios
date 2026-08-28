"""10.2 통합테스트 — 실제 dev DB 대상.

각 테스트는 고유한 trigger_source로 격리한다(approval_requests가 전역
테이블이라 다른 테스트의 요청과 섞이지 않도록).
"""
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.approval import service as approval
from src.core.approval.surge import SurgeDetector


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[2] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=2)
    yield p
    await p.close()


async def _create(pool, trigger_source, *, provenance=None):
    return await approval.create_request(
        pool,
        scope="USER",
        user_id=uuid4(),
        trigger_source=trigger_source,
        requested_action="LIQUIDATE_POSITION",
        context={},
        approval_mode="SOLO",
        provenance=provenance,
    )


async def test_not_surging_with_few_requests(pool):
    source = f"test-{uuid4().hex}"
    detector = SurgeDetector(pool)
    await _create(pool, source)

    assert await detector.is_surging(trigger_source=source) is False


async def test_surging_when_many_requests_appear_at_once(pool):
    source = f"test-{uuid4().hex}"
    detector = SurgeDetector(pool)
    for _ in range(20):
        await _create(pool, source)

    assert await detector.is_surging(trigger_source=source) is True


async def test_classify_verified_provenance_goes_to_batch(pool):
    source = f"test-{uuid4().hex}"
    detector = SurgeDetector(pool)
    for _ in range(20):
        await _create(pool, source, provenance="flash_crash_2026_08_28")

    async def verify(provenance):
        return provenance == "flash_crash_2026_08_28"

    result = await detector.classify_for_batch_approval(
        verify_provenance=verify, trigger_source=source
    )

    assert result.is_surging is True
    assert len(result.batch_eligible_ids) == 20
    assert result.individual_review_ids == []


async def test_classify_unverifiable_provenance_falls_to_individual_review(pool):
    source = f"test-{uuid4().hex}"
    detector = SurgeDetector(pool)
    for _ in range(20):
        await _create(pool, source, provenance="forged_tag")

    async def verify(provenance):
        return False  # 시스템 상태와 대조해 근거를 찾을 수 없음

    result = await detector.classify_for_batch_approval(
        verify_provenance=verify, trigger_source=source
    )

    assert result.batch_eligible_ids == []
    assert len(result.individual_review_ids) == 20


async def test_classify_missing_provenance_goes_to_individual_review(pool):
    source = f"test-{uuid4().hex}"
    detector = SurgeDetector(pool)
    for _ in range(20):
        await _create(pool, source, provenance=None)

    async def verify(provenance):
        return True

    result = await detector.classify_for_batch_approval(
        verify_provenance=verify, trigger_source=source
    )
    assert result.individual_review_ids != []
    assert result.batch_eligible_ids == []


async def test_batch_approve_applies_only_to_given_ids(pool):
    source = f"test-{uuid4().hex}"
    detector = SurgeDetector(pool)
    requests = [await _create(pool, source) for _ in range(3)]
    for r in requests:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE approval_requests SET created_at = now() - interval '61 seconds' "
                "WHERE id = $1",
                r.id,
            )

    approver = uuid4()
    approved = await detector.batch_approve([r.id for r in requests], approver)

    assert set(approved) == {r.id for r in requests}
    for r in requests:
        refreshed = await approval.get_request(pool, r.id)
        assert refreshed.status == "APPROVED"
