"""task-2793 DEEPEN of task-2096 (docs/audit/DEPTH_L4_BR.md #2096, D0 실측 vs D2 하한).

감사 근거: task-2096("BR-13 결함 수정(리뷰 task-2084 REJECT): 계약 테스트가
mypy --strict를 통과하지 않는다")는 commit=""(빈 문자열)이고 이 id의 커밋은
저장소 어디에도 존재하지 않는다 -- 완전한 유령 done 기록(D0). 실제 고침은
task-2021(commit be6c383e)이 수행했다:
`test_contract_helper_detects_mismatched_path`의 `case.path + "-broken"`가
`GeneratedCase.path: str | None`에 대해 mypy --strict에서 `Unsupported left
operand type for + ("None")`로 실패했고, REST 케이스는 항상 path가 있다는
불변조건을 `assert case.path is not None`으로 명시해 없앴다.

task-2096/task-2021 어느 쪽 커밋에도 "이 파일이 실제로 mypy --strict를
통과한다"·"그 assert가 없으면 정확히 그 오류로 다시 실패한다"는 두 주장을
커밋된 자동 테스트로 남긴 적이 없다(리뷰 당시 수기 확인뿐 -- 위 두 커밋
메시지 어디에도 반복 가능한 검증 수단이 없다). 이 파일이 그 증빙을 채운다:

1. `test_contract_test_and_fixture_pass_mypy_strict` -- 대상 두 파일이
   `[tool.mypy] strict = true`(pyproject.toml)로 실제 통과함을 자식 프로세스
   mypy 호출로 증명한다("수기로 확인했다"가 아니라 커밋된 반복 가능 테스트).
2. `test_mypy_strict_gate_turns_red_when_rest_path_invariant_assert_is_removed`
   -- be6c383e가 추가한 `assert case.path is not None`을 `--shadow-file`로
   (실제 디스크 파일은 건드리지 않고) 제거한 내용을 mypy에 보여주면, 정확히
   그 커밋 메시지가 기록한 오류로 다시 실패함을 증명한다(red-then-green) --
   이 부재가 바로 task-2096이 고쳤다고 주장한 결함 그 자체다.

`--shadow-file`은 mypy가 IDE/데몬 통합을 위해 제공하는 공식 옵션으로,
디스크의 원본 파일 대신 지정한 다른 파일의 내용을 그 경로인 것처럼 읽게
한다 -- 실제 리포 파일을 임시로 덮어썼다 복원하는 방식보다 안전하다(테스트
중간에 죽어도 리포가 변형된 채 남지 않는다).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
_TARGET = "tests/contract/exchanges/kis/test_generated_contract.py"
_FIXTURE = "tests/fixtures/kis/generated_cases.py"
_GUARD_LINE = '    assert case.path is not None, "REST case는 path가 None일 수 없음"\n'
_TIMEOUT_S = 90


def _run_mypy_strict(*extra_args: str) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "mypy", "--strict", *extra_args, _TARGET, _FIXTURE]
    return subprocess.run(
        command,
        cwd=_REPO_ROOT,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=_TIMEOUT_S,
        check=False,
    )


def test_guard_line_present_exactly_once() -> None:
    """뮤테이션 테스트가 문자열 치환에 의존하므로, task-2021(be6c383e)이
    추가한 REST 불변조건 assert가 예상 형태 그대로 정확히 1곳에 있는지 먼저
    확인한다(소스가 바뀌면 아래 gate-red 테스트가 무의미하게 항상 통과하는
    것을 방지)."""
    source = (_REPO_ROOT / _TARGET).read_text(encoding="utf-8")
    assert source.count(_GUARD_LINE) == 1


def test_contract_test_and_fixture_pass_mypy_strict() -> None:
    """task-2096이 "고쳤다"고 주장했던 상태(계약 테스트가 mypy --strict를
    통과함)를 커밋된 자동 테스트로 못박는다 -- 지금까지는 리뷰(task-2084)나
    QA(task-2021)가 사람이 그때그때 `mypy --strict`를 손으로 돌려 확인했을
    뿐, 그 확인 자체가 회귀 가드로 남지 않았다."""
    result = _run_mypy_strict()
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Success: no issues found" in result.stdout


def test_mypy_strict_gate_turns_red_when_rest_path_invariant_assert_is_removed(
    tmp_path: Path,
) -> None:
    """be6c383e(task-2021)가 추가한 `assert case.path is not None`을
    (디스크 원본은 그대로 두고) `--shadow-file`로만 제거한 채 mypy에 보여주면,
    커밋 메시지가 기록한 바로 그 오류
    (`Unsupported left operand type for + ("None")`)로 다시 실패해야 한다 --
    task-2096이 고쳤다고 주장한 결함이 실재했고, 그 수정이 지금도 이 오류를
    막고 있다는 유일한 자동 증거."""
    original = (_REPO_ROOT / _TARGET).read_text(encoding="utf-8")
    mutated = original.replace(_GUARD_LINE, "")
    assert mutated != original

    shadow_file = tmp_path / "mutated_test_generated_contract.py"
    shadow_file.write_text(mutated, encoding="utf-8")

    result = _run_mypy_strict("--shadow-file", _TARGET, str(shadow_file))
    assert result.returncode != 0, (
        "REST path None 불변조건 assert를 제거했는데도 mypy --strict가 통과함 -- "
        "task-2096/task-2021이 고쳤다고 주장한 오탐 회귀를 이 게이트가 더 이상 "
        "잡지 못합니다.\n" + result.stdout + result.stderr
    )
    assert 'Unsupported left operand type for + ("None")' in result.stdout
