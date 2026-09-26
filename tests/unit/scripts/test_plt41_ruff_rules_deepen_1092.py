"""pyproject.toml ruff 규칙군 확장(S,BLE,ARG,PGH,T20) 회귀 가드 -- task-1092/PLT-41
DEEPEN(task-3172).

task-1092(f8b3110c)는 `[tool.ruff.lint] select`에 S,BLE,ARG,PGH,T20을 추가하고
기존 위반을 파일 단위 per-file-ignores로 정리했을 뿐, 그 확장이 실제로 위반을
잡는지 -- 그리고 같은 커밋이 함께 넓힌 `tests/**`/`scripts/**` 블랭킷
per-file-ignores가 새 규칙군까지 과잉 억제하지 않는지 -- 증명하는 테스트가 전무
했다(지목 가능한 근거 0, task note). 이 파일은 실제 `ruff` 바이너리를 합성
트리에 대해 호출해 D2 DoD(negative >=3, 실패주입 1, 성능단언 1, 게이트 적색재현
1)를 채운다.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests._perf.relative_budget import RelativeBudget

ROOT = Path(__file__).resolve().parents[3]

# task-7741(esc-ci-coverage): a fixed 5s wall-clock budget around a real ruff
# subprocess invocation is the same false-red shape scripts/coverage_ratchet.py
# documents for the local CI coverage step (pytest --cov=src line-tracer
# overhead in *this* process + shared-host contention on the ruff child
# process) -- local measurement showed the op itself already spending
# 2.5-4.1s of the 5s budget with no contention at all. Switched to
# RelativeBudget (task-7631 pattern, mode="wall" since the measured op is an
# external subprocess whose CPU time this process's time.process_time()
# cannot see) -- a ratio against a same-process calibration loop instead of
# an absolute second figure, so it self-corrects for host speed/load.
_RUFF_CHECK_MAX_RATIO = 150.0
PYPROJECT = ROOT / "pyproject.toml"

# task-1092가 추가한 select 확장 + 같은 커밋이 넓힌 두 블랭킷 per-file-ignores
# 존(tests/**, scripts/**)만 뽑아온 축소판. ruff는 per-file-ignores의 glob을
# "발견된 프로젝트 루트"(체크 대상 cwd) 기준으로 풀기 때문에, 이 문자열을
# tmp_path/pyproject.toml로 써 넣고 cwd=tmp_path로 실행해야 실제 저장소와
# 동일하게 tests/**·scripts/** 글롭이 매치된다.
_REPO_SHAPED_CONFIG = """
[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "TID251", "S", "BLE", "ARG", "PGH", "T20"]

