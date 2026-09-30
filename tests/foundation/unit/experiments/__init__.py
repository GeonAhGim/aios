"""tests/foundation/unit/experiments/__init__.py -- application layer
(record/query/compare) 부정/실패주입/성능 테스트 (fake repo, no DB).

원 리프: task-6704 (고아 산출물 회수 5828 (qa-2))
DEEPEN 대상: task-4084 DEEPEN 기준 -- negative>=3, failure-injection>=1, perf assertion>=1

이 디렉터리의 `test_lineage.py`/`test_contracts_v1.py`는 domain 계층
(`domain/lineage.py`, `contracts/v1.py`)의 순수 규칙을 이미 두껍게 덮지만,
`application/{record,query,compare}.py` 자체는 이 디렉터리에 전용 테스트가
없었다 -- ai/gateway DEEPEN(task-10108)과 같은 공백: "저장소 호출 여부/횟수"
까지 단언 가능한 fake repo 더블로 application 계층을 단위 검증한다
(spec docs/specs/L4_ai_research_strategy_factory_v1.0.md §2.4 AI-11,
"재현 키 동일성" DoD).

불변식 참조:
- `record.py::record_experiment`: `domain/lineage.py::validate_new_experiment`가
  거부하는 candidate는 `repository.append`(실제 WORM INSERT)에 절대 도달하지
  않는다 -- append-only 원장이므로 거부된 행이 잘못 영속화되면 되돌릴 방법이
  없다(모듈 docstring).
- `query.py::get_experiment`: 존재하지 않는 experiment_id와 다른 테넌트 소유
  experiment_id를 구분하지 않고 둘 다 `ExperimentNotFoundError`로 수렴한다
  (교차 테넌트 존재 누출 금지, gateway의 404 컨벤션과 동일 클래스).
- `compare.py::compare_experiments`/`compare_by_reproducibility_key`: 한
  reproducibility_key 아래 서로 다른 inputs_hash가 섞여 있으면(쓰기 경로
  가드를 우회한 손상 데이터) `CorruptedReproductionSetError`로 거부하고,
  서로 다른 reproducibility_key를 비교하려는 요청은 `ReproducibilityKeyMismatchError`
  로 거부한다 -- 둘 다 이 모듈이 쓰기 경로 가드를 다시 신뢰하지 않고 읽기
  경로에서 재검증한다는 것을 보인다(모듈 docstring).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from src.foundation.experiments.application.compare import (
    CorruptedReproductionSetError,
    NoReproductionsFoundError,
    ReproducibilityKeyMismatchError,
    compare_by_reproducibility_key,
    compare_experiments,
)
from src.foundation.experiments.application.query import (
    ExperimentNotFoundError,
    get_experiment,
    get_lineage_chain,
)
from src.foundation.experiments.application.record import record_experiment
from src.foundation.experiments.contracts.v1 import Experiment, ExperimentKind
from src.foundation.experiments.domain.lineage import (
    DanglingParentError,
    ReproducibilityKeyCollisionError,
)

_NOW = datetime(2026, 9, 17, 0, 0, 0, tzinfo=timezone.utc)
_HASH_A = "a" * 64
_HASH_B = "b" * 64


@dataclass
class FakeExperimentRepository:
    """`PostgresExperimentRepository`(`adapters/postgres_repository.py`)와
    같은 메서드 시그니처를 흉내내는 인메모리 더블 -- append(실제 WORM INSERT)
    호출 여부/횟수를 단언 가능하게 만든다(ai/gateway DEEPEN의
    `FakeAgentTokenRepository`와 같은 역할)."""

    by_id: dict[UUID, Experiment] = field(default_factory=dict)
    append_calls: int = 0
    raise_on_get: Exception | None = None

    async def append(self, experiment: Experiment) -> None:
        self.append_calls += 1
        self.by_id[experiment.experiment_id] = experiment

    async def get(self, tenant_id: UUID, experiment_id: UUID) -> Experiment | None:
        if self.raise_on_get is not None:
            raise self.raise_on_get
        experiment = self.by_id.get(experiment_id)
        if experiment is None or experiment.tenant_id != tenant_id:
            return None
        return experiment

    async def find_by_reproducibility_key(
        self, tenant_id: UUID, reproducibility_key: str
    ) -> tuple[Experiment, ...]:
        return tuple(
            e
            for e in self.by_id.values()
            if e.tenant_id == tenant_id and e.reproducibility_key == reproducibility_key
        )


def _make_experiment(
    *,
    experiment_id: UUID | None = None,
    tenant_id: UUID,
    reproducibility_key: str = _HASH_A,
    inputs_hash: str = _HASH_A,
    parent_id: UUID | None = None,
    metrics: dict[str, object] | None = None,
) -> Experiment:
    return Experiment(
        experiment_id=experiment_id or uuid4(),
        tenant_id=tenant_id,
        reproducibility_key=reproducibility_key,
        kind=ExperimentKind.BACKTEST,
        inputs_hash=inputs_hash,
        metrics=metrics or {},
        artifacts=(),
        parent_id=parent_id,
        created_by=uuid4(),
        created_at=_NOW,
    )


# ──────────────────────────────────────────────────────────────────────
# 1. Negative tests -- 불변식 위반 입력을 명시적으로 거부
# ──────────────────────────────────────────────────────────────────────


class TestNegativeRecordExperimentDanglingParent:
    async def test_negative_dangling_parent_never_reaches_append(self) -> None:
        """부정: 존재하지 않는 parent_id를 가리키는 candidate는
        DanglingParentError로 거부되며, repository.append(실제 WORM INSERT)
        는 단 한 번도 호출되지 않는다."""
        repo = FakeExperimentRepository()
        tenant = uuid4()
        candidate = _make_experiment(tenant_id=tenant, parent_id=uuid4())

        with pytest.raises(DanglingParentError):
            await record_experiment(repo, candidate)

        assert repo.append_calls == 0


class TestNegativeRecordExperimentReproducibilityKeyCollision:
    async def test_negative_same_key_different_inputs_hash_never_reaches_append(self) -> None:
        """부정: 같은 reproducibility_key 아래 다른 inputs_hash를 가진
        candidate는 ReproducibilityKeyCollisionError로 거부되며,
        repository.append는 호출되지 않는다."""
        repo = FakeExperimentRepository()
        tenant = uuid4()
        existing = _make_experiment(
            tenant_id=tenant, reproducibility_key=_HASH_A, inputs_hash=_HASH_A
        )
        repo.by_id[existing.experiment_id] = existing
        candidate = _make_experiment(
            tenant_id=tenant, reproducibility_key=_HASH_A, inputs_hash=_HASH_B
        )

        with pytest.raises(ReproducibilityKeyCollisionError):
            await record_experiment(repo, candidate)

        assert repo.append_calls == 0


class TestNegativeGetExperimentCrossTenant:
    async def test_negative_cross_tenant_read_raises_not_found_without_leaking_existence(
        self,
    ) -> None:
        """부정: 다른 테넌트 소유 experiment_id 조회는 존재를 드러내지 않고
        ExperimentNotFoundError로 수렴한다 -- 존재하지 않는 id와 동일한
        예외(교차 테넌트 존재 누출 금지)."""
        repo = FakeExperimentRepository()
        owner, stranger = uuid4(), uuid4()
        experiment = _make_experiment(tenant_id=owner)
        repo.by_id[experiment.experiment_id] = experiment

        with pytest.raises(ExperimentNotFoundError):
            await get_experiment(repo, stranger, experiment.experiment_id)


class TestNegativeCompareExperimentsMismatchedKeys:
    async def test_negative_comparing_different_reproducibility_keys_raises(self) -> None:
        """부정: 서로 다른 reproducibility_key를 가진 experiment_id 목록을
        비교하려는 요청은 ReproducibilityKeyMismatchError로 거부된다 --
        임의의 diff는 이 모듈의 역할이 아니다(모듈 docstring)."""
        repo = FakeExperimentRepository()
        tenant = uuid4()
        first = _make_experiment(tenant_id=tenant, reproducibility_key=_HASH_A)
        second = _make_experiment(tenant_id=tenant, reproducibility_key=_HASH_B)
        repo.by_id[first.experiment_id] = first
        repo.by_id[second.experiment_id] = second

        with pytest.raises(ReproducibilityKeyMismatchError):
            await compare_experiments(repo, tenant, [first.experiment_id, second.experiment_id])


class TestNegativeCompareByReproducibilityKeyCorruptedSet:
    async def test_negative_divergent_inputs_hash_under_same_key_raises_corrupted(self) -> None:
        """부정: 쓰기 경로 가드를 우회해 같은 reproducibility_key 아래
        서로 다른 inputs_hash가 섞여 있는 손상 데이터는
        CorruptedReproductionSetError로 거부된다 -- 쓰기 경로 가드를 다시
        신뢰하지 않고 읽기 경로에서 재검증한다."""
        repo = FakeExperimentRepository()
        tenant = uuid4()
        first = _make_experiment(tenant_id=tenant, reproducibility_key=_HASH_A, inputs_hash=_HASH_A)
        second = _make_experiment(
            tenant_id=tenant, reproducibility_key=_HASH_A, inputs_hash=_HASH_B
        )
        repo.by_id[first.experiment_id] = first
        repo.by_id[second.experiment_id] = second

        with pytest.raises(CorruptedReproductionSetError):
            await compare_by_reproducibility_key(repo, tenant, _HASH_A)


class TestNegativeCompareByReproducibilityKeyNoReproductions:
    async def test_negative_unknown_reproducibility_key_raises_no_reproductions(self) -> None:
        """부정: 이 테넌트에 존재하지 않는 reproducibility_key는
        NoReproductionsFoundError로 거부된다."""
        repo = FakeExperimentRepository()

        with pytest.raises(NoReproductionsFoundError):
            await compare_by_reproducibility_key(repo, uuid4(), _HASH_A)


# ──────────────────────────────────────────────────────────────────────
# 2. Failure-injection tests -- 의존성/경계 예외 유발
# ──────────────────────────────────────────────────────────────────────


class TestFailureInjectionGetExperimentRepoOutage:
    async def test_failure_injection_repo_get_crash_propagates(self) -> None:
        """실패주입: repository.get이 (찾지 못함이 아니라) RuntimeError로
        크래시하면 get_experiment()는 이를 삼키지 않고 그대로 전파한다
        (fail-closed 기본 자세, CLAUDE.md §3)."""
        repo = FakeExperimentRepository(raise_on_get=RuntimeError("db outage"))

        with pytest.raises(RuntimeError, match="db outage"):
            await get_experiment(repo, uuid4(), uuid4())


class TestFailureInjectionLineageChainRepoOutage:
    async def test_failure_injection_repo_crash_during_lineage_walk_propagates(self) -> None:
        """실패주입: get_lineage_chain이 부모를 따라 올라가는 도중
        repository.get이 크래시하면 부분적으로 조립된 체인을 숨기지 않고
        예외를 그대로 전파한다."""
        tenant = uuid4()
        child = _make_experiment(tenant_id=tenant, parent_id=uuid4())
        repo = FakeExperimentRepository()
        repo.by_id[child.experiment_id] = child
        repo.raise_on_get = RuntimeError("db outage mid-walk")

        with pytest.raises(RuntimeError, match="db outage mid-walk"):
            await get_lineage_chain(repo, tenant, child.experiment_id)


# ──────────────────────────────────────────────────────────────────────
# 3. Performance assertion
# ──────────────────────────────────────────────────────────────────────

_RECORD_EXPERIMENT_BUDGET_MS = 5.0


class TestPerformanceRecordExperimentApplicationLayer:
    @pytest.mark.perf
    async def test_perf_record_experiment_full_application_path_within_budget(self) -> None:
        """성능단언: record_experiment()는 parent 조회 + 동일 키 조회 +
        순수 판정 + append를 합친 전체 경로에 예산을 건다 -- ai/gateway
        DEEPEN의 authorize 전체 경로 예산(5ms)과 같은 급의 fake-repo 왕복
        비용."""
        repo = FakeExperimentRepository()
        tenant = uuid4()

        samples: list[float] = []
        for _ in range(200):
            candidate = _make_experiment(
                tenant_id=tenant, reproducibility_key=_HASH_B, inputs_hash=_HASH_B
            )
            started = time.perf_counter()
            await record_experiment(repo, candidate)
            samples.append((time.perf_counter() - started) * 1000)
        samples.sort()
        p95_ms = samples[min(int(len(samples) * 0.95), len(samples) - 1)]
        print(
            f"[AI-11 record_experiment] application-layer p95={p95_ms:.4f}ms "
            f"budget<{_RECORD_EXPERIMENT_BUDGET_MS:.1f}ms"
        )
        assert p95_ms < _RECORD_EXPERIMENT_BUDGET_MS
