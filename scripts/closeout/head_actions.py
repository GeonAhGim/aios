"""12번째 종료 기준 -- 현재 HEAD가 quality.yml에서 success(ADR-2026-09-10-C
Decision 5) -- task-6475 분할 조각.

`scripts/closeout_check.py`의 책임 분할: 이 모듈은 `gh` CLI 기반 GitHub
Actions 조회/트리거/폴링만 담당한다(네트워크·GitHub 인증 필요 -- 저장소 안
정적 증거만 보는 다른 조각들과 달리 `gh_run`을 명시적으로 넘겨야 실제
조회한다).
"""

from __future__ import annotations

import json
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from scripts.closeout.common import UNVERIFIED, CheckResult

GH_QUALITY_WORKFLOW = "quality.yml"
HEAD_WAIT_TIMEOUT_SEC = 40 * 60
HEAD_WAIT_POLL_SEC = 30.0

GhRunner = Callable[[Sequence[str]], "subprocess.CompletedProcess[str]"]


@dataclass(frozen=True)
class ActionsRun:
    head_sha: str
    conclusion: str | None
    status: str
    url: str


def default_gh_run(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603,S607 - 고정 gh 서브커맨드, 사용자 입력 없음
        ["gh", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


def default_git_head(repo_root: Path) -> str | None:
    result = subprocess.run(  # noqa: S603,S607 - 고정 git 서브커맨드, 사용자 입력 없음
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    if result.returncode != 0:
        return None
    sha = result.stdout.strip()
    return sha or None


def _list_quality_runs(gh_run: GhRunner, *, limit: int = 20) -> list[ActionsRun] | None:
    result = gh_run(
        [
            "run",
            "list",
            "--workflow",
            GH_QUALITY_WORKFLOW,
            "--json",
            "headSha,conclusion,status,url",
            "--limit",
            str(limit),
        ]
    )
    if result.returncode != 0:
        return None
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return [
        ActionsRun(
            head_sha=row.get("headSha", ""),
            conclusion=row.get("conclusion") or None,
            status=row.get("status", ""),
            url=row.get("url", ""),
        )
        for row in rows
    ]


def check_12_head_actions_green(
    repo_root: Path,
    *,
    gh_run: GhRunner | None = None,
    git_head: Callable[[Path], str | None] = default_git_head,
) -> CheckResult:
    """기준12(ADR-2026-09-10-C Decision 5) -- 현재 main HEAD가 quality.yml에서 success.

    "최근 어떤 커밋이 녹색이었나"와 구분한다: 과거에 성공한 실행이 있어도
    그 headSha가 지금의 HEAD와 다르면 FAIL이다 -- 그 사이 커밋들은 Actions
    독립 환경에서 아직 검증되지 않았을 수 있다. `gh` 호출은 네트워크·GitHub
    인증에 의존하므로 이 함수 혼자서는 완전히 판정할 수 없다 -- 이 스크립트의
    다른 정적 검사와 달리 `gh_run`을 명시적으로 넘겨야 실제로 조회한다
    (안 넘기면 UNVERIFIED로 FAIL -- 모른다=통과 아님, ADR-D 원칙).
    """
    key, title = "12_head_actions_green", "현재 HEAD Actions 녹색"
    head_sha = git_head(repo_root)
    if head_sha is None:
        return CheckResult(key, title, False, (), "git rev-parse HEAD 실패 — 저장소 아님/git 없음")
    if gh_run is None:
        return CheckResult(
            key,
            title,
            False,
            (f"head={head_sha}", UNVERIFIED),
            f"{UNVERIFIED}: --live-head-check 없이는 gh를 호출하지 않는다",
        )
    runs = _list_quality_runs(gh_run)
    if runs is None:
        return CheckResult(
            key,
            title,
            False,
            (f"head={head_sha}",),
            "gh run list 실패(네트워크/인증 문제로 추정) — 미검증",
        )
    match = next((r for r in runs if r.head_sha == head_sha), None)
    if match is None:
        recent_note = (
            f"최근 실행 headSha={runs[0].head_sha[:8]}({runs[0].conclusion})"
            if runs
            else "quality.yml 실행 이력 없음"
        )
        return CheckResult(
            key,
            title,
            False,
            (f"head={head_sha}", recent_note),
            f"HEAD({head_sha[:8]})에 대한 quality.yml 실행 없음 — {recent_note}"
            "(다른 커밋이 녹색인 것과는 구분)",
        )
    passed = match.conclusion == "success"
    evidence = (f"head={head_sha}", f"run_url={match.url}", f"conclusion={match.conclusion}")
    detail = (
        f"HEAD({head_sha[:8]})가 quality.yml에서 success: {match.url}"
        if passed
        else f"HEAD({head_sha[:8]}) quality.yml conclusion={match.conclusion}: {match.url}"
    )
    return CheckResult(key, title, passed, evidence, detail)


def trigger_head_workflow_dispatch(
    gh_run: GhRunner = default_gh_run, *, ref: str = "main"
) -> bool:
    """`quality.yml`을 workflow_dispatch로 수동 트리거한다(HEAD 재검증 스크립트)."""
    result = gh_run(["workflow", "run", GH_QUALITY_WORKFLOW, "--ref", ref])
    return result.returncode == 0


def wait_for_head_green(
    repo_root: Path,
    *,
    gh_run: GhRunner = default_gh_run,
    git_head: Callable[[Path], str | None] = default_git_head,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    timeout_sec: float = HEAD_WAIT_TIMEOUT_SEC,
    poll_sec: float = HEAD_WAIT_POLL_SEC,
) -> CheckResult:
    """HEAD가 이미 녹색이면 그대로 반환하고, 아니면 workflow_dispatch로 재검증을
    트리거한 뒤 최대 `timeout_sec`(기본 40분) 폴링하며 결과를 기다린다.

    Actions 스케줄(3시간 주기)을 기다리지 않고 지금 이 HEAD를 즉시 재검증하기
    위한 수동 트리거 경로 -- closeout 직전 "혹시 아직 한 번도 안 돌았나"를
    확인할 때 쓴다.
    """
    result = check_12_head_actions_green(repo_root, gh_run=gh_run, git_head=git_head)
    if result.passed:
        return result
    head_sha = git_head(repo_root)
    if head_sha is None:
        return result
    if not trigger_head_workflow_dispatch(gh_run):
        return CheckResult(
            result.key,
            result.title,
            False,
            result.evidence,
            f"{result.detail}; workflow_dispatch 트리거 실패",
        )
    deadline = now() + timeout_sec
    while now() < deadline:
        sleep(poll_sec)
        runs = _list_quality_runs(gh_run)
        if not runs:
            continue
        match = next((r for r in runs if r.head_sha == head_sha), None)
        if match is None or match.status != "completed":
            continue
        passed = match.conclusion == "success"
        evidence = (f"head={head_sha}", f"run_url={match.url}", f"conclusion={match.conclusion}")
        detail = (
            f"workflow_dispatch 재검증 후 HEAD({head_sha[:8]}) success: {match.url}"
            if passed
            else f"workflow_dispatch 재검증 후 HEAD({head_sha[:8]}) "
            f"conclusion={match.conclusion}: {match.url}"
        )
        return CheckResult(result.key, result.title, passed, evidence, detail)
    minutes = int(timeout_sec // 60)
    return CheckResult(
        result.key,
        result.title,
        False,
        result.evidence,
        f"{result.detail}; workflow_dispatch 트리거 후 {minutes}분 대기했지만 완료되지 않음",
    )
