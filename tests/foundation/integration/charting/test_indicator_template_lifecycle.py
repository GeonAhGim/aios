"""CH-17b 통합·교차테넌트 테스트 — 실 Postgres(TEST_DATABASE_URL) 대상.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §9.11
CH-17 DoD "템플릿 적용 후 동일 화면 재현, 교차 테넌트 404". `tenant_id`가
`tenant(id)`를 FK하므로(`b5bf8da8e058`) `create_test_tenant()`로 실제
`tenant` 행을 먼저 만든다(`create_test_user()`만으로는 FK 위반)."""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.db.conditional_write import ConcurrencyConflictError
from src.foundation.charting.adapters.postgres_repository import PostgresChartingRepository
from src.foundation.charting.application.create_indicator_template import (
    create_indicator_template,
)
from src.foundation.charting.application.delete_indicator_template import (
    delete_indicator_template,
)
from src.foundation.charting.application.errors import (
    ChartIndicatorTemplateNotFoundError,
    CrossTenantChartIndicatorTemplateAccessError,
)
from src.foundation.charting.application.get_indicator_template import get_indicator_template
from src.foundation.charting.application.list_indicator_templates import (
    list_indicator_templates,
)
from tests.integration.conftest import create_test_tenant


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[4] / ".env")
    url = env.get("DATABASE_URL")
    assert url
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=4)
    yield p
    await p.close()


@pytest.fixture
def repo(pool):
    return PostgresChartingRepository(pool)


async def _tenant(pool) -> object:
    return await create_test_tenant(pool)


def _sample_template() -> dict:
    """CH-17a `templateModel.ts`의 `Template`(`encodeTemplate()` 출력)과
    1:1인 형태 — `schemaVersion`/`panes[].{id,kind,heightRatio}`/
    `indicators[].{id,paneId,params}`. 이 dict가 백엔드를 그대로 왕복해
    `applyTemplate.apply()` 입력으로 재구성 가능해야 한다(디코더가 이
    필드셋 밖의 것을 허용하지 않으므로, 왕복 후에도 정확히 이 키만
    남아야 한다)."""
    return {
        "schemaVersion": 1,
        "panes": [
            {"id": "main", "kind": "main", "heightRatio": 0.7},
            {"id": "sub-1", "kind": "sub", "heightRatio": 0.3},
        ],
        "indicators": [
            {"id": "sma", "paneId": "main", "params": {"period": 20}},
            {"id": "rsi", "paneId": "sub-1", "params": {"period": 14}},
        ],
    }


async def test_create_and_get_round_trips_template_payload(repo, pool):
    tenant_id = await _tenant(pool)
    payload = _sample_template()

    created = await create_indicator_template(
        repo,
        tenant_id=tenant_id,
        owner_subject_id=tenant_id,
        name="my-template",
        template=payload,
    )
    assert created.revision == 0
    assert created.name == "my-template"

    fetched = await get_indicator_template(repo, tenant_id=tenant_id, template_id=created.id)
    assert fetched.id == created.id

    # applyTemplate 입력 재구성 단언: decodeTemplate()의 필드 화이트리스트
    # (schemaVersion/panes/indicators, panes[].{id,kind,heightRatio},
    # indicators[].{id,paneId,params})만 남아 있고, 저장 전 payload와
    # byte-for-byte 동일해야 "동일 화면이 재현"된다.
    assert fetched.template == payload
    assert set(fetched.template.keys()) == {"schemaVersion", "panes", "indicators"}
    for pane in fetched.template["panes"]:
        assert set(pane.keys()) == {"id", "kind", "heightRatio"}
    for indicator in fetched.template["indicators"]:
        assert set(indicator.keys()) <= {"id", "paneId", "params"}

    listed = await list_indicator_templates(repo, tenant_id=tenant_id)
    assert [item.id for item in listed] == [created.id]


async def test_create_duplicate_name_in_same_tenant_raises_concurrency_conflict(repo, pool):
    tenant_id = await _tenant(pool)
    await create_indicator_template(
        repo,
        tenant_id=tenant_id,
        owner_subject_id=tenant_id,
        name="dup",
        template=_sample_template(),
    )

    with pytest.raises(ConcurrencyConflictError):
        await create_indicator_template(
            repo,
            tenant_id=tenant_id,
            owner_subject_id=tenant_id,
            name="dup",
            template=_sample_template(),
        )


async def test_create_same_name_different_tenant_succeeds(repo, pool):
    tenant_a = await _tenant(pool)
    tenant_b = await _tenant(pool)
    await create_indicator_template(
        repo, tenant_id=tenant_a, owner_subject_id=tenant_a, name="shared-name", template={}
    )

    created_b = await create_indicator_template(
        repo, tenant_id=tenant_b, owner_subject_id=tenant_b, name="shared-name", template={}
    )
    assert created_b.name == "shared-name"


async def test_get_indicator_template_unknown_id_raises_not_found(repo, pool):
    tenant_id = await _tenant(pool)
    with pytest.raises(ChartIndicatorTemplateNotFoundError):
        await get_indicator_template(repo, tenant_id=tenant_id, template_id=uuid4())


async def test_get_indicator_template_cross_tenant_raises_not_found_not_forbidden(repo, pool):
    """DoD "교차 테넌트 404" — 존재는 하지만 남의 tenant 소유인 template_id는
    403이 아니라 404다."""
    owner_tenant = await _tenant(pool)
    other_tenant = await _tenant(pool)
    created = await create_indicator_template(
        repo,
        tenant_id=owner_tenant,
        owner_subject_id=owner_tenant,
        name="owner-only",
        template=_sample_template(),
    )

    with pytest.raises(CrossTenantChartIndicatorTemplateAccessError):
        await get_indicator_template(repo, tenant_id=other_tenant, template_id=created.id)


async def test_delete_indicator_template_cross_tenant_raises_not_found(repo, pool):
    owner_tenant = await _tenant(pool)
    other_tenant = await _tenant(pool)
    created = await create_indicator_template(
        repo,
        tenant_id=owner_tenant,
        owner_subject_id=owner_tenant,
        name="mine",
        template=_sample_template(),
    )

    with pytest.raises(CrossTenantChartIndicatorTemplateAccessError):
        await delete_indicator_template(repo, tenant_id=other_tenant, template_id=created.id)

    # 삭제 시도가 실제로는 아무 것도 지우지 않았음을 확인 — 소유자는 여전히
    # 조회 가능해야 한다.
    fetched = await get_indicator_template(repo, tenant_id=owner_tenant, template_id=created.id)
    assert fetched.id == created.id


async def test_delete_indicator_template_removes_row(repo, pool):
    tenant_id = await _tenant(pool)
    created = await create_indicator_template(
        repo,
        tenant_id=tenant_id,
        owner_subject_id=tenant_id,
        name="to-delete",
        template=_sample_template(),
    )
    await delete_indicator_template(repo, tenant_id=tenant_id, template_id=created.id)

    with pytest.raises(ChartIndicatorTemplateNotFoundError):
        await get_indicator_template(repo, tenant_id=tenant_id, template_id=created.id)
