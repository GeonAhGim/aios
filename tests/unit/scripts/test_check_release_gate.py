"""scripts/check_release_gate.py 단위 테스트 — PLT-39.

DoD: `--stage internal_development`는 실제 저장소에서 exit 0,
`--stage internal_paper`는 미충족 required_evidence를 출력하며 exit 1 —
두 경로 모두 `main()`을 직접 실행해 단언한다(문자열 mock 금지).
DB·네트워크 접근 없음 — 임시 디렉터리와 파일 존재 여부만 쓴다.

DEEPEN(task-6006): 실패 주입(손상된 YAML이 예외로 죽지 않고 fail-closed
exit 1을 내는지)과 수치 성능 단언(실제 5-stage 체인 전수 검사 시간 예산)을
추가한다 — `tests/unit/scripts/test_check_migration_chain.py`의
`test_check_migration_chain_real_versions_dir_completes_within_time_budget`
패턴을 따른다.
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

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


def _write_config(tmp_path: Path, stages: list[dict[str, Any]]) -> Path:
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


# ---------------------------------------------------------------------------
# 실패 주입 — 파싱 자체가 깨지는 상황(손상된 YAML)을 시뮬레이션한다.
# ---------------------------------------------------------------------------


def test_corrupted_yaml_fails_closed_instead_of_crashing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """운영 장애 시뮬레이션: config 파일이 잘려서 잘못된 YAML이 된 상태.

    이전 구현은 `yaml.safe_load`의 `yaml.YAMLError`를 잡지 않아 트레이스백과
    함께 죽었다 — CI 게이트 스텝이 "이 스크립트가 죽었다"와 "게이트가 정상
    작동해 막았다"를 구분 못 하게 만든다. fail-closed 원칙(CLAUDE.md §3)상
    이런 파싱 실패도 깨끗한 FAIL 메시지 + exit 1이어야 한다.
    """
    corrupted_config = tmp_path / "release_gates.yaml"
    corrupted_config.write_text(
        "stages:\n  - name: a\n    required_evidence: [unterminated\n",
        encoding="utf-8",
    )

    exit_code = check_release_gate.main(
        [
            "--stage",
            "a",
            "--config",
            str(corrupted_config),
            "--repo-root",
            str(tmp_path),
        ]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out


def test_evidence_entry_missing_path_key_fails_closed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """실패 주입: required_evidence 항목에 `path` 키가 빠진 상태(예: 편집 실수로
    필드명이 잘못 붙거나 병합 충돌이 절반만 해결된 config)를 시뮬레이션한다.
    이전 구현은 `item["path"]`에서 `KeyError`를 그대로 흘려보냈다.
    """
    config_path = _write_config(
        tmp_path,
        [
            {
                "name": "a",
                "depends_on": [],
                "required_evidence": [{"description": "경로 필드 누락"}],
            }
        ],
    )

    exit_code = check_release_gate.main(
        ["--stage", "a", "--config", str(config_path), "--repo-root", str(tmp_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out


# ---------------------------------------------------------------------------
# 성능 단언 — 실제 저장소 5-stage 체인 전수 해석이 예산 내에 끝나는지.
# ---------------------------------------------------------------------------


def test_full_chain_resolution_completes_within_time_budget() -> None:
    """수치 성능 단언: 가장 깊은 stage(`marketplace_commercialization`, 5단계
    depends_on 체인)의 evidence 누적 해석 + 파일 존재 확인이 예산 내에
    끝나는지 확인한다. `test_check_migration_chain.py`의 시간 예산 패턴과
    동일 — 정적 파일 존재 확인만 하므로 CI 스텝 예산(수 초)보다 훨씬 낮은
    50ms를 예산으로 건다.
    """
    stages = check_release_gate.load_stages(REAL_CONFIG)
    assert len(stages) >= 5  # 벤치마크가 무의미해지지 않도록 stage 수를 보장

    start = time.perf_counter()
    for _ in range(100):
        check_release_gate.check_stage(
            stages, "marketplace_commercialization", repo_root=ROOT
        )
    elapsed = time.perf_counter() - start

    assert elapsed < 0.05 * 100, f"100회 반복 해석이 {elapsed:.3f}s — 예산(5.0s) 초과"
