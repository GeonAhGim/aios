"""scripts/closeout_check.py 단위 테스트 — task-6667, 종료조건 13(사용자 여정).

ADR-2026-09-24-A Decision 4: J1~J3 Playwright 여정 테스트 파일 3개 존재 +
`test.fixme` 0건 + `--ci-report`의 `steps.journeys.ok` 녹색. 리포트를 넘기지
않으면 다른 항목들과 같은 ADR-D 원칙("모른다=통과 아님")으로 FAIL 처리한다.
종료조건 1~9는 `test_closeout_check.py`, 10~11은
`test_closeout_check_ops_hardening.py`, 12는
`test_closeout_check_head_actions.py`에 있다. 공용 로더는
`closeout_check_loader.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.scripts.closeout_check_loader import ROOT, _write, cc

_GREEN_REPORT = {"steps": {"journeys": {"ok": True}}}


def _write_all_specs(tmp_path: Path, *, with_fixme: bool = False) -> None:
    body = 'test.fixme("wip", async () => {})\n' if with_fixme else 'test("ok", async () => {})\n'
    for rel in cc.JOURNEY_SPECS:
        _write(tmp_path / rel, body)


def _write_ci_report(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "ci.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- negative


def test_journeys_fail_on_empty_repo_without_ci_report(tmp_path: Path) -> None:
    result = cc.check_13_user_journeys(tmp_path, ci_report=None)

    assert not result.passed
    assert cc.UNVERIFIED in result.evidence
    assert "여정 테스트 파일 누락" in result.detail


def test_journeys_fail_when_a_spec_file_is_missing(tmp_path: Path) -> None:
    for rel in cc.JOURNEY_SPECS[:-1]:
        _write(tmp_path / rel, 'test("ok", async () => {})\n')
    _write_ci_report(tmp_path, _GREEN_REPORT)

    result = cc.check_13_user_journeys(tmp_path, ci_report=tmp_path / "ci.json")

    assert not result.passed
    assert cc.JOURNEY_SPECS[-1] in result.detail


def test_journeys_fail_when_test_fixme_present(tmp_path: Path) -> None:
    """negative: 파일은 3개 다 있어도 `test.fixme`가 남아 있으면 여정이
    실제로는 검증되지 않은 것 -- 존재만으로 통과시키지 않는다."""
    _write_all_specs(tmp_path, with_fixme=True)
    _write_ci_report(tmp_path, _GREEN_REPORT)

    result = cc.check_13_user_journeys(tmp_path, ci_report=tmp_path / "ci.json")

    assert not result.passed
    assert "test.fixme" in result.detail


def test_journeys_fail_when_ci_report_missing_without_report_path(tmp_path: Path) -> None:
    """negative: 여정 파일·fixme 0건이 전부 갖춰져도 --ci-report를 안 넘기면
    ADR-D 원칙대로 "미검증"으로 FAIL이다(다른 항목과 동일)."""
    _write_all_specs(tmp_path)

    result = cc.check_13_user_journeys(tmp_path, ci_report=None)

    assert not result.passed
    assert cc.UNVERIFIED in result.evidence


def test_journeys_fail_when_journeys_step_is_red(tmp_path: Path) -> None:
    _write_all_specs(tmp_path)
    _write_ci_report(tmp_path, {"steps": {"journeys": {"ok": False}}})

    result = cc.check_13_user_journeys(tmp_path, ci_report=tmp_path / "ci.json")

    assert not result.passed
    assert "여정 CI 단계(steps.journeys)" in result.detail


def test_journeys_fail_when_journeys_step_absent_from_report(tmp_path: Path) -> None:
    _write_all_specs(tmp_path)
    _write_ci_report(tmp_path, {"steps": {"ruff": {"ok": True}}})

    result = cc.check_13_user_journeys(tmp_path, ci_report=tmp_path / "ci.json")

    assert not result.passed


# --------------------------------------------------------------------------- failure injection


def test_journeys_fail_when_ci_report_json_is_malformed(tmp_path: Path) -> None:
    """실패 주입: 리포트 파일은 있는데 JSON이 깨졌으면(디스크 쓰기 중단 등)
    "모른다=통과 아님"으로 fail-closed -- 예외를 삼키고 통과시키지 않는다."""
    _write_all_specs(tmp_path)
    path = tmp_path / "ci.json"
    path.write_text("{not-json", encoding="utf-8")

    result = cc.check_13_user_journeys(tmp_path, ci_report=path)

    assert not result.passed
    assert "JSON 파싱 실패" in result.detail or "steps.journeys" in result.detail


# --------------------------------------------------------------------------- positive


def test_journeys_pass_when_specs_present_no_fixme_and_journeys_green(tmp_path: Path) -> None:
    _write_all_specs(tmp_path)
    _write_ci_report(tmp_path, _GREEN_REPORT)

    result = cc.check_13_user_journeys(tmp_path, ci_report=tmp_path / "ci.json")

    assert result.passed
    assert result.detail == "J1~J3 여정 테스트 기준 통과"


# --------------------------------------------------------------------------- real repo


def test_journeys_present_against_real_repo_but_fixme_still_open() -> None:
    """실측 고정: J1~J3 spec 파일 3개는 이미 존재하지만(task-6664/6665/6666
    선행 작업), 각 파일에 `test.fixme`가 아직 남아 있어 --ci-report 없이도
    13항은 현재 FAIL이다 -- 회귀 감지용 스냅샷(다른 항목들과 동일한 관례,
    `test_closeout_check.py`의 "실측 그대로 고정" 원칙)."""
    result = cc.check_13_user_journeys(ROOT, ci_report=None)

    assert not result.passed
    present, missing = cc._present_missing(ROOT, *cc.JOURNEY_SPECS)
    assert not missing
    assert "test.fixme" in result.detail


def test_run_all_includes_13th_item_last(tmp_path: Path, monkeypatch) -> None:
    """`run_all`이 13번 항목을 12번(HEAD Actions) 다음, 표의 13번째 행으로
    덧붙이는지 배선을 확인한다."""
    def _fixed(key: str) -> object:
        return lambda *a, **k: cc.CheckResult(key, f"t{key}", True, (), "")

    monkeypatch.setattr(cc, "CHECKS_1_10", ())
    monkeypatch.setattr(cc, "check_10_ops", _fixed("10"))
    monkeypatch.setattr(cc, "check_11_hardening", _fixed("11"))
    monkeypatch.setattr(cc, "check_12_head_actions_green", _fixed("12"))
    monkeypatch.setattr(
        cc,
        "check_13_user_journeys",
        lambda *a, **k: cc.CheckResult("13_user_journeys", "t13", True, (), ""),
    )

    results = cc.run_all(tmp_path)

    assert len(results) == 4
    assert results[-1].key == "13_user_journeys"
