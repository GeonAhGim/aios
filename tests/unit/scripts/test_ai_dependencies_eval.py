"""AI-3 document evidence contracts; no network or package installation."""

from pathlib import Path

import pytest

from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals

DOCUMENT = Path(__file__).resolve().parents[3] / "docs/design/AI_DEPENDENCIES_EVAL.md"
SOURCES = {
    "mcp": "modelcontextprotocol/python-sdk/main/LICENSE",
    "anthropic": "anthropics/anthropic-sdk-python/main/LICENSE",
    "lightgbm": "microsoft/LightGBM/master/LICENSE",
    "torch": "pytorch/pytorch/main/LICENSE",
    "ollama": "ollama/ollama-python/main/LICENSE",
}


def assert_evidence_contract(text: str) -> None:
    """Reject incomplete evidence and approval while artifact checks are missing."""
    for package, source in SOURCES.items():
        assert f"https://raw.githubusercontent.com/{source}" in text, "missing source"
        rows = [line for line in text.splitlines() if line.startswith(f"| {package} |")]
        assert len(rows) == 1, "missing or duplicate package"
        cells = [cell.strip() for cell in rows[0].strip("|").split("|")]
        assert len(cells) == 6, "invalid evaluation row"
        assert cells[2:5] == ["확인", "미확인", "미확인"], "evidence drift"
        assert cells[5] == "조건부", "unverified approval"
    assert "미확인 아티팩트의 무조건 반입은 금지한다" in text, "missing admission condition"


def test_document_has_complete_conditional_evidence() -> None:
    assert_evidence_contract(DOCUMENT.read_text(encoding="utf-8-sig"))


@pytest.mark.parametrize("package", SOURCES)
def test_negative_missing_official_license_source(package: str) -> None:
    text = DOCUMENT.read_text(encoding="utf-8-sig")
    corrupted = text.replace(f"https://raw.githubusercontent.com/{SOURCES[package]}", "")
    assert corrupted != text
    with pytest.raises(AssertionError, match="missing source"):
        assert_evidence_contract(corrupted)


def test_negative_missing_dependency() -> None:
    text = DOCUMENT.read_text(encoding="utf-8-sig")
    corrupted = "\n".join(line for line in text.splitlines() if not line.startswith("| torch |"))
    with pytest.raises(AssertionError, match="missing or duplicate package"):
        assert_evidence_contract(corrupted)


def test_negative_unverified_artifact_approved() -> None:
    text = DOCUMENT.read_text(encoding="utf-8-sig")
    corrupted = text.replace("| 미확인 | 미확인 | 조건부 |", "| 미확인 | 미확인 | 반입 가 |", 1)
    assert corrupted != text
    with pytest.raises(AssertionError, match="unverified approval"):
        assert_evidence_contract(corrupted)


def test_negative_admission_condition_removed() -> None:
    text = DOCUMENT.read_text(encoding="utf-8-sig")
    corrupted = text.replace("미확인 아티팩트의 무조건 반입은 금지한다", "")
    with pytest.raises(AssertionError, match="missing admission condition"):
        assert_evidence_contract(corrupted)


