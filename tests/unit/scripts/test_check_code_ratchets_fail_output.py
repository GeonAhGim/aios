"""scripts/check_code_ratchets.py FAIL 출력 단언 -- task-23304.

loc_over_* 증가 시 실패 출력이 경로 사전순 --top(10) 절단으로 방금 임계값을 넘긴 파일을
빠뜨리던 진단 결함을 고정한다. 기존 test_check_code_ratchets.py는 LOC 파일 수 500줄 근접
(CLAUDE.md §6 #12)이라 여기 별도 파일로 둔다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_code_ratchets = _load_module("check_code_ratchets", SCRIPTS_DIR / "check_code_ratchets.py")


def _empty_baseline() -> dict[str, int]:
    return {
        "skip_xfail": 0,
        "todo_fixme_xxx": 0,
        "not_implemented_error": 0,
        "loc_over_500": 0,
        "loc_over_800": 0,
        "loc_over_1000": 0,
    }


def test_loc_over_500_fail_output_lists_every_over_threshold_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """부정 케이스: 기준선 0에서 임계값 초과 파일 12개가 생기면 exit 2이고, --top(10)을
    넘는 목록이라도 경로 사전순 마지막 파일(zz_new.py)까지 전부 출력돼야 한다."""
    for i in range(11):
        path = tmp_path / "src" / f"f{i:02d}.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n" * 501, encoding="utf-8")
    newest = tmp_path / "src" / "zz_new.py"
    newest.write_text("x = 1\n" * 501, encoding="utf-8")
    baseline_path = tmp_path / "code-ratchets-baseline.json"
    baseline_path.write_text(json.dumps(_empty_baseline()), encoding="utf-8")

    exit_code = check_code_ratchets.main(
        ["--root", str(tmp_path), "--baseline", str(baseline_path)]
    )

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "src/zz_new.py" in out
