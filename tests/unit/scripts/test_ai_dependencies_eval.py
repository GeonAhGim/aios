"""AI-3 document evidence contracts; no network or package installation."""

from pathlib import Path

import pytest

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
