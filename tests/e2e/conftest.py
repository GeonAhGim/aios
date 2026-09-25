"""H-7a 백엔드 e2e 3건 공용 픽스처.

`tests/conftest.py`가 `TEST_DATABASE_URL`을 `DATABASE_URL` 환경변수로 옮겨
두므로(레포 전역 관례), 여기서는 다른 `tests/integration/**/conftest.py`와
동일하게 asyncpg DSN 변환 + 풀 픽스처만 둔다.

task-6690(esc-ci-replay_verify): task-6627/task-6687이 이미 같은 공유 로컬
Postgres 커넥션-리셋 클래스(asyncpg.exceptions.ConnectionDoesNotExistError /
ConnectionResetError[WinError 10054])를 scripts/replay_verify.py와
tests/support/db.py에서 재발행-jitter로 근본 정정했지만, 이 파일의 `pool`
픽스처는 그 보호 없이 순정 `asyncpg.create_pool`을 직접 호출했다 -- E2E 스위트가
local_ci full 모드에서 replay_verify 단계 바로 앞에 돌면서 같은 리셋을 무방비로
맞고, 그 스위트가 초래하는 추가 커넥트/재시도 소음이 뒤이은 replay_verify의
접속 시도가 맞는 경합 창을 넓히는 데 일조한다. `tests/adversarial/risk/
conftest.py`가 이미 쓰는 `create_pool_with_retry`(DECISION_GUIDELINES B-2:
재시도 예산은 그대로, 지터만 추가)로 옮겨 이 파일도 같은 보호를 받게 한다."""

from __future__ import annotations

import os

import pytest

from tests.support.db import create_pool_with_retry


def _asyncpg_dsn() -> str:
    url = os.environ["DATABASE_URL"]
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await create_pool_with_retry(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()
