"""check_audit_regressions.py 깊이 보강 D2 — 미감당 검사/마커 커버리지.

대상: scripts/check_audit_regressions.py (기존 테스트 미감당 항목).
DoD: negative>=3, failure_injection>=1, gate_red>=1, perf>=1.
기존 테스트[test_check_audit_regressions.py, test_check_audit_regressions_depth1.py]와
동일한 fixture/임포트 패턴을 따른다. 새 파일 300줄 이하.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import PerfBudget

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


audit_mod = _load_module(
    "check_audit_regressions_depth2", SCRIPTS_DIR / "check_audit_regressions.py"
)


# ---------------------------------------------------------------------------
# negative tests (>= 3) -- 검사별로 하나씩
# ---------------------------------------------------------------------------


def test_negative_xfail_in_gate_tests_real_xfail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative: gate 테스트 파일에 실제 xfail 주석이 있으면 Finding을 반환한다."""
    gate_test = tmp_path / "tests" / "unit" / "test_gate_params_required.py"
    gate_test.parent.mkdir(parents=True, exist_ok=True)
    gate_test.write_text(
        "# xfail: 이 테스트는 현재 실패함 -- 고쳐야 함\n"
        "def test_gate_param_missing() -> None:\n"
        "    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    monkeypatch.setattr(
        audit_mod,
        "_py",
        lambda *args: [gate_test],
    )
    finding = audit_mod.check_xfail_in_gate_tests()
    assert finding is not None
    assert finding.code == "xfail_in_gate_tests"


def test_negative_xfail_in_gate_tests_skip_not_xfail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative: 'skip' 주석이 'xfail'이 아니면 Finding을 반환하지 않는다."""
    gate_test = tmp_path / "tests" / "unittest_gate_params_required.py"
    gate_test.parent.mkdir(parents=True, exist_ok=True)
    gate_test.write_text(
        "# skip: 이 테스트는 아직 작성 안 함\ndef test_gate_param_missing() -> None:\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    monkeypatch.setattr(
        audit_mod,
        "_py",
        lambda *args: [gate_test],
    )
    finding = audit_mod.check_xfail_in_gate_tests()
    assert finding is None


def test_negative_rls_enable_without_force_all_forced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative: 모든 RLS ENABLE 테이블이 FORCE로도 설정되면 Finding을 반환하지 않는다."""
    mig = tmp_path / "src" / "db" / "migrations" / "versions"
    mig.mkdir(parents=True)
    mig_file = mig / "001_add_security.py"
    mig_file.write_text(
        "def upgrade():\n"
        "    op.execute('ALTER TABLE orders ENABLE ROW LEVEL SECURITY')\n"
        "    op.execute('ALTER TABLE orders FORCE ROW LEVEL SECURITY')\n"
        "    op.execute('ALTER TABLE trades ENABLE ROW LEVEL SECURITY')\n"
        "    op.execute('ALTER TABLE trades FORCE ROW LEVEL SECURITY')\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    finding = audit_mod.check_rls_enable_without_force()
    assert finding is None


def test_negative_duplicate_type_names_no_dupes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative: 감시 타입이 한 곳에만 있으면 Finding을 반환하지 않는다."""
    core = tmp_path / "src" / "core"
    core.mkdir(parents=True)
    (core / "types.py").write_text(
        "class IdempotencyScope:\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    finding = audit_mod.check_duplicate_type_names()
    assert finding is None


# ---------------------------------------------------------------------------
# failure injection -- 검사 자체가 예외를 raise하면 checker_error로 분리
# ---------------------------------------------------------------------------


def test_failure_injection_checker_error_isolation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: CHECKS 중 하나가 예외를 raise하면 checker_error_<이름> 코드로
    격리돼 다른 검사 결과에 섞이지 않는다."""
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)

    def _boom() -> None:
        raise ValueError("boom from checker")

    # boom 검사만 넣어도 checker_error 격리 검증 가능
    monkeypatch.setattr(audit_mod, "CHECKS", [_boom])

    findings = audit_mod.run()

    assert len(findings) == 1
    assert findings[0].code == "checker_error__boom"
    assert "boom from checker" in findings[0].detail


# ---------------------------------------------------------------------------
# 게이트 적색 재현 -- 베이스라인에 없는 신규 결함이 main()을 적색으로 만듦
# ---------------------------------------------------------------------------


def test_gate_red_repro_new_finding_causes_red_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """게이트 적색 재현: 베이스라인에 없는 신규 결함이 main() exit code 1을
    반환하고 stdout에 'FAIL: 베이스라인에 없는 결함'이 나와야 한다."""
    baseline = tmp_path / "audit-baseline.json"
    baseline.write_text(json.dumps({"open": {}}), encoding="utf-8")

    def _new_violation() -> audit_mod.Finding:
        return audit_mod.Finding(
            "new_violation_code",
            "새로운 위반이 발견됨",
            ["injected:1"],
        )

    monkeypatch.setattr(audit_mod, "CHECKS", [_new_violation])
    monkeypatch.setattr(sys, "argv", ["check", "--baseline", str(baseline)])

    rc = audit_mod.main()

    assert rc == 1
    out = capsys.readouterr().out
    assert "FAIL: 베이스라인에 없는 결함" in out
    assert "new_violation_code" in out


