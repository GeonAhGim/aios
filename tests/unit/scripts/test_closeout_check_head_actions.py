"""scripts/closeout_check.py 단위 테스트 — task-2738(CLOSEOUT), 종료조건 12.

12(현재 HEAD의 GitHub Actions 녹색)와 그 재검증(`workflow_dispatch` 트리거 +
폴링) 로직을 다룬다. 종료조건 1~9는 `test_closeout_check.py`, 10~11은
`test_closeout_check_ops_hardening.py`, 리포트/CLI는
`test_closeout_check_report_cli.py`에 있다. 공용 로더는
`closeout_check_loader.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.scripts.closeout_check_loader import ROOT, cc

# --------------------------------------------------------------------------- 12: HEAD Actions 녹색


class _FakeGhRun:
    """`gh` CLI 서브프로세스 대역 — `subprocess.CompletedProcess`처럼 returncode/stdout만 흉내."""

    def __init__(self, responses: dict[str, tuple[int, str]]) -> None:
        self._responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> object:
        self.calls.append(list(args))
        key = args[0]
        returncode, stdout = self._responses.get(key, (1, ""))
        return _FakeCompleted(returncode=returncode, stdout=stdout)


class _FakeCompleted:
    def __init__(self, *, returncode: int, stdout: str) -> None:
        self.returncode = returncode
        self.stdout = stdout


def _runs_json(rows: list[dict[str, str]]) -> str:
    return json.dumps(rows)


def test_head_actions_green_fails_without_live_flag_and_makes_no_gh_call(tmp_path: Path) -> None:
    """gh_run을 안 넘기면 UNVERIFIED로 FAIL — 실제 gh 서브프로세스 호출은 없다."""
    result = cc.check_12_head_actions_green(tmp_path, git_head=lambda _root: "abc123")

    assert not result.passed
    assert cc.UNVERIFIED in result.detail


def test_head_actions_green_fails_when_git_head_unavailable(tmp_path: Path) -> None:
    result = cc.check_12_head_actions_green(tmp_path, git_head=lambda _root: None)

    assert not result.passed
    assert "git rev-parse" in result.detail


def test_head_actions_green_fails_when_gh_run_list_errors(tmp_path: Path) -> None:
    fake = _FakeGhRun({"run": (1, "")})

    result = cc.check_12_head_actions_green(tmp_path, gh_run=fake, git_head=lambda _root: "abc123")

    assert not result.passed
    assert "gh run list 실패" in result.detail


def test_head_actions_green_fails_on_head_mismatch(tmp_path: Path) -> None:
    """현재 main HEAD가 아니라 다른(과거) 커밋만 녹색이면 FAIL — '최근 녹색'과 구분."""
    rows = _runs_json(
        [
            {
                "headSha": "old-sha-that-is-green",
                "conclusion": "success",
                "status": "completed",
                "url": "https://example/run/1",
            }
        ]
    )
    fake = _FakeGhRun({"run": (0, rows)})

    result = cc.check_12_head_actions_green(
        tmp_path, gh_run=fake, git_head=lambda _root: "current-head-sha"
    )

    assert not result.passed
    assert "실행 없음" in result.detail
    assert "old-sha-" in result.detail


def test_head_actions_green_fails_when_head_run_concluded_failure(tmp_path: Path) -> None:
    rows = _runs_json(
        [
            {
                "headSha": "current-head-sha",
                "conclusion": "failure",
                "status": "completed",
                "url": "https://example/run/2",
            }
        ]
    )
    fake = _FakeGhRun({"run": (0, rows)})

    result = cc.check_12_head_actions_green(
        tmp_path, gh_run=fake, git_head=lambda _root: "current-head-sha"
    )

    assert not result.passed
    assert "conclusion=failure" in result.detail
    assert "https://example/run/2" in "".join(result.evidence)


def test_head_actions_green_passes_when_head_run_succeeded(tmp_path: Path) -> None:
    rows = _runs_json(
        [
            {
                "headSha": "current-head-sha",
                "conclusion": "success",
                "status": "completed",
                "url": "https://example/run/3",
            }
        ]
    )
    fake = _FakeGhRun({"run": (0, rows)})

    result = cc.check_12_head_actions_green(
        tmp_path, gh_run=fake, git_head=lambda _root: "current-head-sha"
    )

    assert result.passed
    assert "success" in result.detail


def test_head_actions_green_against_real_repo_head_without_live_check() -> None:
    """네트워크 호출 없이 실저장소 HEAD로 호출해도 UNVERIFIED로만 FAIL한다(현재 상태 고정)."""
    result = cc.check_12_head_actions_green(ROOT)
    assert not result.passed
    assert cc.UNVERIFIED in result.detail


def test_run_all_includes_head_green_result_when_supplied(tmp_path: Path) -> None:
    injected = cc.CheckResult("12_head_actions_green", "현재 HEAD Actions 녹색", True, (), "고정")

    results = cc.run_all(tmp_path, head_green_result=injected)

    assert results[-1] is injected


# ------------------------------------------------------------- HEAD 재검증(workflow_dispatch)


def test_trigger_head_workflow_dispatch_true_on_success() -> None:
    fake = _FakeGhRun({"workflow": (0, "")})

    assert cc.trigger_head_workflow_dispatch(fake) is True


def test_trigger_head_workflow_dispatch_false_on_failure() -> None:
    fake = _FakeGhRun({"workflow": (1, "")})

    assert cc.trigger_head_workflow_dispatch(fake) is False


def test_wait_for_head_green_returns_immediately_when_already_green(tmp_path: Path) -> None:
    rows = _runs_json(
        [
            {
                "headSha": "current-head-sha",
                "conclusion": "success",
                "status": "completed",
                "url": "https://example/run/4",
            }
        ]
    )
    fake = _FakeGhRun({"run": (0, rows)})
    sleeps: list[float] = []

    result = cc.wait_for_head_green(
        tmp_path, gh_run=fake, git_head=lambda _root: "current-head-sha", sleep=sleeps.append
    )

    assert result.passed
    assert sleeps == []
    assert not any(call[0] == "workflow" for call in fake.calls)


def test_wait_for_head_green_dispatch_failure_short_circuits(tmp_path: Path) -> None:
    fake = _FakeGhRun({"run": (0, _runs_json([])), "workflow": (1, "")})

    result = cc.wait_for_head_green(
        tmp_path, gh_run=fake, git_head=lambda _root: "current-head-sha", sleep=lambda _s: None
    )

    assert not result.passed
    assert "workflow_dispatch 트리거 실패" in result.detail


def test_wait_for_head_green_polls_then_succeeds(tmp_path: Path) -> None:
    """디스패치 직후엔 아직 안 보이다가, 두 번째 폴에서 success로 나타난다."""
    call_count = {"run": 0}

    def gh_run(args: list[str]) -> _FakeCompleted:
        if args[0] == "workflow":
            return _FakeCompleted(returncode=0, stdout="")
        call_count["run"] += 1
        if call_count["run"] < 2:
            return _FakeCompleted(returncode=0, stdout=_runs_json([]))
        rows = _runs_json(
            [
                {
                    "headSha": "current-head-sha",
                    "conclusion": "success",
                    "status": "completed",
                    "url": "https://example/run/5",
                }
            ]
        )
        return _FakeCompleted(returncode=0, stdout=rows)

    times = iter([0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0])
    result = cc.wait_for_head_green(
        tmp_path,
        gh_run=gh_run,
        git_head=lambda _root: "current-head-sha",
        sleep=lambda _s: None,
        now=lambda: next(times),
        timeout_sec=100.0,
        poll_sec=1.0,
    )

    assert result.passed
    assert call_count["run"] >= 2


def test_wait_for_head_green_times_out_when_never_completes(tmp_path: Path) -> None:
    fake = _FakeGhRun({"run": (0, _runs_json([])), "workflow": (0, "")})
    times = iter([0.0, 200.0])  # deadline(=100)을 즉시 넘겨 폴링 루프를 0회로 만든다

    result = cc.wait_for_head_green(
        tmp_path,
        gh_run=fake,
        git_head=lambda _root: "current-head-sha",
        sleep=lambda _s: None,
        now=lambda: next(times),
        timeout_sec=100.0,
        poll_sec=1.0,
    )

    assert not result.passed
    assert "대기했지만 완료되지 않음" in result.detail
