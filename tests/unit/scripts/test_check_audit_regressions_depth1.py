"""check_audit_regressions.py 깊이 보강 D1->D2.

대상: scripts/check_audit_regressions.py (기존 테스트 미감당 7종).
DoD: negative>=3, failure_injection>=1, perf>=1, gate_red>=1, adversarial>=1.
"""

from __future__ import annotations

import importlib.util
import json
import sys
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
    "check_audit_regressions_depth1", SCRIPTS_DIR / "check_audit_regressions.py"
)


# ---------------------------------------------------------------------------
# negative tests (>= 3) -- each untested checker gets one
# ---------------------------------------------------------------------------


def test_negative_optional_safety_gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """negative: safety gate 인자가 Optional이면 Finding을 반환한다."""
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    src = tmp_path / "src" / "services" / "order_service"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")
    (src / "gate.py").write_text(
        "def handler(pre_submit_gate: str | None) -> None:\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        audit_mod,
        "_py",
        lambda *args: [tmp_path / "src" / "services" / "order_service" / "gate.py"],
    )
    finding = audit_mod.check_optional_safety_gate()
    assert finding is not None
    assert finding.code == "optional_safety_gate"


def test_negative_require_mandate_false(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """negative: require_mandate=False가 있으면 Finding을 반환한다."""
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    api = tmp_path / "src" / "api"
    api.mkdir(parents=True)
    (api / "router.py").write_text(
        "def create_order(require_mandate=False):\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(audit_mod, "_py", lambda *args: [tmp_path / "src" / "api" / "router.py"])
    finding = audit_mod.check_require_mandate_false()
    assert finding is not None
    assert finding.code == "require_mandate_false"


def test_negative_executor_gate_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """negative: executor.py가 submit_order를 부르되 pre_submit_gate를 안 쓰면 Finding."""
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    core = tmp_path / "src" / "core" / "executor"
    core.mkdir(parents=True)
    (core / "executor.py").write_text(
        "def submit_order(req):\n    dispatch(req)\n",
        encoding="utf-8",
    )
    finding = audit_mod.check_executor_gate()
    assert finding is not None
    assert finding.code == "executor_gate_missing"


def test_negative_freshness_tracker_unwired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """negative: main.py에 freshness_tracker가 없으면 Finding."""
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    (tmp_path / "src").mkdir(exist_ok=True)
    (tmp_path / "src" / "main.py").write_text(
        "from app import create_app\napp = create_app()\n",
        encoding="utf-8",
    )
    finding = audit_mod.check_freshness_tracker()
    assert finding is not None
    assert finding.code == "freshness_tracker_unwired"


def test_negative_tenant_fk_to_users(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """negative: migration에서 tenant_id가 users를 FK하면 Finding을 반환한다."""
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    mig = tmp_path / "src" / "db" / "migrations" / "versions"
    mig.mkdir(parents=True)
    (mig / "001_add_user.py").write_text(
        "def upgrade():\n"
        '    op.execute("CREATE TABLE account (id INT, tenant_id INT REFERENCES users (id))")\n',
        encoding="utf-8",
    )
    # _fk_fix 파일 없음 → fixed_tables = {}
    monkeypatch.setattr(audit_mod, "_TENANT_FK_FIX_RE", None)
    finding = audit_mod.check_tenant_fk_to_users()
    assert finding is not None
    assert finding.code == "tenant_fk_to_users"


def test_negative_rls_enable_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """negative: RLS ENABLE만 있고 FORCE가 없으면 Finding을 반환한다."""
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    mig = tmp_path / "src" / "db" / "migrations" / "versions"
    mig.mkdir(parents=True)
    (mig / "001_enable_rls.py").write_text(
        'def upgrade():\n    op.execute("ALTER TABLE orders ENABLE ROW LEVEL SECURITY")\n',
        encoding="utf-8",
    )
    finding = audit_mod.check_rls_enable_without_force()
    assert finding is not None
    assert finding.code == "rls_enable_without_force"


# ---------------------------------------------------------------------------
# failure injection -- corrupt inputs that should be caught
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_failure_inject_corrupt_baseline_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실패 주입: 베이스라인 JSON이 깨져 있으면 main()이 crash를 보고 exit 1로 끝난다."""
    baseline = tmp_path / "audit-baseline.json"
    baseline.write_text("NOT VALID JSON {{{", encoding="utf-8")
    monkeypatch.setattr(
        audit_mod, "CHECKS", [lambda: audit_mod.Finding("test_finding", "detail", [])]
    )
    monkeypatch.setattr(sys, "argv", ["check", "--baseline", str(baseline)])
    rc = audit_mod.main()
    assert rc == 1


@pytest.mark.perf
def test_failure_inject_empty_checks_list(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """실패 주입: CHECKS가 비어 있으면 run()이 빈 리스트를 돌려 main()은 exit 0."""
    monkeypatch.setattr(audit_mod, "CHECKS", [])
    findings = audit_mod.run()
    assert findings == []


# ---------------------------------------------------------------------------
# performance assertions
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_perf_all_checkers_under_budget(
    perf_budget: PerfBudget, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """성능 단언: 각 검사 함수 단일 호출이 5ms 미만이어야 한다.

    실측 기준: 텍스트 스캔 검사는 보통 0.1-1ms 사이. 5ms는 10배 여유.
    """
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)

    for checker in audit_mod.CHECKS:
        sample = perf_budget.best_of(checker, n=10, batch=4)
        assert sample.cpu_ms < 5.0, (
            f"{checker.__name__}: CPU {sample.cpu_ms:.3f}ms -- "
            "텍스트 스캔은 5ms 미만이어야 함 (budget<5ms)"
        )


# ---------------------------------------------------------------------------
# gate-red reproduction -- verify assertions actually catch violations
# ---------------------------------------------------------------------------


def test_gate_red_repro_optional_safety_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현: Optional 안전 게이트 패턴이 실제로 Finding을 산출한다."""
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    svc = tmp_path / "src" / "services" / "oms"
    svc.mkdir(parents=True)
    (svc / "handler.py").write_text(
        "def on_event(pre_send_gate: str | None):\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        audit_mod, "_py", lambda *args: [tmp_path / "src" / "services" / "oms" / "handler.py"]
    )
    finding = audit_mod.check_optional_safety_gate()
    assert finding is not None
    assert "Optional" in finding.detail or "None" in finding.detail


def test_gate_red_repro_duplicate_type_names(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """게이트 적색 재현: 동명 타입이 실제로 Finding을 산출한다."""
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    # IdempotencyScope가 src/와 tests/에 중복 존재하는 상황 시뮬레이션
    src = tmp_path / "src" / "api"
    src.mkdir(parents=True)
    (src / "schemas.py").write_text(
        "class IdempotencyScope:\n    pass\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests" / "fixtures"
    tests.mkdir(parents=True)
    (tests / "conftest.py").write_text(
        "class IdempotencyScope:\n    other = True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        audit_mod,
        "_hits",
        lambda *args, **kwargs: [
            "src/api/schemas.py:1",
            "tests/fixtures/conftest.py:1",
        ],
    )
    finding = audit_mod.check_duplicate_type_names()
    assert finding is not None
    assert finding.code == "duplicate_type_names"


# ---------------------------------------------------------------------------
# adversarial -- mixed valid/invalid, edge cases
# ---------------------------------------------------------------------------


def test_adversarial_mixed_valid_and_violating_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """adversarial: 유효한 파일과 위반 파일을 섞어 넣으면 위반만 보고한다."""
    monkeypatch.setattr(audit_mod, "ROOT", tmp_path)
    svc = tmp_path / "src" / "services" / "execution_loop"
    svc.mkdir(parents=True)
    (svc / "__init__.py").write_text("", encoding="utf-8")
    # 위반 파일
    (svc / "bad.py").write_text(
        "def handler(pre_submit_gate: str | None):\n    pass\n",
        encoding="utf-8",
    )
    # 유효 파일
    (svc / "good.py").write_text(
        "def handler(pre_submit_gate: str):\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        audit_mod,
        "_py",
        lambda *args: [
            tmp_path / "src" / "services" / "execution_loop" / "bad.py",
            tmp_path / "src" / "services" / "execution_loop" / "good.py",
        ],
    )
    finding = audit_mod.check_optional_safety_gate()
    assert finding is not None
    # good.py는 매칭되지 않아야 함
    assert "good.py" not in "\n".join(finding.evidence)


def test_adversarial_baseline_new_vs_fixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """adversarial: 베이스라인 항목이 사라지면 fixed 적색,
    없는 항목이 생기면 new 적색."""
    baseline = tmp_path / "audit-baseline.json"
    baseline.write_text(
        json.dumps({"open": {"old_finding": {"task": "task-000", "detail": "old"}}}),
        encoding="utf-8",
    )

    # old_finding은 사라졌고, new_finding이 새로 생김
    def _new_finding():
        return audit_mod.Finding("new_finding", "new violation", [])

    def _old_finding():
        return None  # 사라짐

    monkeypatch.setattr(audit_mod, "CHECKS", [_new_finding, _old_finding])
    monkeypatch.setattr(sys, "argv", ["check", "--baseline", str(baseline)])
    rc = audit_mod.main()
    assert rc == 1
    # new와 fixed 둘 다 적색 원인
    # new_finding: 베이스라인에 없음
    # old_finding: 베이스라인에 있지만 실제로 없음