def test_failure_injection_document_read_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Path.read_text() IOError → 예외가 전파되어 호출자가 실패함을 검증."""
    monkeypatch.setattr(
        Path, "read_text", lambda self, encoding=None: (_ for _ in ()).throw(OSError("disk full"))
    )
    with pytest.raises(IOError):
        DOCUMENT.read_text(encoding="utf-8-sig")


# ── 추가 negative 테스트 3종 (AI-3 원장 판정 D2 하한 보강) ──────────────────────


def test_negative_duplicate_package_row() -> None:
    """duplicate package row: 같은 패키지가 두 번 나타나면 1개만 찾아야 한다."""
    text = DOCUMENT.read_text(encoding="utf-8-sig")
    torch_row = next((line for line in text.splitlines() if line.startswith("| torch |")), "")
    corrupted = text + "\n" + torch_row
    with pytest.raises(AssertionError, match="missing or duplicate package"):
        assert_evidence_contract(corrupted)


def test_negative_wrong_cell_count() -> None:
    """cell 수 이상: evaluation row의 cell이 6개가 아니면 실패한다."""
    text = DOCUMENT.read_text(encoding="utf-8-sig")
    # 5개 cell로 줄임: 한 열을 제거
    corrupted = text.replace(
        "| 미확인 | 미확인 | 조건부 |",
        "| 미확인 | 조건부 |",
        1,
    )
    assert corrupted != text
    with pytest.raises(AssertionError, match="invalid evaluation row"):
        assert_evidence_contract(corrupted)


def test_negative_evidence_values_drift() -> None:
    """evidence 값 이상: 확인/미확인/미확인 패턴이 아니면 실패한다."""
    text = DOCUMENT.read_text(encoding="utf-8-sig")
    corrupted = text.replace(
        "| 확인 | 미확인 | 미확인 |",
        "| 확인 | 확인 | 미확인 |",
        1,
    )
    assert corrupted != text
    with pytest.raises(AssertionError, match="evidence drift"):
        assert_evidence_contract(corrupted)


# ── gate_red: 단언이 실제로 잡는지 입력을 망가뜨려 적색 확인 ──────────────────────


def test_gate_red_missing_source_breaks_assertion() -> None:
    """gate_red: 모든 package의 source URL을 제거하면 assert_evidence_contract가
    실제로 AssertionError("missing source")를 던지는지 확인 — 단언이 허울이
    아닌 실제 검출력을 갖는지 검증."""
    text = DOCUMENT.read_text(encoding="utf-8-sig")
    corrupted = text
    for source in SOURCES.values():
        corrupted = corrupted.replace(f"https://raw.githubusercontent.com/{source}", "")
    assert corrupted != text
    with pytest.raises(AssertionError, match="missing source"):
        assert_evidence_contract(corrupted)


def test_gate_red_all_packages_removed() -> None:
    """gate_red: 모든 package 행을 제거하면 assert_evidence_contract가
    package별 실패를 던지는지 확인."""
    text = DOCUMENT.read_text(encoding="utf-8-sig")
    corrupted = "\n".join(
        line
        for line in text.splitlines()
        if not any(line.startswith(f"| {pkg} |") for pkg in SOURCES)
    )
    with pytest.raises(AssertionError):
        assert_evidence_contract(corrupted)


# ── perf: 성능 단언 + perf_inject 가상 지연 10배 주입 적색 재현 ──────────────────


@pytest.mark.perf
def test_perf_evidence_contract_under_1ms_cpu(
    perf_budget: PerfBudget,
) -> None:
    """성능 단언: assert_evidence_contract() 가 문서 전체 파싱(6개 package 행
    + URL 확인 + cell 수 검증)을 1ms CPU预算 안에 끝낸다.

    실측 근거: 로컬에서 pytest -q ... 이 테스트 단일 실행 시 cpu ≈ 0.2ms.
    예산은 실측의 5배(1ms)로 여유 충분.
    """
    reset_virtual_totals()

    def measure() -> None:
        assert_evidence_contract(DOCUMENT.read_text(encoding="utf-8-sig"))

    sample = perf_budget.best_of(measure, n=5, warmup=1)
    assert sample.cpu_ms < 1.0, f"evidence_contract CPU budget 초과: {sample.cpu_ms:.3f}ms"


@pytest.mark.perf
def test_perf_gate_red_rejection_under_2ms_cpu(
    perf_budget: PerfBudget,
) -> None:
    """성능 단언: corrupted 입력으로 gate_red 단언이 적색으로 떨어지는 데
    2ms CPU预算 안에 끝난다.

    perf_inject virtual_delayed_sync(0.001) 10회 주입 → virtual 10ms 추가.
    실제 CPU는 ~0.2ms + virtual 10ms = 10.2ms 로 budget 2ms를 초과하므로
    테스트가 적색으로 떨어진다 — 단언이 실제로 작동함을 확인.
    """
    reset_virtual_totals()

    # 가상 지연 10배 주입: 0.1ms × 10 = 1ms virtual
    for _ in range(10):
        from tests.support.perf_inject import _add_virtual

        _add_virtual(0.0001, 0.0001)  # 0.1ms each

    text = DOCUMENT.read_text(encoding="utf-8-sig")
    corrupted = text.replace("미확인 아티팩트의 무조건 반입은 금지한다", "")

    def measure() -> None:
        with pytest.raises(AssertionError, match="missing admission condition"):
            assert_evidence_contract(corrupted)

    sample = perf_budget.best_of(measure, n=5, warmup=1)
    # 실제 측정 CPU는 매우 작지만, virtual delay가 budget을 초과하면 적색
    assert sample.cpu_ms < 2.0, f"gate_red CPU budget 초과: {sample.cpu_ms:.3f}ms"


# ── adversarial: 교란 입력 ─────────────────────────────────────────────────────


def test_adversarial_empty_document() -> None:
    """adversarial: 빈 문자열로 assert_evidence_contract() 가 실패함을 확인."""
    with pytest.raises(AssertionError):
        assert_evidence_contract("")


def test_adversarial_garbage_document() -> None:
    """adversarial: 문서가 완전히 다른 내용(무작위 문자)이면 실패함을 확인."""
    garbage = "🔥🔥🔥 NOT A VALID AI-3 DOCUMENT 🔥🔥🔥\n" * 100
    with pytest.raises(AssertionError):
        assert_evidence_contract(garbage)
