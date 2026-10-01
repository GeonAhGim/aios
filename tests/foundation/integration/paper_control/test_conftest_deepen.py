"""DEEPEN(task-10103, 원 리프 task-6704 "고아 산출물 회수 5828"): 이 디렉터리의
`conftest.py` 헬퍼(`_asyncpg_dsn`/`request`/`tenant_with_mandate`)가 불변식 위반
입력을 명시적으로 거부하는지, 그리고 의존성(커넥션) 실패 시 조용히 삼키지 않고
그대로 전파(fail-closed)하는지를 검증한다.

INVARIANTS.md 점검: I-01~I-11은 주문 제출/실행-소유권/멱등키/전략 아티팩트/
에이전트 capability 등 실행 경로를 다룬다 — 이 리프는 테스트 전용 DB 픽스처
헬퍼(운영 코드 경로 아님)라 해당 사항 없음(N/A). `request()`가 감싸는
`request_deployment()`의 활성 mandate 요구(PAP-002 계열)와 PAP-006 멱등키
충돌 거부는 운영 코드 자체의 방어이며, 이 리프는 그 방어가 conftest 헬퍼를
통해 호출돼도 우회되지 않는지만 확인한다.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from src.foundation.paper_control.application.request_deployment import (
    IdempotencyKeyConflictError,
    NoActiveMandateError,
)
from src.foundation.paper_control.domain.rules import InvalidProvenanceError
from tests._perf.relative_budget import RelativeBudget
from tests.foundation.integration.paper_control.conftest import (
    _asyncpg_dsn,
    request,
    tenant_with_mandate,
)


def test_asyncpg_dsn_raises_when_database_url_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """negative: `.env`에 `DATABASE_URL`이 없으면(설정 누락) 조용히 빈/잘못된
    DSN을 만들어내지 말고 명시적으로 실패해야 한다."""
    monkeypatch.setattr(
        "tests.foundation.integration.paper_control.conftest.dotenv_values",
        lambda *_args, **_kwargs: {},
    )
    with pytest.raises(AssertionError):
        _asyncpg_dsn()


async def test_request_rejects_tenant_without_active_mandate(repo, mandate_repo) -> None:
    """negative: 활성 mandate가 없는(존재하지 않는) tenant로는 deployment를
    요청할 수 없다 — PAP-002 계열, `request_deployment`가 mandate 조회를
    조용히 건너뛰지 않는지 확인한다."""
    unknown_tenant_id = uuid.uuid4()
    with pytest.raises(NoActiveMandateError):
        await request(repo, mandate_repo, unknown_tenant_id)


async def test_request_rejects_live_endpoint_classification(
    pool, repo, mandate_repo, trust_repo
) -> None:
    """negative: `endpoint_classification`에 "live"가 포함되면
    `validate_provenance`가 LIVE 엔드포인트로 의심해 거부한다(PAP-002) —
    conftest의 `request()` 헬퍼가 이 거부를 흡수해 성공으로 위장하지
    않는지 확인한다."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    with pytest.raises(InvalidProvenanceError):
        await request(repo, mandate_repo, tenant_id, endpoint_classification="LIVE_TRADING")


async def test_request_rejects_idempotency_key_reuse_with_different_content(
    pool, repo, mandate_repo, trust_repo
) -> None:
    """negative: PAP-006 — 같은 (tenant_id, idempotency_key)로 다른 내용의
    요청이 재사용되면 처음 요청을 조용히 재생(replay)하지 말고 명시적으로
    거부해야 한다."""
    tenant_id = await tenant_with_mandate(pool, mandate_repo, trust_repo)
    await request(repo, mandate_repo, tenant_id, key_suffix="-dup")

    with pytest.raises(IdempotencyKeyConflictError):
        await request(
            repo,
            mandate_repo,
            tenant_id,
            key_suffix="-dup",
            package_ref="pkg-ref-DIFFERENT",
        )


class _InjectedConnFailure(RuntimeError):
    pass


