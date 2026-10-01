"""CM-20 적대적 스위트 DEEPEN(task-10492, 기계 판정 depth_judge 2026-10-01
D2→D3) — `test_bypass_attempts.py`의 책임 분리 파일(ADR-2026-09-10-C §7,
CLAUDE.md #12: 원본 파일이 500줄 경계를 넘기지 않도록 새 D3 증빙만 옆
파일로 분리한다). 새 기능을 추가하지 않는다 — 제품 코드는 건드리지 않고
`test_bypass_attempts.py`가 이미 재사용하는 CM-4/CM-8 포트만 그대로
빌려 쓴다.

여기서 다루는 세 가지(원본 파일의 우회 시도 1~10을 잇는 11번부터):
11. 영속화 실패(`insert_policy_decision` 예외)를 틈타 "판정은 났으니 일단
    통과"로 조용히 fail-open 변질되지 않는지(실패 주입).
12. 우회 시도 2(캐시 재사용)가 겨냥하는 캐시 적중 경로의 왕복 지연 예산
    (성능 단언, `perf_budget` 픽스처).
13. `scripts/check_compliance_gate.py`(CM-8 CI 정적 검사) `main()`이
    ad-hoc `pre_submit_gate=` 대입을 실제로 적색 처리하는지(게이트 적색
    재현).
"""

from __future__ import annotations

from datetime import datetime, timezone

import asyncpg
import pytest

from src.foundation.mandates.application.evaluate_pre_trade import evaluate_pre_trade
from src.foundation.mandates.contracts.v1 import ComplianceVerdict
from tests.adversarial.compliance.test_bypass_attempts import _active_tenant

# --- 11. 영속화 실패를 틈타 "판정은 났으니 일단 통과" 노리는 시도 --------------


async def test_persist_failure_during_deny_propagates_instead_of_silent_allow(
    pool, repo, trust_repo, monkeypatch
):
    """실패 주입 — `evaluate_bundle()`이 이미 메모리상 DENY를 계산한 뒤,
    `repo.insert_policy_decision()`이 DB 연결 단절(`asyncpg.
    ConnectionDoesNotExistError`)로 실패하는 상황을 흉내낸다. 이 우회
    시도는 "판정 로직은 이미 끝났으니 영속화 실패는 무시하고 호출자에게
    ALLOW/성공으로 보고하자"는 조용한 fail-open 변질을 노린다.
    `evaluate_pre_trade`는 영속화를 try/except로 감싸 ALLOW로 눈감지
    않으므로 예외가 그대로 전파돼야 하고, 이번 시도로 인한
    `policy_decision` 행도 커밋되면 안 된다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)

    async def _boom(decision: object) -> object:
        raise asyncpg.exceptions.ConnectionDoesNotExistError("connection lost mid-persist")

    monkeypatch.setattr(repo, "insert_policy_decision", _boom)

    with pytest.raises(asyncpg.exceptions.ConnectionDoesNotExistError):
        await evaluate_pre_trade(
            repo,
            tenant_id=tenant_id,
            portfolio_id=None,
            snapshot={"symbol": "XYZ"},
            now=datetime.now(timezone.utc),
        )

    async with pool.acquire() as conn:
        count = await conn.fetchval(
            "SELECT count(*) FROM policy_decision WHERE tenant_id = $1", tenant_id
        )
    assert count == 0


# --- 12. 성능 단언: 캐시 적중 재판정이 왕복 예산을 지키는지 -------------------


@pytest.mark.perf
async def test_cached_decision_lookup_meets_latency_budget(pool, repo, trust_repo, perf_budget):
    """성능 단언 — 우회 시도 2(캐시 재사용)가 겨냥하는 바로 그 경로, 동일
    `(bundle_hash, inputs_hash)` 지문에 대한 반복 호출이 왕복 지연 예산을
    지키는지 확인한다. 이 방어 로직이 캐시 조회를 생략하고 매번 전체
    룰 재평가 + 추가 쿼리로 퇴화하면(구현 변경으로 인한 회귀), 10회
    왕복의 누적 지연이 눈에 띄게 늘어나 이 예산을 넘긴다."""
    tenant_id = await _active_tenant(pool, repo, trust_repo)
    now = datetime.now(timezone.utc)

    warm = await evaluate_pre_trade(
        repo, tenant_id=tenant_id, portfolio_id=None, snapshot={"symbol": "XYZ"}, now=now
    )
    assert warm.verdict == ComplianceVerdict.DENY

    async def _run() -> None:
        for _ in range(10):
            result = await evaluate_pre_trade(
                repo, tenant_id=tenant_id, portfolio_id=None, snapshot={"symbol": "XYZ"}, now=now
            )
            assert result.verdict == ComplianceVerdict.DENY

    budget_ms = 2000.0  # 실측 로컬 10회 왕복 ~300ms, CI DB 지연 편차 감안 넉넉히
    sample = await perf_budget.sample_async(_run)
    assert sample.wall_ms < budget_ms, perf_budget.describe(sample, budget_ms=budget_ms)


# --- 13. 게이트 적색 재현: CM-8 정적 검사가 ad-hoc 게이트 대입을 실제로 잡는지 --


def test_gate_red_repro_check_compliance_gate_flips_to_red_on_ad_hoc_gate_factory(
    tmp_path, monkeypatch
):
    """게이트 적색 재현 — `scripts/check_compliance_gate.py`(CI에서
    `python scripts/check_compliance_gate.py`로 실행되는 CM-8 정적 검사,
    main() 종료코드 0=통과)가 `pre_submit_gate=`에 `make_foundation_pre_
    submit_gate` 이외의 값을 대입하는 ad-hoc 우회 코드를 실제로 적색
    처리하는지, 개별 내부 함수가 아니라 CI가 직접 호출하는 `main()`
    자체의 종료코드로 증명한다. 가짜 `src` 트리를 만들어 `_REPO_ROOT`만
    바꿔치기하므로 실제 저장소 파일은 건드리지 않는다."""
    import scripts.check_compliance_gate as gate

    app_dir = tmp_path / "src" / "services" / "oms" / "application"
    app_dir.mkdir(parents=True)
    (app_dir / "submit_order.py").write_text(
        "async def submit_order(cmd):\n"
        "    decision_id = result.decision_id\n"
        "    compliance_decision_id = result.compliance_decision_id\n"
        "    return decision_id, compliance_decision_id\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(gate, "_REPO_ROOT", tmp_path)
    assert gate.main() == 0  # 대조군 — 우회 없는 트리는 통과한다

    bypass_dir = tmp_path / "src" / "services" / "oms" / "adapters"
    bypass_dir.mkdir(parents=True)
    (bypass_dir / "sneaky.py").write_text(
        "submit(order, pre_submit_gate=rogue_factory())\n", encoding="utf-8"
    )

    assert gate.main() == 1  # 우회 코드를 섞어 넣자 적색으로 뒤집힌다
