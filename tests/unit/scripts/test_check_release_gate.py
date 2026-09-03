"""scripts/check_release_gate.py 단위 테스트 — PLT-39.

DoD: `--stage internal_development`는 실제 저장소에서 exit 0,
`--stage internal_paper`는 미충족 required_evidence를 출력하며 exit 1 —
두 경로 모두 `main()`을 직접 실행해 단언한다(문자열 mock 금지).
DB·네트워크 접근 없음 — 임시 디렉터리와 파일 존재 여부만 쓴다.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"
REAL_CONFIG = ROOT / "config" / "release_gates.yaml"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # dataclasses는 cls.__module__을 sys.modules에서 찾는다
    spec.loader.exec_module(module)
    return module


check_release_gate = _load_module(
    "check_release_gate", SCRIPTS_DIR / "check_release_gate.py"
)


# ---------------------------------------------------------------------------
# 실제 저장소 config/release_gates.yaml 대상 — DoD 문구를 그대로 단언
# ---------------------------------------------------------------------------


def test_internal_development_passes_against_real_repo() -> None:
    exit_code = check_release_gate.main(
        ["--stage", "internal_development", "--config", str(REAL_CONFIG), "--repo-root", str(ROOT)]
    )

    assert exit_code == 0


def test_internal_paper_lists_missing_evidence_against_real_repo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = check_release_gate.main(
        ["--stage", "internal_paper", "--config", str(REAL_CONFIG), "--repo-root", str(ROOT)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "미충족 항목" in out
    assert "경로 없음" in out


# ---------------------------------------------------------------------------
# 합성 config — depends_on 누적/순환/알 수 없는 stage 등 경계 조건
# ---------------------------------------------------------------------------


def _write_config(tmp_path: Path, stages: list[dict]) -> Path:
    config_path = tmp_path / "release_gates.yaml"
    config_path.write_text(yaml.safe_dump({"stages": stages}), encoding="utf-8")
    return config_path


def test_stage_with_all_evidence_present_passes(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    config_path = _write_config(
        tmp_path,
        [
            {
                "name": "a",
                "depends_on": [],
                "required_evidence": [{"description": "A 증거", "path": "a.txt"}],
            }
        ],
    )

    exit_code = check_release_gate.main(
        ["--stage", "a", "--config", str(config_path), "--repo-root", str(tmp_path)]
    )

    assert exit_code == 0


def test_depends_on_accumulates_parent_missing_evidence(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        [
            {
                "name": "a",
                "depends_on": [],
                "required_evidence": [{"description": "A 증거", "path": "missing-a.txt"}],
            },
            {
                "name": "b",
                "depends_on": ["a"],
                "required_evidence": [{"description": "B 증거", "path": "missing-b.txt"}],
            },
        ],
    )

    stages = check_release_gate.load_stages(config_path)
    missing = check_release_gate.check_stage(stages, "b", repo_root=tmp_path)

    assert any("[a]" in m and "missing-a.txt" in m for m in missing)
    assert any("[b]" in m and "missing-b.txt" in m for m in missing)
    exit_code = check_release_gate.main(
        ["--stage", "b", "--config", str(config_path), "--repo-root", str(tmp_path)]
    )
    assert exit_code == 1


def test_unknown_stage_fails(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path, [{"name": "a", "depends_on": [], "required_evidence": []}]
    )

    exit_code = check_release_gate.main(
        ["--stage", "does-not-exist", "--config", str(config_path), "--repo-root", str(tmp_path)]
    )

    assert exit_code == 1


def test_circular_depends_on_fails(tmp_path: Path) -> None:
    config_path = _write_config(
        tmp_path,
        [
            {"name": "a", "depends_on": ["b"], "required_evidence": []},
            {"name": "b", "depends_on": ["a"], "required_evidence": []},
        ],
    )

    exit_code = check_release_gate.main(
        ["--stage", "a", "--config", str(config_path), "--repo-root", str(tmp_path)]
    )

    assert exit_code == 1


def test_missing_config_file_fails(tmp_path: Path) -> None:
    missing_config = tmp_path / "does-not-exist.yaml"

    exit_code = check_release_gate.main(
        [
            "--stage",
            "internal_development",
            "--config",
            str(missing_config),
            "--repo-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
