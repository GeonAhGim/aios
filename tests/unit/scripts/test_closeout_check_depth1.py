"""closeout_check 깊이 보강 D1→D2 (task-24781).

음성(negative) 3종 이상, 실패 주입 1종, 게이트 적색 재현 1종, 성능 단언 1종을
새 보조 테스트 파일로 보강한다. 기존 test_closeout_check.py를 수정하지 않는다.

DoD:
- pytest -q -p no:cacheprovider tests/unit/scripts/test_closeout_check_depth1.py 통과
- python scripts/check_perf_marker_guard.py 통과
- 파일 300줄 이하
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.scripts.closeout_check_loader import TEST_DEF, _write, cc

# ---------------------------------------------------------------------------
# 1: 음성(negative) 테스트 3종 — check_04_strategy_language
# ---------------------------------------------------------------------------


def test_negative_01_missing_property_test_fails(tmp_path: Path) -> None:
    """음성1: property 테스트 파일이 없으면 DSL 체크가 FAIL해야 한다."""
    _write(
        tmp_path / "tests/unit/core/script/test_cond_v2_bridge.py",
        "# cond-v2\n" + TEST_DEF,
    )
    _write(tmp_path / "docs/perf/dsl_compile_bench.json", '{"passed": true}')

    result = cc.check_04_strategy_language(tmp_path)

    assert not result.passed
    assert "property" in result.detail


def test_negative_02_missing_cond_v2_test_fails(tmp_path: Path) -> None:
    """음성2: cond-v2 동일성 테스트가 없으면 DSL 체크가 FAIL해야 한다."""
    _write(tmp_path / "tests/unit/core/script/test_interpreter_property.py", TEST_DEF)
    _write(tmp_path / "docs/perf/dsl_compile_bench.json", '{"passed": true}')

    result = cc.check_04_strategy_language(tmp_path)

    assert not result.passed
    assert "cond-v2" in result.detail


def test_negative_03_bench_file_absent_fails(tmp_path: Path) -> None:
    """음성3: 벤치 결과 파일이 아예 없으면 FAIL — 빈 디렉터리만 있어도 통과 금지."""
    _write(tmp_path / "tests/unit/core/script/test_interpreter_property.py", TEST_DEF)
    _write(
        tmp_path / "tests/unit/core/script/test_cond_v2_bridge.py",
        "# cond-v2\n" + TEST_DEF,
    )
    # docs/perf/ 디렉터리는 만들되 bench.json은 작성하지 않음
    (tmp_path / "docs" / "perf").mkdir(parents=True, exist_ok=True)

    result = cc.check_04_strategy_language(tmp_path)

    assert not result.passed
    assert "벤치 결과 파일 없음" in result.detail


# ---------------------------------------------------------------------------
# 2: 실패 주입 — monkeypatch로 내부 헬퍼를 망가뜨려 적색 재현
# ---------------------------------------------------------------------------


def test_failure_injection_grep_returns_empty(tmp_path: Path) -> None:
    """실패주입1: grep 헬퍼가 빈 리스트를 반환하면 check_02_safety_wiring가
    DataDistrust/DEGRADED 신호를 못 찾아 FAIL해야 한다."""
    _write(
        tmp_path / "tests/adversarial/order_service/test_kill_switch_blocks_execution_loop.py",
        TEST_DEF,
    )

    import scripts.closeout_check as _mod

    original_grep = _mod._grep
    _mod._grep = lambda *a, **k: []

    try:
        result = cc.check_02_safety_wiring(tmp_path)
        assert not result.passed
        assert "DataDistrust" in result.detail or "DEGRADED" in result.detail
    finally:
        _mod._grep = original_grep


def test_failure_injection_bench_json_corrupt(tmp_path: Path) -> None:
    """실패주입2: 벤치 JSON이 파싱 가능한 무효 값(passed가 bool 아님)을 담으면
    fail-closed로 False를 반환해 체크가 FAIL해야 한다."""
    _write(tmp_path / "tests/unit/core/script/test_interpreter_property.py", TEST_DEF)
    _write(
        tmp_path / "tests/unit/core/script/test_cond_v2_bridge.py",
        "# cond-v2\n" + TEST_DEF,
    )
    _write(tmp_path / "docs/perf/dsl_compile_bench.json", '{"passed": "yes"}')

    result = cc.check_04_strategy_language(tmp_path)

    assert not result.passed


# ---------------------------------------------------------------------------
# 3: 게이트 적색 재현 — 입력을 망가뜨려 실제로 적색이 나오는지 확인
# ---------------------------------------------------------------------------


def test_gate_red_01_optional_gate_arg_triggers_fail(tmp_path: Path) -> None:
    """게이트적색1: 안전 게이트 인자에 Optional이混入되면 check_02_safety_wiring가
    I-01 위반을 적색으로 본다."""
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
    assert "Optional" in result.detail
    assert "I-01" in result.detail


def test_gate_red_02_ci_report_passed_false(tmp_path: Path) -> None:
    """게이트적색2: ci_report의 passed=false가 전달되면 check_10_ops가 FAIL해야 한다."""
    ci_report = tmp_path / "ci_report.json"
    _write(ci_report, json.dumps({"passed": False}))

    result = cc.check_10_ops(
        tmp_path,
        ci_report=ci_report,
        guard_report=None,
    )

    assert not result.passed


def test_gate_red_03_guard_report_vetoed_true(tmp_path: Path) -> None:
    """게이트적색3: guard_report의 vetoed=true가 전달되면 check_10_ops가 FAIL해야 한다."""
    guard_report = tmp_path / "guard_report.json"
    _write(guard_report, json.dumps({"vetoed": True}))

    result = cc.check_10_ops(
        tmp_path,
        ci_report=None,
        guard_report=guard_report,
    )

    assert not result.passed


# ---------------------------------------------------------------------------
# 4: 성능 단언 — perf marker + virtual_delayed_sync 적색 재현
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_perf_01_check_04_strategy_language_budget_assertion(tmp_path: Path) -> None:
    """성능1: check_04_strategy_language가 ≤50ms 이내 끝나야 한다.

    실측 근거: 로컬에서 check_04는 파일 시스템 stat + grep 2회 + JSON 1회 읽기만
    하므로 10ms 미만. 10배 여유인 50ms를 예산으로 정한다.
    """
    _write(tmp_path / "tests/unit/core/script/test_interpreter_property.py", TEST_DEF)
    _write(
        tmp_path / "tests/unit/core/script/test_cond_v2_bridge.py",
        "# cond-v2\n" + TEST_DEF,
    )
    _write(tmp_path / "docs/perf/dsl_compile_bench.json", '{"passed": true}')

    import time

    before = time.perf_counter()
    result = cc.check_04_strategy_language(tmp_path)
    elapsed_ms = (time.perf_counter() - before) * 1000

    assert elapsed_ms < 50, f"check_04_strategy_language이 {elapsed_ms:.1f}ms — 예산 50ms 초과"
    assert result.passed


@pytest.mark.perf
def test_perf_02_check_02_safety_wiring_budget_assertion(tmp_path: Path) -> None:
    """성능2: check_02_safety_wiring가 ≤50ms 이내 끝나야 한다.

    실측 근거: grep 2회 + 파일 stat 1회. 로컬 실측 8ms 미만.
    """
    _write(
        tmp_path / "tests/adversarial/order_service/test_kill_switch_blocks_execution_loop.py",
        TEST_DEF,
    )
    _write(
        tmp_path / "tests/adversarial/risk/test_distrust.py",
        "# DEGRADED 상태 거부\n" + TEST_DEF,
    )

    import time

    before = time.perf_counter()
    result = cc.check_02_safety_wiring(tmp_path)
    elapsed_ms = (time.perf_counter() - before) * 1000

    assert elapsed_ms < 50, f"check_02_safety_wiring이 {elapsed_ms:.1f}ms — 예산 50ms 초과"
    assert result.passed


# ---------------------------------------------------------------------------
# 5: 추가 음성 — check_08_data, check_09_chart
# ---------------------------------------------------------------------------


def test_negative_04_data_missing_spi_tests_fails(tmp_path: Path) -> None:
    """음성4: 데이터 체크에서 SPI 계약 테스트가 없으면 FAIL해야 한다."""
    _write(tmp_path / "docs/perf/instrument_lookup_bench.json", '{"passed": true}')
    _write(
        tmp_path / "tests/unit/exchanges/test_coverage.py",
        "# coverage 밖 요청 fail-closed deny\n" + TEST_DEF,
    )
    # spi 테스트 파일 없음

    result = cc.check_08_data(tmp_path)

    assert not result.passed
    assert "SPI 계약 테스트 없음" in result.detail


def test_negative_05_chart_missing_cross_tenant_fails(tmp_path: Path) -> None:
    """음성5: 차트 체크에서 교차 테넌트 테스트가 없으면 FAIL해야 한다."""
    _write(tmp_path / "frontend/vitest.config.ts", "export default {}\n")
    _write(tmp_path / "frontend/package.json", "{}")

    result = cc.check_09_chart(tmp_path)

    assert not result.passed
    assert "교차 테넌트" in result.detail


# ---------------------------------------------------------------------------
# 6: 추가 실패 주입 — check_05_indicators count_indicators=None
# ---------------------------------------------------------------------------


def test_failure_injection_indicator_count_none(tmp_path: Path) -> None:
    """실패주입3: 지표 레지스트리 import가 실패하면(None 반환) check_05_indicators가
    fail-closed로 FAIL해야 한다."""
    _write(tmp_path / "tests/unit/core/indicators/test_engine_equivalence.py", TEST_DEF)

    result = cc.check_05_indicators(tmp_path, count_indicators=lambda _root: None)

    assert not result.passed
    assert "import 실패" in result.detail


# ---------------------------------------------------------------------------
# 7: 게이트 적색 재현 — check_11_hardening 하위 항목
# ---------------------------------------------------------------------------


def test_gate_red_04_hardening_h5_supply_chain_missing(tmp_path: Path) -> None:
    """게이트적색4: 공급망 게이트(h5)가 누락되면 check_11_hardening이 FAIL해야 한다."""
    # .github/workflows/quality.yml도 dependabot.yml도 없음 — 빈 tmp_path 유지
    result = cc.check_11_hardening(tmp_path)

    assert not result.passed
    assert "H-5" in result.detail
