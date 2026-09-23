"""FA-21 DR_RUNBOOK.md 증빙 — ADR-2026-09-09-C D2: negative>=3, 실패주입 1, 성능단언 1,
게이트 적색 재현 1.

Spec: docs/specs/L4_ibor_fund_accounting_and_resilience_v1.0.md FA-21.
docs/ops/DR_RUNBOOK.md은 실행 코드가 아니므로, 이 테스트가 그 문서의 필수 절이 실제로
있는지(누락 시 배포 훈련이 막힘) 확인하는 게이트 역할을 한다.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

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


def test_validate_dr_runbook_perf_budget(runbook_text: str) -> None:
    """ADR-2026-09-09-C 예산표에 문서 검증 항목은 없다(docs-only 리프) —
    수 KB 텍스트에 대한 리터럴 스캔 규모에 맞춘 보수적 로컬 상한(단일 실행 50ms)."""
    started = time.perf_counter()
    validate_dr_runbook(runbook_text)
    elapsed_ms = (time.perf_counter() - started) * 1000
    assert elapsed_ms < 50.0
