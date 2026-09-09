"""scripts/check_code_ratchets.py 단위 테스트 -- ADR-2026-09-09-D Decision 2 "코드 래칫".

DoD: skip/xfail·TODO/FIXME/XXX·NotImplementedError 세 지표 각각의 baseline 증가를
exit 2로 적색 재현하고(re-repro), 감소는 --update 없이는 baseline을 건드리지
않다가 --update로만 반영됨을 직접 단언한다. 정적 AST/tokenize 스캔만 하는 순수
파서이므로 DB·import 없이 tmp_path에 합성한 `.py` 파일만으로 검증한다.
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


def _write_py(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _write_baseline(
    tmp_path: Path, counts: dict[str, int], name: str = "code-ratchets-baseline.json"
) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(counts), encoding="utf-8")
    return path


def _empty_baseline() -> dict[str, int]:
    return {"skip_xfail": 0, "todo_fixme_xxx": 0, "not_implemented_error": 0}


# ---------------------------------------------------------------------------
# 순수 스캐너 함수
# ---------------------------------------------------------------------------


def test_scan_counts_skip_xfail_decorator_and_calls(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "src/a.py",
        "import pytest\n\n"
        "@pytest.mark.skip(reason='x')\n"
        "def test_a(): pass\n\n"
        "@pytest.mark.xfail\n"
        "def test_b(): pass\n\n"
        "def test_c():\n"
        "    pytest.skip('nope')\n",
    )

    hits = check_code_ratchets.scan_tree(tmp_path, subdirs=("src",))

    assert len(hits["skip_xfail"]) == 3


def test_scan_ignores_skip_mention_in_docstring_or_string(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "src/a.py",
        '"""This module discusses pytest.mark.skip() in prose."""\n'
        "x = 'pytest.skip is mentioned here too'\n",
    )

    hits = check_code_ratchets.scan_tree(tmp_path, subdirs=("src",))

    assert hits["skip_xfail"] == []


def test_scan_counts_todo_fixme_xxx_in_comments_only(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "src/a.py",
        "# TODO: fix this\n"
        "x = 1  # FIXME later\n"
        "y = 2  # XXX hack\n"
        '"""docstring mentioning TODO is not a comment"""\n'
        "z = 3\n",
    )

    hits = check_code_ratchets.scan_tree(tmp_path, subdirs=("src",))

    assert len(hits["todo_fixme_xxx"]) == 3


def test_scan_counts_raise_not_implemented_error(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "src/a.py",
        "def f():\n"
        "    raise NotImplementedError\n\n"
        "def g():\n"
        "    raise NotImplementedError('why')\n",
    )

    hits = check_code_ratchets.scan_tree(tmp_path, subdirs=("src",))

    assert len(hits["not_implemented_error"]) == 2


def test_ratchet_allow_header_comment_exempts_not_implemented_error(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "src/a.py",
        "# ratchet-allow: fail-closed adapter stub, intentional\n"
        "def f():\n"
        "    raise NotImplementedError\n",
    )

    hits = check_code_ratchets.scan_tree(tmp_path, subdirs=("src",))

    assert hits["not_implemented_error"] == []


def test_ratchet_allow_only_exempts_not_implemented_error_metric(tmp_path: Path) -> None:
    _write_py(
        tmp_path,
        "src/a.py",
        "# ratchet-allow: fail-closed adapter stub\n"
        "# TODO: still counted\n"
        "def f():\n"
        "    raise NotImplementedError\n",
    )

    hits = check_code_ratchets.scan_tree(tmp_path, subdirs=("src",))

    assert hits["not_implemented_error"] == []
    assert len(hits["todo_fixme_xxx"]) == 1


def test_scan_excludes_pycache(tmp_path: Path) -> None:
    _write_py(tmp_path, "src/__pycache__/a.py", "raise NotImplementedError\n")

    hits = check_code_ratchets.scan_tree(tmp_path, subdirs=("src",))

    assert hits["not_implemented_error"] == []


# ---------------------------------------------------------------------------
# main() -- DoD 시나리오
# ---------------------------------------------------------------------------


def test_first_run_initializes_baseline_from_measurement(tmp_path: Path) -> None:
    _write_py(tmp_path, "src/a.py", "raise NotImplementedError\n")
    baseline_path = tmp_path / "code-ratchets-baseline.json"

    exit_code = check_code_ratchets.main(
        ["--root", str(tmp_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data == {"skip_xfail": 0, "todo_fixme_xxx": 0, "not_implemented_error": 1}


def test_increase_in_not_implemented_error_fails_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_py(tmp_path, "src/a.py", "raise NotImplementedError\nraise NotImplementedError\n")
    baseline_path = _write_baseline(tmp_path, {**_empty_baseline(), "not_implemented_error": 1})

    exit_code = check_code_ratchets.main(
        ["--root", str(tmp_path), "--baseline", str(baseline_path)]
    )

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "not_implemented_error" in out
    assert "1개 -> 2개" in out
    unchanged = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert unchanged["not_implemented_error"] == 1  # 실패 시 baseline 미변경


def test_increase_in_skip_xfail_fails_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_py(
        tmp_path,
        "tests/test_a.py",
        "import pytest\n\n@pytest.mark.skip\ndef test_a(): pass\n\n"
        "@pytest.mark.xfail\ndef test_b(): pass\n",
    )
    baseline_path = _write_baseline(tmp_path, {**_empty_baseline(), "skip_xfail": 1})

    exit_code = check_code_ratchets.main(
        ["--root", str(tmp_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 2
    assert "skip_xfail" in capsys.readouterr().out


def test_increase_in_todo_fixme_xxx_fails_red(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _write_py(tmp_path, "src/a.py", "# TODO one\n# FIXME two\n")
    baseline_path = _write_baseline(tmp_path, {**_empty_baseline(), "todo_fixme_xxx": 1})

    exit_code = check_code_ratchets.main(
        ["--root", str(tmp_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 2
    assert "todo_fixme_xxx" in capsys.readouterr().out


def test_decrease_without_update_leaves_baseline_unchanged(tmp_path: Path) -> None:
    _write_py(tmp_path, "src/a.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {**_empty_baseline(), "not_implemented_error": 3})

    exit_code = check_code_ratchets.main(
        ["--root", str(tmp_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["not_implemented_error"] == 3


def test_decrease_with_update_flag_ratchets_down(tmp_path: Path) -> None:
    _write_py(tmp_path, "src/a.py", "x = 1\n")
    baseline_path = _write_baseline(tmp_path, {**_empty_baseline(), "not_implemented_error": 3})

    exit_code = check_code_ratchets.main(
        ["--root", str(tmp_path), "--baseline", str(baseline_path), "--update"]
    )

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["not_implemented_error"] == 0


def test_equal_to_baseline_passes_and_keeps_baseline(tmp_path: Path) -> None:
    _write_py(tmp_path, "src/a.py", "raise NotImplementedError\n")
    baseline_path = _write_baseline(tmp_path, {**_empty_baseline(), "not_implemented_error": 1})

    exit_code = check_code_ratchets.main(
        ["--root", str(tmp_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0
    data = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert data["not_implemented_error"] == 1


def test_malformed_baseline_json_fails(tmp_path: Path) -> None:
    baseline_path = tmp_path / "code-ratchets-baseline.json"
    baseline_path.write_text("not json", encoding="utf-8")
    _write_py(tmp_path, "src/a.py", "x = 1\n")

    exit_code = check_code_ratchets.main(
        ["--root", str(tmp_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1


def test_baseline_missing_metric_key_fails(tmp_path: Path) -> None:
    baseline_path = tmp_path / "code-ratchets-baseline.json"
    baseline_path.write_text(json.dumps({"skip_xfail": 0}), encoding="utf-8")
    _write_py(tmp_path, "src/a.py", "x = 1\n")

    exit_code = check_code_ratchets.main(
        ["--root", str(tmp_path), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