class _FailingTransactionCtx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FailingConn:
    def transaction(self) -> _FailingTransactionCtx:
        return _FailingTransactionCtx()

    async def execute(self, *args: object, **kwargs: object) -> None:
        raise _InjectedConnFailure("injected connection failure in execute")


class _FailingAcquireCtx:
    async def __aenter__(self) -> _FailingConn:
        return _FailingConn()

    async def __aexit__(self, *exc_info: object) -> bool:
        return False


class _FailingPool:
    """DB 없이도 실행 가능한 의존성 실패 주입용 이중체 -- 실제 asyncpg.Pool을
    대체하지 않고 `acquire()`만 흉내 낸다."""

    def acquire(self) -> _FailingAcquireCtx:
        return _FailingAcquireCtx()


async def test_repo_get_deployment_by_request_key_propagates_connection_failure() -> None:
    """실패주입: `tenant_transaction()`이 `app.tenant_id` GUC를 설정하려는
    `conn.execute` 호출에서 실패하면 `PostgresPaperControlRepository`는 이를
    삼키고 `None` 같은 값을 조용히 반환해서는 안 되며, 그대로 전파해야
    한다(fail-closed)."""
    from src.foundation.paper_control.adapters.postgres_repository import (
        PostgresPaperControlRepository,
    )

    failing_repo = PostgresPaperControlRepository(_FailingPool())
    with pytest.raises(_InjectedConnFailure, match="injected connection failure"):
        await failing_repo.get_deployment_by_request_key(uuid.uuid4(), "any-key")


@pytest.mark.perf
async def test_tenant_with_mandate_setup_stays_within_local_budget(
    pool: asyncpg.Pool, mandate_repo, trust_repo
) -> None:
    """성능 단언(D2): 기준 왕복 비용(pool.acquire + SELECT 1)과 `tenant_with_
    mandate`(tenant 생성 + mandate 활성화, 여러 순차 INSERT/UPDATE 왕복)를
    같은 `RelativeBudget`가 best-of-N(최솟값)으로 재, 절대 시간이 그 기준의
    40배(연속 DB 왕복 여유, 바닥선 100ms) 이내인지 단언한다 -- 절대 ms 임계는
    실행환경마다 흔들려 회귀 게이트로 못 쓴다. 예산 값(40배/100ms 바닥선)은
    그대로다.

    task-11078(`-n 8` 부하 재현): 이전 구현은 baseline을 23회 연속 측정해
    *최댓값*을 기준으로 삼은 "뒤" op을 단 1회만 측정했다 -- baseline 구간이
    한가한 순간에 걸리고 op 측정 1회가 그 직후 다른 xdist 워커의 부하 버스트와
    겹치면(측정 시점이 떨어져 있어 구조적으로 가능) op만 느려져 적색이 된다
    (관측: baseline p95=0.4ms→budget=100ms인데 op=130.7ms). `RelativeBudget.
    measure_async`는 op/calibration 모두 best-of-N(최솟값, `tests/unit/scripts/
    test_setup_test_db.py`의 동일 기법)을 쓰고 서로 가까운 시점에 측정해, 가장
    덜 간섭받은 표본으로 수렴한다."""

    async def _baseline_round_trip() -> None:
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

    budget = RelativeBudget()
    sample = await budget.measure_async(
        lambda: tenant_with_mandate(pool, mandate_repo, trust_repo),
        n=3,
        warmup=1,
        calibration_n=5,
        calibration_fn=_baseline_round_trip,
    )

    budget_ms = max(100.0, 40 * sample.calibration_ms)
    print(
        f"\ntenant_with_mandate wall={sample.op_ms:.1f}ms "
        f"baseline(SELECT 1)={sample.calibration_ms:.1f}ms budget={budget_ms:.1f}ms"
    )
    assert sample.op_ms < budget_ms, (
        f"tenant_with_mandate took {sample.op_ms:.1f}ms, budget {budget_ms:.1f}ms "
        f"(baseline rt {sample.calibration_ms:.1f}ms)"
    )
