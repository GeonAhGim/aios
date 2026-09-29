"""10번(운영)·13번(사용자 여정 J1~J3) 종료 기준 -- task-7857 분할 조각.

`scripts/closeout_check.py`의 책임 분할: 이 모듈은 외부 리포트(`--ci-report`/
`--guard-report`) 스키마 판정과 그에 의존하는 두 항목만 담당한다 -- task-6475
분할(11번 하드닝/12번 HEAD Actions 녹색)과 같은 전례를 따른다(ADR-2026-09-10-C
§7 LOC 관측 임계, closeout_check.py가 다시 500줄을 넘겨 분리했다).
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from scripts.closeout.common import UNVERIFIED, CheckResult, present_missing, read_text

JOURNEY_SPECS: tuple[str, ...] = (
    "frontend/e2e/journey-j1-onboarding-to-dashboard.spec.ts",
    "frontend/e2e/journey-j2-discover-to-backtest.spec.ts",
    "frontend/e2e/journey-j3-paper-order-to-position.spec.ts",
)


def check_10_ops(
    repo_root: Path, *, ci_report: Path | None, guard_report: Path | None
) -> CheckResult:
    """기준10 — 로컬 CI 녹색 + Guard veto 0 + INVARIANTS 위반 0 + RED_TEAM P0 미해결 0.

    ci_report/guard_report는 이 저장소에 실제로 존재하는 두 산출 경로 모두를
    지원한다(task-7857 근본 원인) — `pm/local_ci.py`(top-level `"ok": bool`)/
    `meta/guards/run_guards.py --json`(`"vetoed": bool`)를 직접 넘기는 경로와,
    `pm/healthcheck.py`의 `mvp1_gate` 자동 배선(`_write_closeout_ci_report`/
    `_write_closeout_guard_report`)이 그 값을 옮겨 쓰는 `{"passed": bool}`/
    `{"veto_count": int}` 경로. task-6497은 전자만 지원하도록 고쳤는데, 실제
    운영에서 mvp1_gate는 항상 후자 스키마로 리포트를 넘겨 이 항목이 로컬 CI
    실제 결과와 무관하게 항상 FAIL로 고정됐다(재현: `_load_ci_report_ok`에
    `{"passed": true}`만 있는 리포트를 넘기면, 구현이 `"ok"` 키만 보던 시절에는
    `data.get("ok")`가 `None`이라 항상 FAIL — 아래
    `test_ops_check_passes_when_healthcheck_wrapper_schema_is_green` 참고).
    """
    ci_ok, ci_note = _load_ci_report_ok(ci_report)
    guard_ok, guard_note = _load_guard_report_ok(guard_report)
    invariants_ok, invariants_note = _check_invariants(repo_root)
    red_team_ok, red_team_note = _check_red_team_open(repo_root)

    passed = ci_ok and guard_ok and invariants_ok and red_team_ok
    evidence = [ci_note, guard_note, invariants_note, red_team_note]
    detail = "운영 기준 통과" if passed else "; ".join(e for e in evidence if "OK" not in e[:2])
    return CheckResult("10_ops", "운영", passed, tuple(evidence), detail)


def _load_ci_report_ok(path: Path | None) -> tuple[bool, str]:
    """ci_report의 통과 여부 — `"ok"`(`pm/local_ci.py` 원본) 또는 `"passed"`
    (`pm/healthcheck.py` mvp1_gate 배선) 중 실제로 있는 키를 읽는다."""
    if path is None:
        return False, UNVERIFIED
    if not path.is_file():
        return False, f"리포트 없음: {path}"
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError:
        return False, f"리포트 JSON 파싱 실패: {path}"
    if "ok" in data:
        key, value = "ok", data.get("ok")
    elif "passed" in data:
        key, value = "passed", data.get("passed")
    else:
        return False, f"ok/passed 키 없음: {path}"
    ok = bool(value)
    return ok, f"OK {key}=True" if ok else f"{key}={value!r}: {path}"


def _load_guard_report_ok(path: Path | None) -> tuple[bool, str]:
    """guard_report의 통과 여부 — `"vetoed"`(`meta/guards/run_guards.py` 원본,
    False가 통과) 또는 `"veto_count"`(`pm/healthcheck.py` mvp1_gate 배선, 0이
    통과) 중 실제로 있는 키를 읽는다."""
    if path is None:
        return False, UNVERIFIED
    if not path.is_file():
        return False, f"리포트 없음: {path}"
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError:
        return False, f"리포트 JSON 파싱 실패: {path}"
    if "vetoed" in data:
        value = data.get("vetoed")
        ok = not bool(value)
        return ok, "OK vetoed=False" if ok else f"vetoed={value!r} (False여야 함): {path}"
    if "veto_count" in data:
        value = data.get("veto_count")
        ok = isinstance(value, int) and value == 0
        return ok, "OK veto_count=0" if ok else f"veto_count={value!r} (0이어야 함): {path}"
    return False, f"vetoed/veto_count 키 없음: {path}"


def _load_bool_report(
    path: Path | None, key: str, *, expect_zero: bool = False, invert: bool = False
) -> tuple[bool, str]:
    """`key`가 가리키는 값을 읽어 PASS 여부를 판정한다.

    `expect_zero`: 값이 정수 0이어야 PASS(레거시 카운터 스키마용).
    `invert`: 값이 falsy여야 PASS(예: `"vetoed": false` — veto 없음이 통과).
    기본은 값이 truthy여야 PASS.
    """
    if path is None:
        return False, UNVERIFIED
    if not path.is_file():
        return False, f"리포트 없음: {path}"
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError:
        return False, f"리포트 JSON 파싱 실패: {path}"
    value = data.get(key)
    if expect_zero:
        ok = isinstance(value, int) and value == 0
        return ok, f"OK {key}=0" if ok else f"{key}={value!r} (0이어야 함): {path}"
    if invert:
        ok = not bool(value)
        return ok, f"OK {key}=False" if ok else f"{key}={value!r} (False여야 함): {path}"
    ok = bool(value)
    return ok, f"OK {key}=True" if ok else f"{key}={value!r}: {path}"


def _check_invariants(repo_root: Path) -> tuple[bool, str]:
    script = repo_root / "scripts" / "check_audit_regressions.py"
    if not script.is_file():
        return False, "scripts/check_audit_regressions.py 없음"
    baseline = repo_root / "audit-baseline.json"
    try:
        open_findings = json.loads(read_text(baseline)).get("open", {})
    except json.JSONDecodeError:
        open_findings = {}
    result = subprocess.run(  # noqa: S603 - 저장소 내 고정 경로 스크립트, 사용자 입력 없음
        [sys.executable, str(script)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    ok = result.returncode == 0 and not open_findings
    if not ok and result.returncode != 0:
        return False, "check_audit_regressions.py FAIL(신규/미해소 회귀 있음)"
    if open_findings:
        names = ", ".join(open_findings)
        return False, f"audit-baseline.json에 열린 항목 {len(open_findings)}건: {names}"
    return True, "OK INVARIANTS 위반 0"


def _check_red_team_open(repo_root: Path) -> tuple[bool, str]:
    path = repo_root / "docs" / "RED_TEAM_FINDINGS.md"
    text = read_text(path)
    open_count = text.count("⏳ OPEN")
    ok = open_count == 0
    return ok, "OK RED_TEAM 미해결 0" if ok else f"RED_TEAM_FINDINGS.md 미해결(OPEN) {open_count}건"


def _load_ci_step_ok(path: Path | None, step: str) -> tuple[bool, str]:
    """`--ci-report`의 `steps.<step>.ok`를 읽는다(`_load_bool_report`는 top-level
    키만 보므로 별도 헬퍼 -- `pm/local_ci.py` 산출 `pm/ci/latest.json`은
    `{"steps": {"frontend": {"ok": bool, ...}, ...}}` 형태다)."""
    if path is None:
        return False, UNVERIFIED
    if not path.is_file():
        return False, f"리포트 없음: {path}"
    try:
        data = json.loads(read_text(path))
    except json.JSONDecodeError:
        return False, f"리포트 JSON 파싱 실패: {path}"
    steps = data.get("steps")
    step_data = steps.get(step) if isinstance(steps, dict) else None
    if not isinstance(step_data, dict):
        return False, f"steps.{step} 없음: {path}"
    ok = bool(step_data.get("ok"))
    if ok:
        return True, f"OK steps.{step}.ok=True"
    return False, f"steps.{step}.ok={step_data.get('ok')!r}: {path}"


def check_13_user_journeys(repo_root: Path, *, ci_report: Path | None) -> CheckResult:
    """기준13(ADR-2026-09-24-A Decision 4) — J1~J3 Playwright 여정 테스트 존재 +
    `test.fixme` 0건 + `--ci-report`의 `steps.journeys.ok` 녹색
    (pm/local_ci full 모드 J1~J3 단계, task-7145).

    리포트를 넘기지 않으면(다른 항목들과 같은 ADR-D 원칙) "미검증(외부 리포트
    미지정)"으로 FAIL 처리한다 — 모른다=통과 아님.
    """
    present, missing = present_missing(repo_root, *JOURNEY_SPECS)
    fixme_hits = [p for p in present if "test.fixme" in read_text(repo_root / p)]
    # task-7145: pm/local_ci full 모드가 J1~J3만 따로 돌려 steps.journeys에 기록한다 —
    # steps.frontend.ok(lint/build/vitest)는 여정을 실제로 실행했다는 증거가 아니었다.
    journeys_ok, journeys_note = _load_ci_step_ok(ci_report, "journeys")
    passed = not missing and not fixme_hits and journeys_ok
    evidence = [*present, *missing, *(f"FIXME:{p}" for p in fixme_hits), journeys_note]
    parts = []
    if missing:
        parts.append(f"여정 테스트 파일 누락: {', '.join(missing)}")
    if fixme_hits:
        parts.append(f"test.fixme 존재: {', '.join(fixme_hits)}")
    if not journeys_ok:
        parts.append(f"여정 CI 단계(steps.journeys) 미확인/적색: {journeys_note}")
    detail = "J1~J3 여정 테스트 기준 통과" if passed else "; ".join(parts)
    return CheckResult("13_user_journeys", "사용자 여정(J1~J3)", passed, tuple(evidence), detail)
