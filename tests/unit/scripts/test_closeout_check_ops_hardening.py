"""scripts/closeout_check.py 단위 테스트 — task-2738(CLOSEOUT), 종료조건 10~11.

10(운영)의 리포트 로딩 헬퍼(`_load_bool_report`)·불변식/red-team 서브체크와,
11(하드닝 집계 로직 + 개별 H1~H13 항목)을 다룬다. 종료조건 1~9는
`test_closeout_check.py`, 12(HEAD Actions 녹색)는
`test_closeout_check_head_actions.py`, 리포트/CLI는
`test_closeout_check_report_cli.py`에 있다. 공용 로더는
`closeout_check_loader.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

from tests.unit.scripts.closeout_check_loader import ROOT, TEST_DEF, _write, cc

# --------------------------------------------------------------------------- 10: 운영


def test_load_bool_report_unverified_when_path_is_none() -> None:
    ok, note = cc._load_bool_report(None, "passed")
    assert not ok
    assert note == cc.UNVERIFIED


def test_load_bool_report_fails_when_file_missing(tmp_path: Path) -> None:
    ok, _note = cc._load_bool_report(tmp_path / "does-not-exist.json", "passed")
    assert not ok


def test_load_bool_report_true(tmp_path: Path) -> None:
    path = tmp_path / "ci.json"
    path.write_text(json.dumps({"passed": True}), encoding="utf-8")

    ok, note = cc._load_bool_report(path, "passed")

    assert ok
    assert "OK" in note


def test_load_bool_report_expect_zero_fails_when_nonzero(tmp_path: Path) -> None:
    path = tmp_path / "guard.json"
    path.write_text(json.dumps({"veto_count": 3}), encoding="utf-8")

    ok, note = cc._load_bool_report(path, "veto_count", expect_zero=True)

    assert not ok
    assert "3" in note


def test_check_invariants_fails_when_baseline_has_open_findings(tmp_path: Path) -> None:
    _write(
        tmp_path / "scripts/check_audit_regressions.py",
        "import sys\nsys.exit(0)\n",
    )
    _write(
        tmp_path / "audit-baseline.json",
        json.dumps({"open": {"require_mandate_false": {"task": 1750}}}),
    )

    ok, note = cc._check_invariants(tmp_path)

    assert not ok
    assert "require_mandate_false" in note


def test_check_invariants_passes_when_script_exits_zero_and_baseline_empty(tmp_path: Path) -> None:
    _write(tmp_path / "scripts/check_audit_regressions.py", "import sys\nsys.exit(0)\n")
    _write(tmp_path / "audit-baseline.json", json.dumps({"open": {}}))

    ok, note = cc._check_invariants(tmp_path)

    assert ok
    assert "OK" in note


def test_check_invariants_fails_when_script_missing(tmp_path: Path) -> None:
    ok, note = cc._check_invariants(tmp_path)
    assert not ok
    assert "없음" in note


def test_check_red_team_open_fails_when_open_marker_present(tmp_path: Path) -> None:
    _write(tmp_path / "docs/RED_TEAM_FINDINGS.md", "## finding\n\n**상태**: ⏳ OPEN\n")

    ok, note = cc._check_red_team_open(tmp_path)

    assert not ok
    assert "1건" in note


def test_check_red_team_open_passes_when_no_open_marker(tmp_path: Path) -> None:
    _write(tmp_path / "docs/RED_TEAM_FINDINGS.md", "## finding\n\n**상태**: ✅ FIXED\n")

    ok, _note = cc._check_red_team_open(tmp_path)

    assert ok


def test_ops_check_fails_on_empty_repo_without_external_reports(tmp_path: Path) -> None:
    result = cc.check_10_ops(tmp_path, ci_report=None, guard_report=None)
    assert not result.passed
    assert cc.UNVERIFIED in result.evidence


def test_ops_check_passes_when_everything_supplied_and_green(tmp_path: Path) -> None:
    _write(tmp_path / "scripts/check_audit_regressions.py", "import sys\nsys.exit(0)\n")
    _write(tmp_path / "audit-baseline.json", json.dumps({"open": {}}))
    _write(tmp_path / "docs/RED_TEAM_FINDINGS.md", "no open findings")
    ci_report = tmp_path / "ci.json"
    ci_report.write_text(json.dumps({"passed": True}), encoding="utf-8")
    guard_report = tmp_path / "guard.json"
    guard_report.write_text(json.dumps({"veto_count": 0}), encoding="utf-8")

    result = cc.check_10_ops(tmp_path, ci_report=ci_report, guard_report=guard_report)

    assert result.passed


# --------------------------------------------------------------------------- 11: 하드닝 집계 로직


def _fake_item(item_id: str, passed: bool) -> cc.HardeningItem:
    return cc.HardeningItem(item_id, f"{item_id} 라벨", lambda _root, p=passed: (p, "고정 결과"))


def test_hardening_fails_when_any_item_fails(tmp_path: Path) -> None:
    items = (_fake_item("H-1", True), _fake_item("H-2", False))

    result = cc.check_11_hardening(tmp_path, items=items)

    assert not result.passed
    assert "H-2" in result.detail
    assert "H-1" not in result.detail


def test_hardening_passes_when_all_items_pass(tmp_path: Path) -> None:
    items = (_fake_item("H-1", True), _fake_item("H-2", True))

    result = cc.check_11_hardening(tmp_path, items=items)

    assert result.passed
    assert "전부 닫힘" in result.detail


def test_hardening_against_real_repo_now_passes() -> None:
    """ADR-2026-09-09-B 하드닝 H-1~H-13 전부 닫힘(task-6398, CTO 2026-09-24 확인).

    H-2/H-3/H-4/H-6/H-8/H-10은 검사의 정적 경로/패턴이 실제 배치와
    어긋나 있던 오탐이었다 — 증거 자체는 이미 존재했다(task-6388 재배정).
    """
    result = cc.check_11_hardening(ROOT)
    assert result.passed


# --------------------------------------------------------------------------- 11 개별 H 항목


def test_h1_fails_when_require_mandate_false_present(tmp_path: Path) -> None:
    _write(tmp_path / "src/api/wiring.py", "require_mandate = False\n")

    ok, _note = cc._h1_mandate_required(tmp_path)

    assert not ok


def test_h1_passes_when_no_hits(tmp_path: Path) -> None:
    ok, _note = cc._h1_mandate_required(tmp_path)
    assert ok


def test_h2_fails_when_cm_files_missing(tmp_path: Path) -> None:
    ok, note = cc._h2_cm_reporting_and_api(tmp_path)
    assert not ok
    assert "trade_report.py" in note


def test_h3_fails_when_notimplemented_present(tmp_path: Path) -> None:
    _write(tmp_path / "src/exchanges/nh/trading_mixin.py", "raise NotImplementedError\n")

    ok, _note = cc._h3_nh_no_notimplemented(tmp_path)

    assert not ok


def test_h3_passes_when_absent(tmp_path: Path) -> None:
    ok, _note = cc._h3_nh_no_notimplemented(tmp_path)
    assert ok


def test_h4_fails_when_backup_scripts_missing(tmp_path: Path) -> None:
    ok, _note = cc._h4_backup_scripts(tmp_path)
    assert not ok


def test_h5_fails_when_ci_workflow_missing(tmp_path: Path) -> None:
    ok, _note = cc._h5_supply_chain_gate(tmp_path)
    assert not ok


def test_h5_passes_when_gate_wired(tmp_path: Path) -> None:
    _write(
        tmp_path / ".github/workflows/quality.yml",
        "run_pip_audit\nnpm audit --audit-level=high\n",
    )
    _write(tmp_path / ".github/dependabot.yml", "version: 2\n")

    ok, _note = cc._h5_supply_chain_gate(tmp_path)

    assert ok


def test_h6_fails_when_no_dockerfiles(tmp_path: Path) -> None:
    ok, _note = cc._h6_dockerfiles(tmp_path)
    assert not ok


def test_h7_fails_when_no_e2e_or_playwright(tmp_path: Path) -> None:
    ok, _note = cc._h7_e2e_suites(tmp_path)
    assert not ok


def test_h8_fails_when_no_property_tests(tmp_path: Path) -> None:
    ok, _note = cc._h8_property_tests(tmp_path)
    assert not ok


def test_h8_passes_with_hypothesis_test(tmp_path: Path) -> None:
    _write(tmp_path / "tests/unit/core/risk/test_limit_monotonicity.py", "hypothesis\n" + TEST_DEF)

    ok, _note = cc._h8_property_tests(tmp_path)

    assert ok


def test_h9_fails_when_test_file_missing(tmp_path: Path) -> None:
    ok, _note = cc._h9_dsr_pbo_regression(tmp_path)
    assert not ok


def test_h9_passes_with_paper_numbers(tmp_path: Path) -> None:
    _write(
        tmp_path / "tests/foundation/unit/backtest/test_overfitting.py",
        "# DSR~=0.9004 and 0.9505\n" + TEST_DEF,
    )

    ok, _note = cc._h9_dsr_pbo_regression(tmp_path)

    assert ok


def test_h10_fails_when_no_webhook_routing(tmp_path: Path) -> None:
    _write(tmp_path / "config/observability/alert_rules.yaml", "groups: []\n")

    ok, _note = cc._h10_alert_routing(tmp_path)

    assert not ok


def test_h11_fails_when_no_cache_or_race_test(tmp_path: Path) -> None:
    ok, _note = cc._h11_mandate_cache_invalidation(tmp_path)
    assert not ok


def test_h12_fails_when_placeholder_cast_present(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/foundation/ledger/adapters/legacy_wallet_bridge.py",
        "_UNUSED_POOL = cast(asyncpg.Pool, None)\n",
    )

    ok, _note = cc._h12_legacy_wallet_bridge_sentinel(tmp_path)

    assert not ok


def test_h12_passes_when_placeholder_removed(tmp_path: Path) -> None:
    _write(
        tmp_path / "src/foundation/ledger/adapters/legacy_wallet_bridge.py",
        "pool: asyncpg.Pool = injected_pool\n",
    )

    ok, _note = cc._h12_legacy_wallet_bridge_sentinel(tmp_path)

    assert ok


def test_h13_fails_when_env_configs_missing(tmp_path: Path) -> None:
    ok, _note = cc._h13_env_config(tmp_path)
    assert not ok


def test_h13_passes_when_all_present(tmp_path: Path) -> None:
    for name in ("dev.yaml", "staging.yaml", "live.yaml"):
        _write(tmp_path / "config" / name, "env: {}\n")

    ok, _note = cc._h13_env_config(tmp_path)

    assert ok
