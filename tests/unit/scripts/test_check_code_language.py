"""scripts/check_code_language.py 단위 테스트 — task-4966.

task-4238 재시도 중 pre_push_gate가 무관 파일 위반으로 push를 거부했다. 원인은
4acc2620(다른 커밋)이 잘못된 --target(빈/존재하지 않는 경로)으로 스캐너를 돌려
측정치 0을 "감소"로 오인해 baseline을 0으로 낮춰 쓴 것이었다 — 그 뒤 어떤 push든
저장소에 남아있는 기존 한글 주석 전부를 초과분으로 잡아 거부당했다. 이 파일은
그 실패 모드(scanned==0을 감지해 baseline을 건드리지 않고 거부)를 고정한다.
"""

from __future__ import annotations

import importlib.util
import shutil
import sys
import time
import uuid
from collections.abc import Iterator
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


check_code_language = _load_module("check_code_language", SCRIPTS_DIR / "check_code_language.py")


def _write_py(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def in_repo_dir() -> Iterator[Path]:
    """count_tree()가 위반 파일의 상대 경로를 ROOT 기준으로 계산하므로(per_file
    보고용), 실제 위반이 있는 픽스처는 pytest의 기본 tmp_path(저장소 밖)가 아니라
    ROOT 아래에 둬야 한다."""
    d = ROOT / f"_tmp_check_lang_test_{uuid.uuid4().hex}"
    d.mkdir()
    try:
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# count_file — 순수 스캐너
# ---------------------------------------------------------------------------


def test_count_file_flags_hangul_comment(tmp_path: Path) -> None:
    p = _write_py(tmp_path / "a.py", "x = 1  # 한글 주석\ny = 2  # english comment\n")

    assert check_code_language.count_file(p) == 1


def test_count_file_flags_hangul_docstring(tmp_path: Path) -> None:
    p = _write_py(tmp_path / "a.py", '"""한글 독스트링."""\n\n\ndef f() -> None:\n    pass\n')

    assert check_code_language.count_file(p) == 1


# ---------------------------------------------------------------------------
# negative tests — 대상이 아닌 것을 세지 않는다
# ---------------------------------------------------------------------------


def test_count_file_ignores_hangul_in_string_literal(tmp_path: Path) -> None:
    """일반 문자열 리터럴은 제품 텍스트(i18n 대상)이지 코드 언어 위반이 아니다."""
    p = _write_py(tmp_path / "a.py", 'msg = "사용자에게 보여줄 한글 문자열"\n')

    assert check_code_language.count_file(p) == 0


def test_count_file_english_only_is_zero(tmp_path: Path) -> None:
    p = _write_py(
        tmp_path / "a.py",
        '"""English docstring."""\n\n\ndef f() -> None:\n    pass  # english comment\n',
    )

    assert check_code_language.count_file(p) == 0


def test_count_file_syntax_error_returns_zero_not_crash(tmp_path: Path) -> None:
    """파싱 불가능한 파일(다른 워커가 동시에 쓰는 중인 파일 등)에서 예외로 전체
    스캔이 죽으면 안 된다 -- 0을 반환하고 계속 진행한다."""
    p = _write_py(tmp_path / "broken.py", "def f(:\n    한글 broken syntax\n")

    assert check_code_language.count_file(p) == 0


# ---------------------------------------------------------------------------
# count_tree — scanned==0 회귀 방지 (task-4966 핵심)
# ---------------------------------------------------------------------------


def test_count_tree_reports_zero_scanned_for_empty_dir(tmp_path: Path) -> None:
    empty_dir = tmp_path / "nothing-here"
    empty_dir.mkdir()

    total, per_file, scanned = check_code_language.count_tree(empty_dir)

    assert (total, per_file, scanned) == (0, [], 0)


def test_count_tree_reports_zero_scanned_for_missing_dir(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist"

    total, per_file, scanned = check_code_language.count_tree(missing)

    assert (total, per_file, scanned) == (0, [], 0)


# ---------------------------------------------------------------------------
# main() — 실패 주입: 잘못된 --target이 baseline을 침묵 속에 0으로 낮추면 안 된다
# ---------------------------------------------------------------------------


def test_main_refuses_to_touch_baseline_when_target_is_empty(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty_target = tmp_path / "wrong-cwd-target"
    empty_target.mkdir()
    baseline_path = tmp_path / "code-language-baseline.txt"
    baseline_path.write_text("13063\n", encoding="utf-8")

    exit_code = check_code_language.main(
        ["--target", str(empty_target), "--baseline", str(baseline_path)]
    )

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out
    assert "no .py files" in out
    # 회귀 재현: 이 assert가 없었다면 4acc2620처럼 baseline이 0으로 덮어써졌을 것이다.
    assert baseline_path.read_text(encoding="utf-8").strip() == "13063"


def test_main_gate_red_reproduction_real_violation_still_fails(
    in_repo_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """scanned==0 가드가 진짜 위반까지 숨기면 안 된다 -- 실제 초과분이 있으면
    여전히 FAIL·exit=1이어야 한다 (원래 게이트의 red 경로 재현)."""
    target = in_repo_dir / "src"
    _write_py(target / "a.py", "x = 1  # 한글 위반\n")
    baseline_path = tmp_path / "code-language-baseline.txt"
    baseline_path.write_text("0\n", encoding="utf-8")

    exit_code = check_code_language.main(
        ["--target", str(target), "--baseline", str(baseline_path)]
    )

    out = capsys.readouterr().out
    assert exit_code == 1
    assert "FAIL: Hangul comment/docstring lines 0 -> 1" in out
    assert baseline_path.read_text(encoding="utf-8").strip() == "0"  # 실패 시 미변경


def test_main_decrease_reports_without_update_baseline_flag(
    in_repo_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real, plausible reduction is not silently written -- --update-baseline is
    required, so a plain CI run never mutates the baseline file underneath a reviewer."""
    target = in_repo_dir / "src"
    _write_py(
        target / "a.py",
        "a = 1  # 한글 하나\nb = 2  # 한글 둘\nc = 3  # 한글 셋\n",
    )
    baseline_path = tmp_path / "code-language-baseline.txt"
    baseline_path.write_text("5\n", encoding="utf-8")

    exit_code = check_code_language.main(
        ["--target", str(target), "--baseline", str(baseline_path)]
    )

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "reduced" in out
    assert "--update-baseline" in out
    assert baseline_path.read_text(encoding="utf-8").strip() == "5"


def test_main_decrease_writes_baseline_with_update_flag(
    in_repo_dir: Path, tmp_path: Path
) -> None:
    target = in_repo_dir / "src"
    _write_py(target / "a.py", "x = 1  # 한글 하나\n")
    baseline_path = tmp_path / "code-language-baseline.txt"
    baseline_path.write_text("2\n", encoding="utf-8")

    exit_code = check_code_language.main(
        ["--target", str(target), "--baseline", str(baseline_path), "--update-baseline"]
    )

    assert exit_code == 0
    assert baseline_path.read_text(encoding="utf-8").strip() == "1"


# ---------------------------------------------------------------------------
# 하한 가드 -- task-5303: 측정치 0(또는 직전 baseline의 50% 미만)은 baseline을
# 건드리지 않고 rc=2로 거부한다 (4acc2620, c2770645/task-4328 재발 방지)
# ---------------------------------------------------------------------------


def test_main_zero_total_refuses_to_touch_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """전량 영어(측정치 0)는 정말 다 고쳤다는 증거가 아니라 십중팔구 측정 오류다 --
    baseline이 무엇이든 rc=2로 거부하고 파일은 그대로 둔다."""
    target = tmp_path / "src"
    _write_py(target / "a.py", "x = 1  # english only\n")
    baseline_path = tmp_path / "code-language-baseline.txt"
    baseline_path.write_text("5\n", encoding="utf-8")

    exit_code = check_code_language.main(
        ["--target", str(target), "--baseline", str(baseline_path), "--update-baseline"]
    )

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "FAIL" in out
    assert "implausible" in out
    assert baseline_path.read_text(encoding="utf-8").strip() == "5"


def test_main_below_half_baseline_refuses_to_touch_baseline(
    in_repo_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """직전 baseline의 50% 미만으로 급락한 측정치도 같은 실패 모드다(4acc2620처럼
    잘못된 target이 부분적으로만 스캔됐을 때) -- 0이 아니어도 거부한다."""
    target = in_repo_dir / "src"
    _write_py(target / "a.py", "x = 1  # 한글\n")
    baseline_path = tmp_path / "code-language-baseline.txt"
    baseline_path.write_text("100\n", encoding="utf-8")

    exit_code = check_code_language.main(
        ["--target", str(target), "--baseline", str(baseline_path), "--update-baseline"]
    )

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "implausible" in out
    assert baseline_path.read_text(encoding="utf-8").strip() == "100"


def test_main_missing_baseline_requires_update_flag(
    in_repo_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = in_repo_dir / "src"
    _write_py(target / "a.py", "x = 1  # 한글\n")
    baseline_path = tmp_path / "does-not-exist-baseline.txt"

    exit_code = check_code_language.main(
        ["--target", str(target), "--baseline", str(baseline_path)]
    )

    out = capsys.readouterr().out
    assert exit_code == 2
    assert "--update-baseline" in out
    assert not baseline_path.exists()


# ---------------------------------------------------------------------------
# 성능 — 실제 src/ 트리 전체 스캔이 예산 내에 끝난다
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_count_tree_full_src_scan_completes_within_budget() -> None:
    start = time.perf_counter()
    total, _per_file, scanned = check_code_language.count_tree(ROOT / "src")
    elapsed = time.perf_counter() - start

    assert scanned > 0
    assert total >= 0
    assert elapsed < 30.0, f"full src/ Hangul scan took {elapsed:.1f}s, budget is 30s"
