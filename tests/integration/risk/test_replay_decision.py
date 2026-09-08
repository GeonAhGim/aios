"""R-54 `application/replay_decision.py` + `src/tools/risk_replay.py` 통합테스트
— 실 DB(`TEST_DATABASE_URL`) 대상.

Spec: docs/specs/L4_risk_and_safety_v1.0.md §9 R-54.
DoD: (a) 일치 — replay `match=True`·`diff=={}`, CLI exit 0. (b) 변조 감지 —
WORM 우회(트리거 비활성화 후 직접 UPDATE)로 `reason_codes`를 바꾼 뒤 replay
하면 `match=False`, diff에 `reason_codes` 키, CLI exit 2. (c) `--since` —
2건 중 1건만 변조하면 exit 2 + 불일치 decision_id가 출력에 나옴. (d) 판정
재구현 0 — `replay_decision.py`는 `Decimal`도, 숫자 리터럴 비교도 갖지 않는다
(evaluator 호출 결과만 대조).
"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dotenv import dotenv_values

from src.core.risk.decision import GateKind
from src.core.risk.evaluator import evaluate
from src.core.risk.policy_bundle import BundleState, RiskRuleBundle
from src.foundation.risk_gate.adapters.postgres_bundle_repository import (
    PostgresBundleRepository,
)
from src.foundation.risk_gate.adapters.postgres_decision_repository import (
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.application.replay_decision import replay
from src.tools import risk_replay
from tests.integration.conftest import create_test_tenant
from tests.unit.core.risk._rule_test_helpers import NOW, POLICY, sample_inputs

_WORM_TRIGGER = "risk_decision_worm_guard_trg"


def _asyncpg_dsn() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[3] / ".env")
    url = env.get("DATABASE_URL")
    assert url, ".env에 DATABASE_URL이 없습니다"
    return url.replace("postgresql+asyncpg://", "postgresql://")


@pytest.fixture
async def pool():
    p = await asyncpg.create_pool(_asyncpg_dsn(), min_size=1, max_size=8)
    yield p
    await p.close()


@pytest.fixture
def decision_repo(pool: asyncpg.Pool) -> PostgresDecisionRepository:
    return PostgresDecisionRepository(pool)


@pytest.fixture
def bundle_repo(pool: asyncpg.Pool) -> PostgresBundleRepository:
    return PostgresBundleRepository(pool)


def _bundle() -> RiskRuleBundle:
    return RiskRuleBundle(
        id=uuid4(),
        version=f"v-{uuid4().hex[:8]}",
        rule_hash=uuid4().hex + uuid4().hex,  # 64 hex chars, unique per test run
        engine_version="engine-test-1",
        policy_snapshot=POLICY.model_dump(mode="python"),
        state=BundleState.DRAFT,
        created_by=uuid4(),
    )


async def _seed_decision(pool: asyncpg.Pool, decision_repo, bundle_repo, *, tenant_id):
    bundle = await bundle_repo.insert_draft(_bundle())
    inputs = sample_inputs(tenant_id=tenant_id)
    decision = evaluate(
        inputs, bundle, gate_kind=GateKind.PRE_TRADE, trace_id=uuid4(), now=NOW, ttl=300.0
    )
    await decision_repo.insert(decision, inputs.model_dump(mode="json"))
    return decision


async def _tamper_reason_codes(pool: asyncpg.Pool, decision_id) -> None:
    async with pool.acquire() as conn:
        await conn.execute(f"ALTER TABLE risk_decision DISABLE TRIGGER {_WORM_TRIGGER}")
        try:
            await conn.execute(
                "UPDATE risk_decision SET reason_codes = ARRAY['TAMPERED'] "
                "WHERE decision_id = $1",
                decision_id,
            )
        finally:
            await conn.execute(f"ALTER TABLE risk_decision ENABLE TRIGGER {_WORM_TRIGGER}")


async def test_replay_matches_untampered_decision(pool, decision_repo, bundle_repo):
    tenant_id = await create_test_tenant(pool)
    decision = await _seed_decision(pool, decision_repo, bundle_repo, tenant_id=tenant_id)

    result = await replay(decision_repo, bundle_repo, decision_id=decision.decision_id)

    assert result.match is True
    assert result.diff == {}

    exit_code = await risk_replay._run(decision_id=decision.decision_id, since=None)
    assert exit_code == 0


async def test_replay_detects_tampered_reason_codes(pool, decision_repo, bundle_repo):
    tenant_id = await create_test_tenant(pool)
    decision = await _seed_decision(pool, decision_repo, bundle_repo, tenant_id=tenant_id)
    await _tamper_reason_codes(pool, decision.decision_id)

    result = await replay(decision_repo, bundle_repo, decision_id=decision.decision_id)

    assert result.match is False
    assert "reason_codes" in result.diff

    exit_code = await risk_replay._run(decision_id=decision.decision_id, since=None)
    assert exit_code == 2


async def test_cli_since_reports_only_the_tampered_decision(
    pool, decision_repo, bundle_repo, capsys
):
    tenant_id = await create_test_tenant(pool)
    since = NOW.replace(year=NOW.year - 1)  # 두 결정보다 확실히 이전인 고정 시점
    clean = await _seed_decision(pool, decision_repo, bundle_repo, tenant_id=tenant_id)
    tampered = await _seed_decision(pool, decision_repo, bundle_repo, tenant_id=tenant_id)
    await _tamper_reason_codes(pool, tampered.decision_id)

    exit_code = await risk_replay._run(decision_id=None, since=since)

    assert exit_code == 2
    captured = capsys.readouterr()
    assert str(tampered.decision_id) in captured.err
    assert str(clean.decision_id) not in captured.err


def test_replay_decision_has_no_reimplemented_thresholds():
    """DoD(d) — 판정(임계값 비교) 재구현 0. `Decimal` 미사용 = 수치 임계를
    다룰 능력 자체가 없다는 뜻이고, evaluator 호출 결과만 대조함을 보증한다."""
    source = Path("src/foundation/risk_gate/application/replay_decision.py").read_text(
        encoding="utf-8"
    )
    assert "Decimal" not in source
    assert re.search(r"[<>]=?\s*\d", source) is None
