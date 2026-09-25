"""FA-0b 리팩터(commit 3b290ee, task-1941) 동작보존 증명.

DEPTH 감사(docs/audit/DEPTH_FA.md #1941)가 D0로 판정한 근거는 "리팩터
커밋에 검증 테스트 0건"이었다 — `postgres_repository.py`의
policy_bundle/policy_decision 메서드를 `postgres_policy_repository.py`의
`PostgresPolicyRepositoryMixin`으로 옮긴 커밋 자체는 테스트 파일을 하나도
건드리지 않았다. 이 파일은 그 리팩터가 실제로 "메서드 순수 이동"이었지
재구현이 아니었음을(구조적 동일성) 증명하고, 그 이동의 실제 동기였던
Guard P6.line_cap을 재현하며, 이동된 메서드 중 다른 통합테스트가 직접
왕복시키지 않은 것들(get_bundle/get_policy_decision by id,
get_cached_decision의 만료 처리)을 실 DB로 확인한다."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from src.foundation.mandates.adapters.postgres_policy_repository import (
    PostgresPolicyRepositoryMixin,
)
from src.foundation.mandates.adapters.postgres_repository import PostgresMandateRepository
from src.foundation.mandates.application.evaluate_policy import evaluate as evaluate_policy_command
from src.foundation.mandates.contracts.v1 import PolicyEvaluationSubject
from src.foundation.mandates.domain.models import PolicyDecision, PolicyOutcome
from tests.foundation.integration.mandates.test_policy_repository import _activated_tenant

_REPO_ROOT = Path(__file__).resolve().parents[4]
_ADAPTERS_DIR = _REPO_ROOT / "src" / "foundation" / "mandates" / "adapters"

# --- 구조적 동작보존 증명 (커밋 메시지가 주장하는 "메서드 순수 이동") -------

_MOVED_METHODS = [
    "insert_policy_bundle",
    "get_bundle_for_revision",
    "get_bundle",
    "insert_policy_decision",
    "get_policy_decision",
    "get_cached_decision",
]


@pytest.mark.parametrize("method_name", _MOVED_METHODS)
def test_moved_methods_are_inherited_unchanged_from_mixin(method_name: str) -> None:
    """commit 3b290ee의 "메서드 순수 이동"(재구현 아님) 주장을 실증한다 —
    `PostgresMandateRepository`가 이 메서드들을 자기 클래스 본문에 재정의하지
    않고 `PostgresPolicyRepositoryMixin`에서 그대로 상속받은 동일 함수
    객체를 쓰는지 identity로 확인한다. 앞으로 누군가 병합 충돌 해결
    실수 등으로 `PostgresMandateRepository`에 같은 이름의 메서드를 다시
    정의하면, 그 순간 "이동"이 아니라 "중복 구현"이 된 것을 이 테스트가
    잡는다."""
    assert method_name not in PostgresMandateRepository.__dict__
    assert getattr(PostgresMandateRepository, method_name) is getattr(
        PostgresPolicyRepositoryMixin, method_name
    )


# --- 게이트재현 (P6.line_cap) -----------------------------------------------

_GUARD_SRC_LINE_CAP = 300  # meta/guards/common.py:SRC_LINE_CAP 복제 -- 가드는
# 이 저장소 밖 별도 meta/ 체크아웃에 있어 임포트할 수 없다(동일 패턴:
# tests/unit/core/script/import_/test_pine_parser.py의 게이트재현 테스트).


def _guard_line_count(text: str) -> int:
    """architecture_guard.py P6.line_cap 카운팅 공식 복제: 개행 문자 수 + 1."""
    return text.count("\n") + 1


@pytest.mark.parametrize("module", ["postgres_repository.py", "postgres_policy_repository.py"])
def test_split_files_stay_within_guard_line_cap(module: str) -> None:
    """task-1941이 postgres_repository.py를 분리한 실제 동기였던 가드를
    재현한다 — 분리된 두 파일 모두 지금도 300줄 상한(P6.line_cap) 안에
    있어야 한다. 회귀 시(둘 중 하나가 다시 커지면) 이 테스트가 CI보다
    먼저 잡는다."""
    text = (_ADAPTERS_DIR / module).read_text(encoding="utf-8")
    assert _guard_line_count(text) <= _GUARD_SRC_LINE_CAP


def test_combined_pre_split_line_count_would_still_veto() -> None:
    """분리 전 314줄이 실제로 캡을 넘었다는 사실(가드 veto 사유)을 고정한다
    — 두 파일을 합친 줄 수가 더 이상 상한을 넘지 않게 되면 이 리프의 존재
    이유(분리)가 사라진 것이므로, 그 회귀를 이 테스트가 잡는다."""
    repository_text = (_ADAPTERS_DIR / "postgres_repository.py").read_text(encoding="utf-8")
    policy_repository_text = (_ADAPTERS_DIR / "postgres_policy_repository.py").read_text(
        encoding="utf-8"
    )
    combined_lines = repository_text.count("\n") + policy_repository_text.count("\n") + 2
    assert combined_lines > _GUARD_SRC_LINE_CAP


# --- negative: 이동된 get_cached_decision의 만료 처리 -----------------------


async def test_get_cached_decision_excludes_expired_row(pool, repo, trust_repo):
    """negative — `get_cached_decision`은 만료된(`expires_at`이 과거인) 캐시
    행을 반환하면 안 된다(75번 §3 "짧은 TTL만" fail-closed). 이 WHERE절이
    이동 과정에서 실수로 빠지면(예: AND 절 누락) 만료된 ALLOW 판정이
    재사용되는 결함이 되므로 실 DB 조회로 직접 확인한다. `policy_decision`은
    WORM(UPDATE 금지)이라 기존 행을 만료시킬 수 없어, 이미 만료된
    `expires_at`으로 새 행을 직접 INSERT한다."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    decision = await evaluate_policy_command(
        repo, tenant_id=tenant_id, subject=PolicyEvaluationSubject(command_type="expiry-test")
    )
    fingerprint = f"expired-fp-{uuid4()}"
    now = datetime.now(timezone.utc)
    await repo.insert_policy_decision(
        PolicyDecision(
            id=uuid4(),
            tenant_id=tenant_id,
            bundle_id=decision.bundle_id,
            command_type="expiry-test",
            command_fingerprint=fingerprint,
            outcome=PolicyOutcome.ALLOW,
            reason_codes=(),
            obligations=(),
            evaluated_at=now - timedelta(seconds=60),
            expires_at=now - timedelta(seconds=30),
        )
    )

    cached = await repo.get_cached_decision(tenant_id, fingerprint)
    assert cached is None


