"""U-3a 적대적 테스트 -- AI 어시스턴트 경로는 구조적으로 주문 실행에 접근할
수 없다(임포트 그래프 정적 검사).

Spec DoD: "실행은 절대 자동으로 하지 않는다(주문 경로 접근 금지)". 진짜
안전 경계는 "이 모듈 트리 어디에도 실행 경로를 임포트하는 파일이 없다"는
사실이다 -- 아무리 프롬프트 인젝션으로 provider가 악성 스크립트를 생성해도,
이 트리에는 그 스크립트를 실행할 함수 자체가 없다(컴파일까지만 한다).

INVARIANTS.md I-12(ADR-2026-09-26-B Decision 3, AIS-1)가 이 파일을 그
불변식의 강제 지점으로 명시한다. 위 정적 검사는 임포트 그래프만 보므로
I-12의 "PAPER 승격·실행은 confirm ticket 필수" 절반은 다른 종류의 증거가
필요하다 -- 그래서 아래 세 케이스(no ticket / reused ticket / expired
ticket)는 정적 검사가 아니라, confirm ticket이 없거나 이미 무효화됐을 때
`promote_to_paper`를 호출하면 실제로 거부되고 PAPER 배포가 생성되지
않음을 실행 시점에 확인하는 동적 케이스다(`promote_to_paper`의 게이트
순서 자체는 `tests/foundation/unit/ai/factory/test_promote_to_paper.py`에서
이미 D2/D3 깊이로 다뤄지므로, 여기서는 그 순서를 다시 검증하지 않고
I-12가 요구하는 "유효한 확인 없이는 도달 불가"라는 사실만 재확인한다).
"""

from __future__ import annotations

import ast
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from src.foundation.ai.factory.application.promote_to_paper import (
    ConfirmTicketExpiredError,
    ConfirmTicketNotFoundError,
    ConfirmTicketReusedError,
    promote_to_paper,
)
from src.foundation.paper_control.contracts.v1 import PaperDeploymentView
from tests.foundation.unit.ai.factory._promote_to_paper_fakes import (
    DIGEST,
    NOW,
    Deps,
    make_evaluation,
    make_proposal,
    make_ticket,
)

FORBIDDEN_MODULE_PREFIXES = (
    "src.core.executor",
    "src.services.execution_loop",
    "src.exchanges",
    "src.foundation.execution_ownership",
    "src.foundation.ems",
    "src.foundation.oms",
    "src.foundation.order_service",
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
ASSISTANT_ROOTS = (
    _REPO_ROOT / "src" / "foundation" / "ai" / "assistant",
    _REPO_ROOT / "src" / "api" / "routers" / "assistant.py",
)


def _iter_py_files() -> list[Path]:
    files: list[Path] = []
    for root in ASSISTANT_ROOTS:
        if root.is_dir():
            files.extend(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)
        elif root.is_file():
            files.append(root)
    return files


def _imported_module_names(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def test_assistant_tree_never_imports_execution_paths() -> None:
    files = _iter_py_files()
    assert files, "assistant 소스 트리를 찾지 못함 -- 경로 점검 필요"

    violations: list[tuple[str, str]] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module_name in _imported_module_names(tree):
            if any(
                module_name == prefix or module_name.startswith(prefix + ".")
                for prefix in FORBIDDEN_MODULE_PREFIXES
            ):
                violations.append((str(path), module_name))

    assert violations == []


async def _attempt_promotion(
    deps: Deps, *, ticket_id: UUID, execute_digest: str = DIGEST
) -> PaperDeploymentView:
    """Shared call shape for the I-12 confirm-ticket negative cases below --
    only `ticket_id`/`execute_digest` vary per case, everything else is a
    fixed, unremarkable PAPER promotion request."""
    proposal = make_proposal()
    evaluation = make_evaluation(proposal)
    return await promote_to_paper(
        proposal=proposal,
        evaluation=evaluation,
        ticket_id=ticket_id,
        execute_digest=execute_digest,
        confirm_repo=deps.confirm_repo,
        paper_repo=deps.paper_repo,
        risk_repo=deps.risk_repo,
        mandate_repo=deps.mandate_repo,
        connection_repo=deps.connection_repo,
        tenant_id=deps.tenant_id,
        actor_subject_id=uuid4(),
        connection_id=None,
        adapter_type="paper_sim",
        provider_sandbox_account_ref="acct-1",
        endpoint_classification="sandbox",
        idempotency_key=str(uuid4()),
        clock=lambda: NOW,
    )


@pytest.mark.asyncio
async def test_promote_to_paper_rejects_paper_promotion_without_confirm_ticket() -> None:
    """I-12 negative case 1: calling `promote_to_paper` with a `ticket_id`
    that was never issued (no confirm ticket exists) must raise
    `ConfirmTicketNotFoundError` and must never reach the risk gate or
    create a PAPER deployment -- confirming, at the one call site the
    AI factory has for reaching PAPER, that "no confirm ticket" is not a
    bypassable precondition but a hard stop before any execution-adjacent
    side effect."""
    deps = Deps()

    with pytest.raises(ConfirmTicketNotFoundError):
        # never seeded into confirm_repo -- no ticket was ever issued
        await _attempt_promotion(deps, ticket_id=uuid4())

    assert deps.paper_repo.insert_deployment_calls == 0
    assert deps.risk_repo.list_active_controls_calls == 0  # rejected before the risk gate runs


@pytest.mark.asyncio
async def test_promote_to_paper_rejects_already_consumed_confirm_ticket() -> None:
    """I-12 negative case 2: a confirm ticket that was already consumed by
    an earlier promotion must not authorize a second one -- single-use is
    not optional, and reuse must raise `ConfirmTicketReusedError` rather
    than silently allowing a second PAPER deployment through a stale
    ticket."""
    deps = Deps()
    ticket = make_ticket()
    deps.confirm_repo.seed(ticket)

    first = await _attempt_promotion(deps, ticket_id=ticket.ticket_id)
    assert first is not None
    assert deps.paper_repo.insert_deployment_calls == 1

    with pytest.raises(ConfirmTicketReusedError):
        await _attempt_promotion(deps, ticket_id=ticket.ticket_id)

    assert deps.paper_repo.insert_deployment_calls == 1  # no second deployment from the reuse


@pytest.mark.asyncio
async def test_promote_to_paper_rejects_expired_confirm_ticket() -> None:
    """I-12 negative case 3: a confirm ticket past its TTL must not
    authorize PAPER promotion -- an attacker (or a stale client) holding an
    old ticket cannot resurrect it after expiry to reach PAPER."""
    deps = Deps()
    ticket = make_ticket(expires_at=NOW - timedelta(seconds=1))
    deps.confirm_repo.seed(ticket)

    with pytest.raises(ConfirmTicketExpiredError):
        await _attempt_promotion(deps, ticket_id=ticket.ticket_id)

    assert deps.paper_repo.insert_deployment_calls == 0
