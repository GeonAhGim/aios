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
    DecisionCorruptError,
    PostgresDecisionRepository,
)
from src.foundation.risk_gate.application.replay_decision import BundleNotFoundError, replay
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


async def _seed_decision_with_missing_bundle(
    pool: asyncpg.Pool, decision_repo, bundle_repo, *, tenant_id
):
    """rule_hash가 어떤 risk_rule_bundle 행에도 매칭되지 않는 결정을 심는다
    — CI(753a88c6aeb5)에서 관측된 상황(번들 없는 rule_hash) 재현. INSERT는
    WORM 트리거(BEFORE UPDATE만 잠금) 대상이 아니라 그대로 쓴다."""
    decision = await _seed_decision(pool, decision_repo, bundle_repo, tenant_id=tenant_id)
    orphaned = decision.model_copy(
        update={"decision_id": uuid4(), "rule_hash": uuid4().hex + uuid4().hex}
    )
    await decision_repo.insert(orphaned, {})
    return orphaned


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


async def _null_out_latency(pool: asyncpg.Pool, decision_id) -> None:
    """task-2395 — reproduces CI 77871f678ce2: sets `latency_us` to NULL. The
    normal write path (`RiskDecisionRecorder`) can never produce this row
    (pydantic already rejects it when `RiskDecision.latency_us: int` is
    constructed), so this disables the WORM trigger briefly and updates the
    row directly to simulate a contract-bypassing corrupt row."""
    async with pool.acquire() as conn:
        await conn.execute(f"ALTER TABLE risk_decision DISABLE TRIGGER {_WORM_TRIGGER}")
        try:
            await conn.execute(
                "UPDATE risk_decision SET latency_us = NULL WHERE decision_id = $1",
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


async def test_replay_raises_bundle_not_found_for_orphaned_rule_hash(
    pool, decision_repo, bundle_repo
):
    """task-2174 — 저장된 rule_hash에 매칭되는 번들이 없으면 replay()는
    조용히 넘어가지 않고 BundleNotFoundError를 낸다(불일치를 위장하지 않음)."""
    tenant_id = await create_test_tenant(pool)
    orphaned = await _seed_decision_with_missing_bundle(
        pool, decision_repo, bundle_repo, tenant_id=tenant_id
    )

    with pytest.raises(BundleNotFoundError):
        await replay(decision_repo, bundle_repo, decision_id=orphaned.decision_id)


async def test_cli_reports_bundle_not_found_and_still_checks_the_rest(
    pool, decision_repo, bundle_repo, capsys
):
    """task-2174 — CI가 BundleNotFoundError로 전체가 죽던 것을 고침. 번들이
    없는 결정 하나가 있어도 배치는 죽지 않고 나머지 decision_id를 계속
    재생하며, exit code는 2(불일치 포함)로 실패를 드러낸다(조용한 스킵 금지)."""
    tenant_id = await create_test_tenant(pool)
    since = NOW.replace(year=NOW.year - 1)
    clean = await _seed_decision(pool, decision_repo, bundle_repo, tenant_id=tenant_id)
    orphaned = await _seed_decision_with_missing_bundle(
        pool, decision_repo, bundle_repo, tenant_id=tenant_id
    )

    exit_code = await risk_replay._run(decision_id=None, since=since)

    assert exit_code == 2
    captured = capsys.readouterr()
    assert f"MATCH {clean.decision_id}" in captured.out
    assert "BUNDLE_NOT_FOUND" in captured.err
    assert str(orphaned.decision_id) in captured.err


async def test_replay_raises_decision_corrupt_for_null_latency_us(pool, decision_repo, bundle_repo):
    """task-2395 — reproduces CI 77871f678ce2: for a row with NULL
    `latency_us`, `replay()` raises `DecisionCorruptError`, not
    `pydantic.ValidationError` (no disguised mismatch, no process crash)."""
    tenant_id = await create_test_tenant(pool)
    decision = await _seed_decision(pool, decision_repo, bundle_repo, tenant_id=tenant_id)
    await _null_out_latency(pool, decision.decision_id)

    with pytest.raises(DecisionCorruptError):
        await replay(decision_repo, bundle_repo, decision_id=decision.decision_id)


async def test_cli_reports_decision_corrupt_and_still_checks_the_rest(
    pool, decision_repo, bundle_repo, capsys
):
    """task-2395 negative test — given a mix of one NULL row and one clean
    row, (i) the process does not crash, (ii) the clean row still replays as
    MATCH, and (iii) the exit code is non-zero (the NULL row is not silently
    skipped, it shows up in the exit code)."""
    tenant_id = await create_test_tenant(pool)
    since = NOW.replace(year=NOW.year - 1)
    clean = await _seed_decision(pool, decision_repo, bundle_repo, tenant_id=tenant_id)
    corrupt = await _seed_decision(pool, decision_repo, bundle_repo, tenant_id=tenant_id)
    await _null_out_latency(pool, corrupt.decision_id)

    exit_code = await risk_replay._run(decision_id=None, since=since)

    assert exit_code == 2
    captured = capsys.readouterr()
    assert f"MATCH {clean.decision_id}" in captured.out
    assert "DECISION_CORRUPT" in captured.err
    assert str(corrupt.decision_id) in captured.err


def test_replay_decision_has_no_reimplemented_thresholds():
    """DoD(d) — 판정(임계값 비교) 재구현 0. `Decimal` 미사용 = 수치 임계를
    다룰 능력 자체가 없다는 뜻이고, evaluator 호출 결과만 대조함을 보증한다."""
    source = Path("src/foundation/risk_gate/application/replay_decision.py").read_text(
        encoding="utf-8"
    )
    assert "Decimal" not in source
    assert re.search(r"[<>]=?\s*\d", source) is None