[tool.ruff.lint.per-file-ignores]
"tests/**" = [
    "TID251", "S101", "ARG001", "ARG002", "ARG005",
    "S105", "S106", "S311", "T201", "S603", "S607",
]
"scripts/**" = ["TID251", "T201", "S603", "S607", "S314"]
"""

_PRE_PLT41_CONFIG = """
[tool.ruff]
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "TID251"]
"""


def _write(base: Path, relative: str, content: str) -> Path:
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _run_ruff(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "ruff", "check", "--no-cache", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


# ---------------------------------------------------------------------------
# happy path -- pyproject.toml declares what task-1092 claims
# ---------------------------------------------------------------------------


def test_pyproject_select_declares_all_five_plt41_rule_groups() -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r"\[tool\.ruff\.lint\]\nselect\s*=\s*\[([^\]]*)\]", text)
    assert match is not None, "[tool.ruff.lint] select 라인을 찾을 수 없다"
    declared = {code.strip().strip('"') for code in match.group(1).split(",")}
    assert {"S", "BLE", "ARG", "PGH", "T20"} <= declared
    # task-1092 이전부터 있던 기본 규칙군이 확장 과정에서 빠지지 않았는지도 확인.
    assert {"E", "F", "I", "UP", "B", "TID251"} <= declared


# ---------------------------------------------------------------------------
# negative (>= 3) -- 새로 selected된 각 규칙군이 실제로 위반을 잡는다
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("src/a.py", 'PASSWORD = "hunter2"\n', "S105"),
        (
            "src/b.py",
            "def f() -> int | None:\n"
            "    try:\n"
            "        return 1 // 0\n"
            "    except Exception:\n"
            "        return None\n",
            "BLE001",
        ),
        ("src/c.py", "def f(unused):\n    return 1\n", "ARG001"),
        ("src/d.py", 'def f() -> None:\n    print("hi")\n', "T201"),
        (
            "src/e.py",
            "def f() -> int:\n    x = 1  # type: ignore\n    return x\n",
            "PGH003",
        ),
    ],
    ids=["S105", "BLE001", "ARG001", "T201", "PGH003"],
)
def test_ruff_catches_plt41_rule_group_violation(
    tmp_path: Path, filename: str, content: str, code: str
) -> None:
    _write(tmp_path, "pyproject.toml", _REPO_SHAPED_CONFIG)
    _write(tmp_path, filename, content)

    result = _run_ruff(tmp_path, filename)

    assert result.returncode == 1, result.stdout
    assert code in result.stdout


def test_ruff_passes_clean_file_with_extended_select(tmp_path: Path) -> None:
    """양성 대조: 확장된 select 자체가 무해한 코드까지 과잉 탐지하지 않는다."""
    _write(tmp_path, "pyproject.toml", _REPO_SHAPED_CONFIG)
    _write(tmp_path, "src/clean.py", "def f(x: int) -> int:\n    return x + 1\n")

    result = _run_ruff(tmp_path, "src/clean.py")

    assert result.returncode == 0, result.stdout


# ---------------------------------------------------------------------------
# failure injection (1) -- tests/** 블랭킷 무시가 새 규칙군까지 과잉 억제하면
# 회귀다. task-1092가 tests/**에 넣은 건 S101/ARG001/ARG002/ARG005/S105/S106/
# S311/T201/S603/S607뿐이고 BLE001은 없다 -- 실제로 새지 않는지 주입해 확인.
# ---------------------------------------------------------------------------


def test_tests_zone_blanket_ignore_does_not_leak_into_unlisted_rule(tmp_path: Path) -> None:
    _write(tmp_path, "pyproject.toml", _REPO_SHAPED_CONFIG)
    content = (
        "def f() -> int | None:\n"
        "    try:\n"
        "        return 1 // 0\n"
        "    except Exception:\n"
        "        return None\n"
    )
    _write(tmp_path, "tests/test_b.py", content)

    result = _run_ruff(tmp_path, "tests/test_b.py")

    assert result.returncode == 1, result.stdout
    assert "BLE001" in result.stdout

    # 대조군: 같은 tests/** 존에서 실제로 예외 목록에 있는 S105/ARG001은
    # 블랭킷대로 억제된다(과잉 억제가 아니라 정확히 그 규칙만 새는지 확인).
    _write(tmp_path, "tests/test_a.py", 'PASSWORD = "hunter2"\n')
    _write(tmp_path, "tests/test_c.py", "def f(unused):\n    return 1\n")
    suppressed = _run_ruff(tmp_path, "tests/test_a.py", "tests/test_c.py")
    assert suppressed.returncode == 0, suppressed.stdout


# ---------------------------------------------------------------------------
# 성능 단언 (1)
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_ruff_check_repo_perf_budget() -> None:
    """CLAUDE.md 게이트 커맨드(`ruff check src tests scripts`)를 실제로 돌려
    자기보정 예산 안에 끝나는지 확인한다(스캔 결과는 exit 0 -- 이 리프가 정리한
    위반이 회귀하지 않았다는 뜻이기도 하다)."""
    result: subprocess.CompletedProcess[str] | None = None

    def run() -> None:
        nonlocal result
        result = _run_ruff(ROOT, "src", "tests", "scripts")

    RelativeBudget().assert_within(
        run,
        max_ratio=_RUFF_CHECK_MAX_RATIO,
        mode="wall",
        n=1,
        warmup=0,
        label="ruff check src tests scripts",
    )

    assert result is not None
    assert result.returncode == 0, result.stdout


# ---------------------------------------------------------------------------
# 게이트 적색 재현 (1)
# ---------------------------------------------------------------------------


def test_gate_red_repro_pre_plt41_select_was_blind(tmp_path: Path) -> None:
    """task-1092 이전 select(S/BLE/ARG/PGH/T20 없음)로는 하드코딩 비밀번호·
    미사용 인자·print가 전부 통과(green)했다는 것과, task-1092가 병합한 select
    로는 같은 파일이 즉시 적색(exit 1)이 된다는 것을 같은 소스로 재현한다."""
    violating = 'PASSWORD = "hunter2"\n\ndef f(unused) -> None:\n    print("hi")\n'
    _write(tmp_path, "src/a.py", violating)

    _write(tmp_path, "pyproject.toml", _PRE_PLT41_CONFIG)
    pre = _run_ruff(tmp_path, "src/a.py")
    assert pre.returncode == 0, pre.stdout

    _write(tmp_path, "pyproject.toml", _REPO_SHAPED_CONFIG)
    post = _run_ruff(tmp_path, "src/a.py")
    assert post.returncode == 1
    assert "S105" in post.stdout
    assert "ARG001" in post.stdout
    assert "T201" in post.stdout
