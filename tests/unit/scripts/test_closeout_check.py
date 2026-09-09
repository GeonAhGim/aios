"""scripts/closeout_check.py 단위 테스트 — task-2738(CLOSEOUT).

DoD: 종료조건 11항 각각에 FAIL 재현 단위 테스트(빈 임시 저장소 = 증빙 없음
= FAIL)와, 가능한 항목은 증빙을 채운 임시 저장소로 PASS도 확인한다.
실제 저장소 대상 호출(`ROOT`)은 이 시점의 실측 상태를 그대로 드러낸다 —
전부 PASS를 강제로 단언하지 않는다(그러면 검사가 무의미해진다), 대신
"이 스크립트가 지금 무엇을 적색으로 보는가"를 회귀 감지용으로 고정한다.
DB·네트워크 접근 없음 — 임시 디렉터리와 로컬 subprocess(자기 저장소의
`check_audit_regressions.py`, DB 미접근)만 쓴다.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc = _load_module("closeout_check_under_test", SCRIPTS_DIR / "closeout_check.py")


def _write(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


TEST_DEF = "def test_x():\n    assert True\n"


# --------------------------------------------------------------------------- 1: 패리티


def test_parity_fails_on_empty_repo(tmp_path: Path) -> None:
    result = cc.check_01_parity(tmp_path)
    assert not result.passed


def test_parity_passes_when_both_harness_files_have_tests(tmp_path: Path) -> None:
    _write(tmp_path / "tests/integration/backtest/test_parity_harness.py", TEST_DEF)
    _write(
        tmp_path / "tests/integration/foundation/backtest/test_vector_event_parity.py", TEST_DEF
    )

    result = cc.check_01_parity(tmp_path)

    assert result.passed


def test_parity_against_real_repo() -> None:
    assert cc.check_01_parity(ROOT).passed


# --------------------------------------------------------------------------- 2: 안전 배선 증명


def test_safety_wiring_fails_on_empty_repo(tmp_path: Path) -> None:
    result = cc.check_02_safety_wiring(tmp_path)
    assert not result.passed
    assert "kill switch" in result.detail or "DataDistrust" in result.detail


def test_safety_wiring_fails_when_gate_arg_is_optional(tmp_path: Path) -> None:
    _write(
        tmp_path / "tests/adversarial/order_service/test_kill_switch_blocks_execution_loop.py",
        TEST_DEF,
    )
    _write(
        tmp_path / "tests/adversarial/risk/test_distrust.py", "# DEGRADED 상태 거부\n" + TEST_DEF
    )
    _write(
        tmp_path / "src/services/order_service/gate.py",
        "def submit(pre_submit_gate: Gate | None = None) -> None: ...\n",
    )

    result = cc.check_02_safety_wiring(tmp_path)

    assert not result.passed
    assert "I-01" in result.detail


def test_safety_wiring_passes_with_all_three_signals(tmp_path: Path) -> None:
    _write(
        tmp_path / "tests/adversarial/order_service/test_kill_switch_blocks_execution_loop.py",
        TEST_DEF,
    )
    _write(
        tmp_path / "tests/adversarial/risk/test_distrust.py", "# DEGRADED 상태 거부\n" + TEST_DEF
    )

    result = cc.check_02_safety_wiring(tmp_path)

    assert result.passed


def test_safety_wiring_against_real_repo() -> None:
    assert cc.check_02_safety_wiring(ROOT).passed


# --------------------------------------------------------------------------- 3: 검증 게이트 실효


def test_validation_gates_fail_on_empty_repo(tmp_path: Path) -> None:
    result = cc.check_03_validation_gates(tmp_path)
    assert not result.passed


def test_validation_gates_pass_when_hard_fail_test_and_wiring_present(tmp_path: Path) -> None:
    _write(tmp_path / "tests/foundation/unit/validation/test_rules.py", TEST_DEF)
    _write(
        tmp_path / "src/foundation/validation/domain/rules.py",
        "from src.foundation.backtest.domain.overfitting import deflated_sharpe, pbo_cscv\n",
    )

    result = cc.check_03_validation_gates(tmp_path)

    assert result.passed


def test_validation_gates_against_real_repo_currently_fails() -> None:
    """rules.py는 아직 overfitting.py를 호출하지 않는다(주석뿐) — 현재 상태를 고정."""
    result = cc.check_03_validation_gates(ROOT)
    assert not result.passed


# --------------------------------------------------------------------------- 4: 전략 언어(DSL)


def test_strategy_language_fails_on_empty_repo(tmp_path: Path) -> None:
    result = cc.check_04_strategy_language(tmp_path)
    assert not result.passed


def test_strategy_language_passes_with_property_cond_v2_and_bench(tmp_path: Path) -> None:
    _write(tmp_path / "tests/unit/core/script/test_interpreter_property.py", TEST_DEF)
    _write(tmp_path / "tests/unit/core/script/test_cond_v2_bridge.py", "# cond-v2\n" + TEST_DEF)
    _write(tmp_path / "docs/perf/dsl_compile_bench.json", "{}")

    result = cc.check_04_strategy_language(tmp_path)

    assert result.passed


def test_strategy_language_against_real_repo_currently_fails_missing_bench() -> None:
    result = cc.check_04_strategy_language(ROOT)
    assert not result.passed
    assert "벤치" in result.detail


# --------------------------------------------------------------------------- 5: 지표 ≥100종


def test_indicators_fail_below_threshold(tmp_path: Path) -> None:
    result = cc.check_05_indicators(tmp_path, count_indicators=lambda _root: 5)
    assert not result.passed
    assert "< 100" in result.detail


def test_indicators_fail_when_registry_import_fails(tmp_path: Path) -> None:
    result = cc.check_05_indicators(tmp_path, count_indicators=lambda _root: None)
    assert not result.passed


def test_indicators_pass_with_count_and_reference_test(tmp_path: Path) -> None:
    _write(tmp_path / "tests/unit/core/indicators/test_engine_equivalence.py", TEST_DEF)

    result = cc.check_05_indicators(tmp_path, count_indicators=lambda _root: 150)

    assert result.passed


def test_indicators_against_real_repo() -> None:
    result = cc.check_05_indicators(ROOT)
    assert result.passed


# --------------------------------------------------------------------------- 6: 백테스트 현실성


def test_backtest_realism_fails_on_empty_repo(tmp_path: Path) -> None:
    result = cc.check_06_backtest_realism(tmp_path)
    assert not result.passed


def test_backtest_realism_passes_with_contract_tests_and_bench(tmp_path: Path) -> None:
    _write(
        tmp_path / "tests/foundation/unit/backtest/test_cost_models.py",
        "# slippage/fee_tier/funding/partial_fill/latency\n" + TEST_DEF,
    )
    _write(tmp_path / "docs/perf/backtest_instant_bench.json", "{}")

    result = cc.check_06_backtest_realism(tmp_path)

    assert result.passed


def test_backtest_realism_against_real_repo_currently_fails_missing_bench() -> None:
    result = cc.check_06_backtest_realism(ROOT)
    assert not result.passed


# --------------------------------------------------------------------------- 7: 실행(OMS)


def test_execution_fails_on_empty_repo(tmp_path: Path) -> None:
    result = cc.check_07_execution(tmp_path)
    assert not result.passed


def test_execution_passes_with_wiring_and_latency_tests(tmp_path: Path) -> None:
    _write(
        tmp_path / "tests/integration/oms/test_cancel_order.py",
        "# cancel/amend/recover/reconcile\n" + TEST_DEF,
    )
    _write(
        tmp_path / "tests/perf/test_pre_trade_latency.py",
        "@pytest.mark.perf\n" + TEST_DEF,
    )

    result = cc.check_07_execution(tmp_path)

    assert result.passed


def test_execution_against_real_repo() -> None:
    assert cc.check_07_execution(ROOT).passed


# --------------------------------------------------------------------------- 8: 데이터


def test_data_fails_on_empty_repo(tmp_path: Path) -> None:
    result = cc.check_08_data(tmp_path)
    assert not result.passed


def test_data_passes_with_bench_coverage_and_spi_tests(tmp_path: Path) -> None:
    _write(tmp_path / "docs/perf/instrument_lookup_bench.json", "{}")
    _write(
        tmp_path / "tests/unit/exchanges/test_coverage.py",
        "# coverage 밖 요청 fail-closed deny\n" + TEST_DEF,
    )
    _write(
        tmp_path / "tests/unit/exchanges/test_kis_contract.py",
        "def test_kis_contract():\n    assert True\n",
    )

    result = cc.check_08_data(tmp_path)

    assert result.passed


def test_data_against_real_repo_currently_fails_missing_bench() -> None:
    result = cc.check_08_data(ROOT)
    assert not result.passed


# --------------------------------------------------------------------------- 9: 차트


def test_chart_fails_on_empty_repo(tmp_path: Path) -> None:
    result = cc.check_09_chart(tmp_path)
    assert not result.passed


def test_chart_passes_with_cross_tenant_test_and_frontend_config(tmp_path: Path) -> None:
    _write(
        tmp_path / "tests/unit/frontend/test_drawing_tenant_isolation.py",
        "# chart drawing tenant 404\n" + TEST_DEF,
    )
    _write(tmp_path / "frontend/vitest.config.ts", "export default {}\n")
    _write(tmp_path / "frontend/package.json", "{}")

    result = cc.check_09_chart(tmp_path)

    assert result.passed


def test_chart_against_real_repo_currently_fails_missing_vitest_config() -> None:
    result = cc.check_09_chart(ROOT)
    assert not result.passed


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


def test_hardening_against_real_repo_currently_fails() -> None:
    """ADR-2026-09-09-B 하드닝은 이 시점에 미완료다 — 현재 적색 상태를 고정."""
    result = cc.check_11_hardening(ROOT)
    assert not result.passed


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


# --------------------------------------------------------------------------- 리포트/CLI


def test_render_markdown_contains_table_and_evidence() -> None:
    results = [cc.CheckResult("01_x", "제목", True, ("evidence-a",), "요약")]

    markdown = cc.render_markdown(results)

    assert "| 1 | 제목 | PASS | 요약 |" in markdown
    assert "evidence-a" in markdown


def test_write_closeout_doc_writes_file(tmp_path: Path) -> None:
    results = [cc.CheckResult("01_x", "제목", True, (), "요약")]
    out = tmp_path / "docs" / "milestones" / "MVP-1_CLOSEOUT.md"

    cc.write_closeout_doc(out, results)

    assert out.is_file()
    assert "MVP-1 종료 확인서" in out.read_text(encoding="utf-8")


def test_main_returns_1_and_skips_write_when_any_check_fails(tmp_path, monkeypatch) -> None:
    fake_results = [
        cc.CheckResult("01_x", "제목1", True, (), "ok"),
        cc.CheckResult("02_y", "제목2", False, (), "적색"),
    ]
    monkeypatch.setattr(cc, "run_all", lambda *a, **k: fake_results)
    out = tmp_path / "CLOSEOUT.md"

    exit_code = cc.main(["--repo-root", str(tmp_path), "--write", str(out)])

    assert exit_code == 1
    assert not out.exists()


def test_main_returns_0_and_writes_when_all_checks_pass(tmp_path, monkeypatch) -> None:
    fake_results = [cc.CheckResult("01_x", "제목1", True, (), "ok")]
    monkeypatch.setattr(cc, "run_all", lambda *a, **k: fake_results)
    out = tmp_path / "CLOSEOUT.md"

    exit_code = cc.main(["--repo-root", str(tmp_path), "--write", str(out)])

    assert exit_code == 0
    assert out.is_file()


def test_main_against_real_repo_currently_returns_1() -> None:
    """MVP-1은 아직 종료조건을 전부 채우지 못했다 — 현재 적색 상태를 고정."""
    assert cc.main(["--repo-root", str(ROOT)]) == 1