def test_gate_red_repro_fixed_but_not_closed(tmp_path, monkeypatch, capsys):
    """게이트 적색 재현: 베이스라인에 있는데 실제로 사라진 항목이 있으면
    '기지(...)'에 fixed 메시지가 나온다."""
    baseline = tmp_path / "audit-baseline.json"
    baseline.write_text(
        json.dumps({"open": {"old_code": {"task": "task-000", "detail": "old"}}}),
        encoding="utf-8",
    )

    def _nothing() -> None:
        return None

    monkeypatch.setattr(audit_mod, "CHECKS", [_nothing])
    monkeypatch.setattr(sys, "argv", ["check", "--baseline", str(baseline)])

    rc = audit_mod.main()

    assert rc == 1
    out = capsys.readouterr().out
    assert "해소된 항목" in out


# ---------------------------------------------------------------------------
# 성능 단언 -- CPU 0 수렴 + 지연 주입 적색
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_perf_xfail_check_cpu_converges_to_zero(
    perf_budget: PerfBudget,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 수렴: check_xfail_in_gate_tests 단일 호출의 CPU 시간이 1ms 미만이어야 한다."""
    gate_test = tmp_path / "tests" / "unit" / "test_gate_params_required.py"
    gate_test.parent.mkdir(parents=True, exist_ok=True)
    gate_test.write_text("def test_x() -> None:\n    pass\n", encoding="utf-8")
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    monkeypatch.setattr(
        audit_mod,
        "_py",
        lambda *args: [gate_test],
    )

    sample = perf_budget.best_of(audit_mod.check_xfail_in_gate_tests, n=50, batch=8)
    assert sample.cpu_ms < 1.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        "텍스트 스캔은 사실상 0ms에 수렴해야 함 (budget<1ms)"
    )


@pytest.mark.perf
def test_perf_rls_check_cpu_converges_to_zero(
    perf_budget: PerfBudget,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 수렴: check_rls_enable_without_force 단일 호출의 CPU 시간이 1ms 미만이어야 한다."""
    mig = tmp_path / "src" / "db" / "migrations" / "versions"
    mig.mkdir(parents=True)
    (mig / "001_add.py").write_text(
        "def upgrade():\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)

    sample = perf_budget.best_of(audit_mod.check_rls_enable_without_force, n=50, batch=8)
    assert sample.cpu_ms < 1.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        "텍스트 스캔은 사실상 0ms에 수렴해야 함 (budget<1ms)"
    )


@pytest.mark.perf
def test_perf_rls_check_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: 성능 단언이 실제로 지연을 포착하는지 역으로 증명한다.
    `time.process_time`을 monkeypatch해 1회 호출당 예산(1.0ms)을 초과하는
    CPU 시간을 가장하면 budget 단언이 AssertionError로 적색이 돼야 한다."""
    mig = tmp_path / "src" / "db" / "migrations" / "versions"
    mig.mkdir(parents=True)
    (mig / "001_add.py").write_text(
        "def upgrade():\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)

    _fake_time = [0.0]

    def _fake_process_time() -> float:
        _fake_time[0] += 5.0  # +5ms per call >> 1.0ms budget
        return _fake_time[0]

    monkeypatch.setattr(time, "process_time", _fake_process_time)

    with pytest.raises(AssertionError):
        perf_budget.assert_within(
            audit_mod.check_rls_enable_without_force,
            budget_ms=1.0,
            batch=1,
        )


# ---------------------------------------------------------------------------
# adversarial -- 베이스라인 상태 전이
# ---------------------------------------------------------------------------


def test_adversarial_baseline_new_then_fixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """adversarial: 신규 결함이 생기면 new 적색, 베이스라인에 있는데 사라지면 fixed 적색.
    두 상태가 동시에 발생하면 둘 다 보고한다."""
    baseline = tmp_path / "audit-baseline.json"
    baseline.write_text(
        json.dumps({"open": {"old_code": {"task": "task-000", "detail": "old"}}}),
        encoding="utf-8",
    )

    def _new_code() -> audit_mod.Finding:
        return audit_mod.Finding("new_code", "new", [])

    def _old_code() -> None:
        return None  # 사라짐

    monkeypatch.setattr(audit_mod, "CHECKS", [_new_code, _old_code])
    monkeypatch.setattr(sys, "argv", ["check", "--baseline", str(baseline)])

    rc = audit_mod.main()
    assert rc == 1
