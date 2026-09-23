"""scripts/closeout_check.py 단위 테스트 — task-2738(CLOSEOUT), 종료조건 1~9.

DoD: 종료조건 11항 각각에 FAIL 재현 단위 테스트(빈 임시 저장소 = 증빙 없음
= FAIL)와, 가능한 항목은 증빙을 채운 임시 저장소로 PASS도 확인한다.
실제 저장소 대상 호출(`ROOT`)은 이 시점의 실측 상태를 그대로 드러낸다 —
전부 PASS를 강제로 단언하지 않는다(그러면 검사가 무의미해진다), 대신
"이 스크립트가 지금 무엇을 적색으로 보는가"를 회귀 감지용으로 고정한다.
DB·네트워크 접근 없음 — 임시 디렉터리와 로컬 subprocess(자기 저장소의
`check_audit_regressions.py`, DB 미접근)만 쓴다.

종료조건 10(운영)·11(하드닝)은 `test_closeout_check_ops_hardening.py`,
12(HEAD Actions 녹색)는 `test_closeout_check_head_actions.py`, 리포트/CLI는
`test_closeout_check_report_cli.py`에 있다.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.scripts.closeout_check_loader import ROOT, TEST_DEF, _write, cc

# --------------------------------------------------------------------------- 1: 패리티


def test_parity_fails_on_empty_repo(tmp_path: Path) -> None:
    result = cc.check_01_parity(tmp_path)
    assert not result.passed


def test_parity_passes_when_both_harness_files_have_tests(tmp_path: Path) -> None:
    _write(tmp_path / "tests/integration/backtest/test_parity_harness.py", TEST_DEF)
    _write(
        tmp_path / "tests/integration/foundation/backtest/test_vector_event_parity.py",
        TEST_DEF,
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
        tmp_path / "tests/adversarial/risk/test_distrust.py",
        "# DEGRADED 상태 거부\n" + TEST_DEF,
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
        tmp_path / "tests/adversarial/risk/test_distrust.py",
        "# DEGRADED 상태 거부\n" + TEST_DEF,
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
    _write(
        tmp_path / "tests/unit/core/script/test_cond_v2_bridge.py",
        "# cond-v2\n" + TEST_DEF,
    )
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


def test_chart_against_real_repo_now_passes_with_vitest_config() -> None:
    """task-3460 CI-GREEN-2 (b) added frontend/vitest.config.ts (the vitest 4
    `test.projects` workspace config -- see its own docstring), so this
    criterion's "frontend/vitest.config.ts missing" gap is closed; this test
    used to pin the opposite (missing-config) real-repo state."""
    result = cc.check_09_chart(ROOT)
    assert result.passed
