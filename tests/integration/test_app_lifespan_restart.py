"""src/main.py lifespan — 재시작 누수 테스트.

test_app_lifespan.py에서 분리 — lifespan을 연속 두 번 돌려도 잔여 태스크가
남지 않는지만 격리해 다룬다. 공유 `app` 싱글톤은 병렬 실행되는 다른 테스트와
lifespan 상태가 충돌하므로 매번 새 FastAPI 인스턴스를 쓴다.
"""

from __future__ import annotations

import asyncio


async def test_lifespan_can_start_and_stop_twice() -> None:
    """app 재시작(예: 여러 테스트 모듈의 client 픽스처)에도 잔여 태스크가
    누적되지 않는지 — 백그라운드 루프 취소 로직이 매번 완전히 정리하는지 확인."""
    from fastapi import FastAPI

    from src.main import lifespan as _lifespan

    tasks_before = asyncio.all_tasks()

    for _ in range(2):
        test_app = FastAPI(lifespan=_lifespan)
        async with test_app.router.lifespan_context(test_app):
            pass

    tasks_after = asyncio.all_tasks()
    assert tasks_after - tasks_before == set()
