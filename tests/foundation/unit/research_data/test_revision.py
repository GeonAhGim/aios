"""RD-3 — `domain/revision.link_revision_chain` tests.

Spec: docs/specs/L4_research_data_and_market_ecosystem_v1.0.md §9 RD-3
DoD (c)(d).
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest

from src.foundation.research_data.contracts.v1 import ResearchItem
from src.foundation.research_data.domain.known_at import PointInTimeViolationError
from src.foundation.research_data.domain.revision import (
    RevisionBranchError,
    RevisionCycleError,
    link_revision_chain,
)

_T0 = datetime(2026, 9, 1, 0, 0, tzinfo=timezone.utc)


def _item(*, item_id: UUID, revision_of: UUID | None, known_at: datetime) -> ResearchItem:
    return ResearchItem(
        item_id=item_id,
        source_id="opendart",
        kind="filing",
        published_at=known_at,
        known_at=known_at,
        instruments=("005930",),
        title="정정 공시",
        body_ref=None,
        url="https://dart.fss.or.kr/x",
        language="ko",
        hash="h" * 64,
        revision_of=revision_of,
    )


def test_empty_input_returns_empty_chain() -> None:
    assert link_revision_chain([]) == ()


def test_single_origin_with_no_revisions_is_a_one_item_chain() -> None:
    origin = _item(item_id=uuid4(), revision_of=None, known_at=_T0)
    assert link_revision_chain([origin]) == (origin,)


def test_linear_chain_of_three_orders_oldest_first() -> None:
    """RD-3 DoD (c) — 같은 원문의 개정 n개가 supersedes 링크로 단선 연결된다."""
    origin = _item(item_id=uuid4(), revision_of=None, known_at=_T0)
    rev1 = _item(item_id=uuid4(), revision_of=origin.item_id, known_at=_T0 + timedelta(days=1))
    rev2 = _item(item_id=uuid4(), revision_of=rev1.item_id, known_at=_T0 + timedelta(days=2))

    # 입력 순서를 일부러 뒤섞는다 -- 결과 순서는 링크 구조로만 정해져야 한다.
    ordered = link_revision_chain([rev2, origin, rev1])

    assert ordered == (origin, rev1, rev2)


def test_branching_input_rejected() -> None:
    """RD-3 DoD (c) — 분기(같은 원문을 두 항목이 동시에 정정) 입력은 명시
    예외로 거부된다."""
    origin = _item(item_id=uuid4(), revision_of=None, known_at=_T0)
    rev_a = _item(item_id=uuid4(), revision_of=origin.item_id, known_at=_T0 + timedelta(days=1))
    rev_b = _item(item_id=uuid4(), revision_of=origin.item_id, known_at=_T0 + timedelta(days=1))

    with pytest.raises(RevisionBranchError) as exc_info:
        link_revision_chain([origin, rev_a, rev_b])
    assert exc_info.value.error_code == "RD_REVISION_BRANCH"
    assert exc_info.value.parent_id == origin.item_id


def test_cyclic_input_rejected() -> None:
    """RD-3 DoD (c) — 순환(두 항목이 서로를 정정본이라 참조) 입력은 명시
    예외로 거부된다."""
    id_a, id_b = uuid4(), uuid4()
    item_a = _item(item_id=id_a, revision_of=id_b, known_at=_T0)
    item_b = _item(item_id=id_b, revision_of=id_a, known_at=_T0 + timedelta(days=1))

    with pytest.raises(RevisionCycleError) as exc_info:
        link_revision_chain([item_a, item_b])
    assert exc_info.value.error_code == "RD_REVISION_CYCLE"


def test_self_referencing_item_rejected_as_cycle() -> None:
    self_id = uuid4()
    item = _item(item_id=self_id, revision_of=self_id, known_at=_T0)
    with pytest.raises(RevisionCycleError):
        link_revision_chain([item])


def test_disconnected_cycle_alongside_valid_chain_rejected() -> None:
    """단선 체인 하나(origin->rev1)와 별개로 떨어진 2-순환(c<->d)이 같은
    입력에 섞이면, 단선 체인만으로는 전체 항목 수를 소진하지 못하므로
    순환으로 거부된다."""
    origin = _item(item_id=uuid4(), revision_of=None, known_at=_T0)
    rev1 = _item(item_id=uuid4(), revision_of=origin.item_id, known_at=_T0 + timedelta(days=1))
    id_c, id_d = uuid4(), uuid4()
    item_c = _item(item_id=id_c, revision_of=id_d, known_at=_T0)
    item_d = _item(item_id=id_d, revision_of=id_c, known_at=_T0 + timedelta(days=1))

    with pytest.raises(RevisionCycleError):
        link_revision_chain([origin, rev1, item_c, item_d])


def test_multiple_roots_rejected() -> None:
    origin_a = _item(item_id=uuid4(), revision_of=None, known_at=_T0)
    origin_b = _item(item_id=uuid4(), revision_of=None, known_at=_T0)
    with pytest.raises(RevisionCycleError):
        link_revision_chain([origin_a, origin_b])


def test_out_of_order_known_at_rejected_via_fa9_delegation() -> None:
    """RD-3 DoD (d) — known_at 순서 검증은 FA-9 위임 `assert_point_in_time`을
    그대로 쓴다: 정정본의 known_at이 원본보다 앞서면 FA-9의
    `PointInTimeViolationError`가 그대로 올라온다(이 모듈이 별도 예외 타입을
    만들지 않는다)."""
    origin = _item(item_id=uuid4(), revision_of=None, known_at=_T0)
    earlier_rev = _item(
        item_id=uuid4(), revision_of=origin.item_id, known_at=_T0 - timedelta(days=1)
    )
    with pytest.raises(PointInTimeViolationError):
        link_revision_chain([origin, earlier_rev])


# ---- DEEPEN(task-2907) — DEPTH_DC_RD.md D2 부족분: 실패주입/성능단언/게이트적색 ----


def test_link_revision_chain_rejects_non_research_item_element() -> None:
    """실패 주입 — `Sequence[ResearchItem]` 타입힌트는 런타임을 강제하지
    않는다. 역직렬화 경로(예: 캐시·큐 재생)에서 dict가 섞여 들어와도 조용히
    건너뛰거나 잘못된 순서를 내지 않고 즉시 크래시해야 한다(fail-closed)."""
    origin = _item(item_id=uuid4(), revision_of=None, known_at=_T0)
    garbage: Any = {"item_id": uuid4(), "revision_of": origin.item_id, "known_at": _T0}
    with pytest.raises(AttributeError):
        link_revision_chain([origin, garbage])


@pytest.mark.perf
def test_link_revision_chain_meets_latency_budget_for_long_chain() -> None:
    """성능 단언 — 단선 체인 워크(FA-9 위임 known_at 비교 포함)는
    O(n)이어야 한다 — 회귀가 있다면(예: 매 단계마다 이미 방문한 전체
    항목을 다시 스캔) O(n^2)로 퇴화한다."""
    n = 5_000
    items: list[ResearchItem] = []
    prev_id: UUID | None = None
    for i in range(n):
        item_id = uuid4()
        items.append(_item(item_id=item_id, revision_of=prev_id, known_at=_T0 + timedelta(days=i)))
        prev_id = item_id
    rng = random.Random(2907)
    rng.shuffle(items)  # 입력 순서는 링크 구조와 무관해야 한다

    budget_sec = 5.0  # 실측 로컬 <1s(5000개, FA-9 위임 비교 포함)
    start_time = time.perf_counter()
    ordered = link_revision_chain(items)
    elapsed = time.perf_counter() - start_time

    assert len(ordered) == n
    print(f"[RD-3 revision] {n}-item chain linked in {elapsed:.3f}s (budget<{budget_sec}s)")
    assert elapsed < budget_sec, (
        f"link_revision_chain({n}개)가 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s) — "
        "O(n) 워크가 O(n^2)로 퇴화했는지 확인하세요."
    )


def test_gate_red_if_branch_check_removed_existing_negative_would_flip() -> None:
    """게이트 적색 재현 — RD-3(c) 분기 검출 줄이 실수로 삭제된 회귀를
    흉내낸 대조 구현을 구성해, 그 버전은 분기 입력을 예외 없이 "먼저
    등록된 자식 하나만" 골라 조용히 절반을 누락한 체인을 반환함(즉 데이터
    손실이 조용히 일어남)을 실측한다. 현재 구현은 `RevisionBranchError`로
    즉시 거부한다 — 이 테스트가 실제로 위험한 회귀를 잡아낼 수 있다는
    증거다."""
    origin = _item(item_id=uuid4(), revision_of=None, known_at=_T0)
    rev_a = _item(item_id=uuid4(), revision_of=origin.item_id, known_at=_T0 + timedelta(days=1))
    rev_b = _item(item_id=uuid4(), revision_of=origin.item_id, known_at=_T0 + timedelta(days=1))
    items = [origin, rev_a, rev_b]

    def _regressed_without_branch_check(items: list[ResearchItem]) -> list[ResearchItem]:
        by_id = {item.item_id: item for item in items}
        successors: dict[UUID | None, list[UUID]] = {}
        for item in items:
            successors.setdefault(item.revision_of, []).append(item.item_id)
        # (분기 개수 검사가 빠진 회귀 -- 여러 자식 중 첫 번째만 따라간다)
        roots = successors.get(None, [])
        current_id: UUID | None = roots[0]
        ordered: list[ResearchItem] = []
        while current_id is not None:
            current = by_id[current_id]
            ordered.append(current)
            next_ids = successors.get(current_id, [])
            current_id = next_ids[0] if next_ids else None
        return ordered

    regressed = _regressed_without_branch_check(items)
    assert len(regressed) == 2  # rev_b가 조용히 누락됐다 -- 회귀가 실제로 위험함을 증명

    with pytest.raises(RevisionBranchError):
        link_revision_chain(items)
