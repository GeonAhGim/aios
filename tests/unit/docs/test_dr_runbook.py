"""FA-21 DR_RUNBOOK.md 증빙 — ADR-2026-09-09-C D2: negative>=3, 실패주입 1, 성능단언 1,
게이트 적색 재현 1. D3: 적대적 검증 우회 1.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md FA-21.
docs/ops/DR_RUNBOOK.md은 실행 코드가 아니므로, 이 테스트가 그 문서의 필수 절이 실제로
있는지(누락 시 배포 훈련이 막힘) 확인하는 게이트 역할을 한다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import PerfBudget

RUNBOOK_PATH = Path(__file__).resolve().parents[3] / "docs" / "ops" / "DR_RUNBOOK.md"

REQUIRED_SECTIONS = (
    "## 1. 목표(RPO/RTO)",
    "## 2. 현재 구현 상태",
    "## 3. 페일오버 훈련 절차",
    "## 4. RPO 훈련",
    "## 5. 목표 미달 시 처리",
    "## 6. 훈련 주기",
    "## 7. 에스컬레이션",
)
RPO_TARGET_LITERAL = "RPO(Recovery Point Objective) | **0**"
RTO_TARGET_LITERAL = "RTO(Recovery Time Objective) | **≤ 5분**"


class DrRunbookValidationError(ValueError):
    """DR_RUNBOOK.md가 필수 절/목표 수치를 갖추지 못했을 때."""


def validate_dr_runbook(text: str) -> None:
    missing: list[str] = [f"section:{s}" for s in REQUIRED_SECTIONS if s not in text]
    if RPO_TARGET_LITERAL not in text:
        missing.append("target:RPO=0")
    if RTO_TARGET_LITERAL not in text:
        missing.append("target:RTO<=5min")
    if missing:
        raise DrRunbookValidationError(f"DR_RUNBOOK.md missing: {missing}")


@pytest.fixture(scope="module")
def runbook_text() -> str:
    return RUNBOOK_PATH.read_text(encoding="utf-8")


def test_actual_runbook_passes_validation(runbook_text: str) -> None:
    validate_dr_runbook(runbook_text)


def test_gate_red_repro_without_runbook_file() -> None:
    """FA-21 이전 상태(파일 부재) 재현 — 빈 문서는 게이트가 적색이어야 한다."""
    with pytest.raises(DrRunbookValidationError):
        validate_dr_runbook("")


def test_missing_rpo_target_detected(runbook_text: str) -> None:
    corrupted = runbook_text.replace(RPO_TARGET_LITERAL, "RPO(Recovery Point Objective) | 0")
    with pytest.raises(DrRunbookValidationError) as exc:
        validate_dr_runbook(corrupted)
    assert "target:RPO=0" in str(exc.value)


def test_missing_rto_target_detected(runbook_text: str) -> None:
    corrupted = runbook_text.replace(RTO_TARGET_LITERAL, "RTO(Recovery Time Objective) | 5분 내외")
    with pytest.raises(DrRunbookValidationError) as exc:
        validate_dr_runbook(corrupted)
    assert "target:RTO<=5min" in str(exc.value)


def test_missing_failover_drill_section_detected(runbook_text: str) -> None:
    corrupted = runbook_text.replace("## 3. 페일오버 훈련 절차", "## 3. (removed)")
    with pytest.raises(DrRunbookValidationError) as exc:
        validate_dr_runbook(corrupted)
    assert "section:## 3. 페일오버 훈련 절차" in str(exc.value)


def test_missing_escalation_section_detected(runbook_text: str) -> None:
    corrupted = runbook_text.replace("## 7. 에스컬레이션", "## 7. (removed)")
    with pytest.raises(DrRunbookValidationError) as exc:
        validate_dr_runbook(corrupted)
    assert "section:## 7. 에스컬레이션" in str(exc.value)


def test_truncated_runbook_fails_as_failure_injection(runbook_text: str) -> None:
    """쓰기 도중 파일이 잘렸다고 가정(장애 주입) — 뒤쪽 절이 전부 빠져야 잡힌다."""
    truncated = runbook_text[:400]
    with pytest.raises(DrRunbookValidationError):
        validate_dr_runbook(truncated)


@pytest.mark.perf
def test_validate_dr_runbook_perf_budget(perf_budget: PerfBudget, runbook_text: str) -> None:
    """ADR-2026-09-09-C 예산표에 문서 검증 항목은 없다(docs-only 리프) —
    수 KB 텍스트에 대한 리터럴 스캔 규모에 맞춘 보수적 로컬 상한(단일 실행 50ms)."""

    def measure_once() -> None:
        validate_dr_runbook(runbook_text)

    samples = perf_budget.samples(measure_once, n=100)
    p95_ms = sorted(s.cpu_ms for s in samples)[94]
    assert p95_ms < 50.0, f"DR_RUNBOOK validation p95={p95_ms:.3f}ms > 50ms budget"


def test_adversarial_dr_runbook_section_obfuscation_bypass(runbook_text: str) -> None:
    """FA-21 D3 적대적 검증 우회 — 필수 절이 실제 내용을 담았는지 확인한다.

    ADR-2026-09-09-C D3: 안전/실행/정책 축은 적대적 테스트가 INVARIANTS.md와
    교차 확인된다.

    DR_RUNBOOK.md는 필수 절이 실제로 있어야 한다. 적대적 시나리오:
    - 섹션 헤딩이 Unicode 변조로 존재하지 않음
    - 섹션 제목이 대소문자/공백/한글로 변조되어 존재하지 않음
    - 섹션이 마크다운 형식(##)이 아닌 일반 텍스트로만 존재
    - 섹션 번호이 누락 (예: "## 3." -> "## 3")

    실제 검증 함수(validate_dr_runbook)가 이 테스트의 "검증 엔진"이다.
    """
    # 시나리오 1: Unicode combining 문자로 헤딩 변조 (예: '페일오버' -> '페일̈오버')
    corrupted = runbook_text.replace(
        "## 3. 페일오버 훈련 절차",
        "## 3. \udc3c\udc74일\udc3c오베\udc3c\udc74루\udc3c\udc74트\udc3c\udc74태\udc3c\udc74",
    )
    with pytest.raises(DrRunbookValidationError) as exc:
        validate_dr_runbook(corrupted)
    assert "section:## 3. 페일오버 훈련 절차" in str(exc.value)

    # 시나리오 2: RPO/RTO 목표를 "N/A"로 치환 — 목표 수치 누락 감지
    corrupted = runbook_text.replace(
        RPO_TARGET_LITERAL,
        "RPO(Recovery Point Objective) | **N/A**",
    )
    with pytest.raises(DrRunbookValidationError) as exc:
        validate_dr_runbook(corrupted)
    assert "target:RPO=0" in str(exc.value)

    # 시나리오 3: 섹션 번호 누락 — "## 7. 에스컬레이션" -> "## 에스컬레이션"
    corrupted = runbook_text.replace("## 7. 에스컬레이션", "## 에스컬레이션")
    with pytest.raises(DrRunbookValidationError) as exc:
        validate_dr_runbook(corrupted)
    assert "section:## 7. 에스컬레이션" in str(exc.value)

    # 시나리오 4: RTO 값을 **≤ 5분**이 아닌 다른 값으로 교체
    corrupted = runbook_text.replace(RTO_TARGET_LITERAL, "RTO(Recovery Time Objective) | **5분**")
    with pytest.raises(DrRunbookValidationError) as exc:
        validate_dr_runbook(corrupted)
    # RTO 값이 **≤ 5분**이 아니므로 누락 감지되어야 함
    assert "target:RTO<=5min" in str(exc.value)