# --- 이동된 by-id 조회 메서드의 실 DB 왕복 (explain()의 유일한 소비 경로) ----


async def test_get_bundle_and_get_policy_decision_by_id_round_trip(pool, repo, trust_repo):
    """`get_bundle(id)`/`get_policy_decision(id)` — 다른 통합테스트는
    `get_bundle_for_revision`/`insert_policy_decision`만 직접 확인했을 뿐,
    CM-13 `explain()`이 실제로 쓰는 이 두 by-id 조회는 실 DB로 왕복시킨
    적이 없었다. 이동 과정에서 컬럼/테이블 이름이 잘못 옮겨졌다면 여기서
    None이 반환되거나 예외가 난다."""
    tenant_id = await _activated_tenant(pool, repo, trust_repo)
    decision = await evaluate_policy_command(
        repo, tenant_id=tenant_id, subject=PolicyEvaluationSubject(command_type="explain-test")
    )

    fetched_decision = await repo.get_policy_decision(decision.id)
    assert fetched_decision is not None
    assert fetched_decision.id == decision.id
    assert fetched_decision.bundle_id == decision.bundle_id

    fetched_bundle = await repo.get_bundle(fetched_decision.bundle_id)
    assert fetched_bundle is not None
    assert fetched_bundle.id == fetched_decision.bundle_id
