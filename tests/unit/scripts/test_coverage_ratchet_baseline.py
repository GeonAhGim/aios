"""scripts/coverage_ratchet.py — baseline write trust gating.

DoD: baseline 파일이 신뢰할 수 있는 CI 컨텍스트(GitHub Actions 또는 명시적 플래그)
외에서 생성된 경우, 그 baseline으로 커버리지를 올리는 것은 허용하지 않는다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.scripts.test_coverage_ratchet import (
    _load_module,
    _write_baseline,
    _write_coverage_xml,
)

SCRIPTS_DIR = Path(__file__).resolve().parents[3] / "scripts"
coverage_ratchet = _load_module("coverage_ratchet_baseline", SCRIPTS_DIR / "coverage_ratchet.py")


# ---------------------------------------------------------------------------
# baseline_write_is_trusted() — 순수 함수 단위 테스트
# ---------------------------------------------------------------------------


def test_baseline_write_is_trusted_defaults_to_untrusted() -> None:
    """DoD: 기본값은 신뢰하지 않는다 — 명시적 플래그나 CI 환경이 없으면 baseline
    쓰기를 거부한다."""
    assert coverage_ratchet.baseline_write_is_trusted(False, {}) is False


def test_baseline_write_is_trusted_via_github_actions_env() -> None:
    """DoD: GITHUB_ACTIONS=true 환경 변수가 설정되면 baseline 쓰기가 신뢰된다."""
    assert coverage_ratchet.baseline_write_is_trusted(False, {"GITHUB_ACTIONS": "true"}) is True


def test_baseline_write_is_trusted_via_explicit_flag() -> None:
    """DoD: --allow-baseline-write 플래그가 전달되면 baseline 쓰기가 신뢰된다."""
    assert coverage_ratchet.baseline_write_is_trusted(True, {}) is True


def test_baseline_write_is_trusted_rejects_falsy_github_actions_value() -> None:
    """DoD: GITHUB_ACTIONS=false 또는 빈 문자열은 신뢰하지 않는다."""
    assert coverage_ratchet.baseline_write_is_trusted(False, {"GITHUB_ACTIONS": "false"}) is False
    assert coverage_ratchet.baseline_write_is_trusted(False, {"GITHUB_ACTIONS": ""}) is False


# ---------------------------------------------------------------------------
# main() — baseline write trust gating 통합 테스트
# ---------------------------------------------------------------------------


def test_baseline_from_untrusted_context_warns_but_allows_ratchet_up(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """DoD: 로컬 환경(비 CI)에서 생성된 baseline은 신뢰할 수 없으므로, 커버리지가
    올라 baseline을 올리는 시도는 경고 메시지를 출력하지만 거부하지는 않는다."""
    xml_path = _write_coverage_xml(tmp_path, 0.85)
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
        ]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "신뢰 불가" in out


def test_baseline_from_trusted_context_allows_ratchet_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DoD: CI 환경(GITHUB_ACTIONS=true)에서 생성된 baseline은 신뢰할 수 있으므로,
    커버리지가 올라 baseline을 올리는 것이 허용된다."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    xml_path = _write_coverage_xml(tmp_path, 0.85)
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
        ]
    )

    assert exit_code == 0
    assert baseline_path.read_text(encoding="utf-8").strip() == "85.00"


def test_baseline_from_untrusted_context_allows_drop_within_tolerance(
    tmp_path: Path,
) -> None:
    """DoD: baseline이 신뢰할 수 없는 컨텍스트에서 생성되어도, 측정치가 baseline
    내에서 떨어지면(허용 오차 범위) baseline trust gating은 적용되지 않는다."""
    xml_path = _write_coverage_xml(tmp_path, 0.7970)  # 80.00 - 0.30%p
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0


def test_baseline_from_untrusted_context_fails_below_tolerance(
    tmp_path: Path,
) -> None:
    """DoD: baseline trust gating이 활성화되면, 측정치가 허용 오차 밖으로 떨어지면
    평소대로 기준선 미달로 실패한다."""
    xml_path = _write_coverage_xml(tmp_path, 0.70)
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        ["--coverage-xml", str(xml_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1


def test_explicit_allow_flag_allows_ratchet_up_locally(
    tmp_path: Path,
) -> None:
    """DoD: --allow-baseline-write 플래그를 전달하면 로컬에서도 baseline 쓰기가
    신뢰된다 — 개발 중에 수동으로 baseline을 업데이트할 수 있다."""
    xml_path = _write_coverage_xml(tmp_path, 0.85)
    baseline_path = _write_baseline(tmp_path, 80.00)

    exit_code = coverage_ratchet.main(
        [
            "--coverage-xml",
            str(xml_path),
            "--baseline",
            str(baseline_path),
            "--allow-baseline-write",
        ]
    )

    assert exit_code == 0
    assert baseline_path.read_text(encoding="utf-8").strip() == "85.00"


@pytest.mark.perf
def test_performance_assertion_baseline_write_under_100ms(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """성능 예산: baseline 쓰기 연산(측정+비교+쓰기)이 100ms 이하여야 한다.
    CI에서 수백~수천 건의 테스트를 돌릴 때 스토리지 I/O가 병목되지 않도록."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    xml_path = _write_coverage_xml(tmp_path, 0.75)
    baseline_path = tmp_path / "coverage-baseline.txt"

    import time

    start = time.perf_counter()
    for _ in range(100):
        coverage_ratchet.main(
            [
                "--coverage-xml",
                str(xml_path),
                "--baseline",
                str(baseline_path),
            ]
        )
    elapsed_ms = (time.perf_counter() - start) / 100 * 1000

    assert elapsed_ms < 100, f"baseline 쓰기 평균 {elapsed_ms:.1f}ms — 예산 100ms 초과"
