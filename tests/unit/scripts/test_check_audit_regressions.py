"""scripts/check_audit_regressions.py 단위 테스트 — FA-15a(esc-2115).

DoD: "새 정적 검사가 raw 시드 픽스처를 주입하면 실패함을 증명" — 실제
저장소를 건드리지 않고 임시 디렉터리에 위반 패턴을 주입해
`check_ledger_balance_raw_seed`가 그것을 찾아내는지, 그리고
`# audit-allow` 주석이 있는 문서화된 예외는 봐주는지를 직접 실행해
확인한다. DB·네트워크 접근 없음 — 순수 텍스트 스캔이라 임시 파일만 쓴다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


check_audit_regressions = _load_module(
    "check_audit_regressions_under_test", SCRIPTS_DIR / "check_audit_regressions.py"
)


def _write_injected_fixture(tmp_path: Path, body: str) -> None:
    tests_dir = tmp_path / "tests" / "integration" / "foundation" / "ledger"
    tests_dir.mkdir(parents=True)
    (tests_dir / "conftest.py").write_text(body, encoding="utf-8")


def test_flags_raw_balance_seed_injected_into_a_fixture(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(check_audit_regressions, "ROOT", tmp_path)
    _write_injected_fixture(
        tmp_path,
        "async def create_ledger_account(pool, *, initial_balance):\n"
        "    async with pool.acquire() as conn:\n"
        "        await conn.execute(\n"
        '            "INSERT INTO ledger_balance (account_id, balance, allow_negative) "\n'
        '            "VALUES ($1, $2, $3)",\n'
        "            account_id, initial_balance, allow_negative,\n"
        "        )\n",
    )

    finding = check_audit_regressions.check_ledger_balance_raw_seed()

    assert finding is not None
    assert finding.code == "ledger_balance_raw_seed"
    assert any("conftest.py" in e for e in finding.evidence)


def test_allows_raw_balance_seed_with_documented_exception_marker(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(check_audit_regressions, "ROOT", tmp_path)
    _write_injected_fixture(
        tmp_path,
        "async def _bump_balance(delta):\n"
        "    async with pool.acquire() as conn:\n"
        "        # audit-allow: ledger_balance_raw_seed -- adversarial tamper, 시드 아님\n"
        "        await conn.execute(\n"
        '            "INSERT INTO ledger_balance (account_id, balance, allow_negative) "\n'
        '            "VALUES ($1, $2, $3)",\n'
        "            account_id, new_balance, allow_negative,\n"
        "        )\n",
    )

    finding = check_audit_regressions.check_ledger_balance_raw_seed()

    assert finding is None


def test_allows_balance_free_provisioning_insert(tmp_path, monkeypatch) -> None:
    """운영 코드와 동일한 관례(balance 컬럼을 아예 나열하지 않음)는 통과한다."""
    monkeypatch.setattr(check_audit_regressions, "ROOT", tmp_path)
    _write_injected_fixture(
        tmp_path,
        "async def _ensure_account(conn, code):\n"
        "    await conn.execute(\n"
        '        "INSERT INTO ledger_balance (account_id, allow_negative) "\n'
        '        "SELECT account_id, $2 FROM ledger_account WHERE account_code = $1 "\n'
        '        "ON CONFLICT (account_id) DO NOTHING",\n'
        "        code, negative_ok,\n"
        "    )\n",
    )

    finding = check_audit_regressions.check_ledger_balance_raw_seed()

    assert finding is None


def test_current_repository_has_no_open_ledger_balance_raw_seed_findings() -> None:
    """실제 저장소는 FA-15a 정리 이후 이 결함이 완전히 닫혀 있어야 한다 —
    회귀가 들어오면 이 테스트가 바로 잡는다."""
    finding = check_audit_regressions.check_ledger_balance_raw_seed()

    assert finding is None


def test_flags_raw_balance_seed_with_column_list_on_continuation_line(
    tmp_path, monkeypatch
) -> None:
    """컬럼 목록이 문자열 리터럴 이어붙이기로 다음 줄에 걸쳐 있어도(체크 docstring이
    설명하는 실제 관례) `balance` 컬럼을 놓치지 않고 잡아야 한다 — `INSERT INTO`가
    있는 줄 자체에는 `balance`가 없는 경계 사례."""
    monkeypatch.setattr(check_audit_regressions, "ROOT", tmp_path)
    _write_injected_fixture(
        tmp_path,
        "async def _seed(conn, account_id, initial_balance, allow_negative):\n"
        "    await conn.execute(\n"
        '        "INSERT INTO ledger_balance "\n'
        '        "(account_id, balance, allow_negative) "\n'
        '        "VALUES ($1, $2, $3)",\n'
        "        account_id, initial_balance, allow_negative,\n"
        "    )\n",
    )

    finding = check_audit_regressions.check_ledger_balance_raw_seed()

    assert finding is not None
    assert finding.code == "ledger_balance_raw_seed"


def test_flags_raw_balance_seed_when_allow_marker_is_outside_context_window(
    tmp_path, monkeypatch
) -> None:
    """`# audit-allow` 주석이 12줄 컨텍스트 창보다 더 위에 있으면 예외로 인정되지
    않는다 — 화이트박스 주석을 파일 어딘가에 던져두고 무관한 위반을 가리는
    회피를 막는 경계 사례."""
    monkeypatch.setattr(check_audit_regressions, "ROOT", tmp_path)
    padding = "\n".join(f"    # padding {i}" for i in range(15))
    _write_injected_fixture(
        tmp_path,
        "async def _seed(conn, account_id, initial_balance, allow_negative):\n"
        "    # audit-allow: ledger_balance_raw_seed -- 너무 멀리 있어 무효해야 한다\n"
        f"{padding}\n"
        "    await conn.execute(\n"
        '        "INSERT INTO ledger_balance (account_id, balance, allow_negative) "\n'
        '        "VALUES ($1, $2, $3)",\n'
        "        account_id, initial_balance, allow_negative,\n"
        "    )\n",
    )

    finding = check_audit_regressions.check_ledger_balance_raw_seed()

    assert finding is not None
    assert finding.code == "ledger_balance_raw_seed"


def _write_class_fixture(tmp_path: Path, rel_path: str, body: str) -> None:
    file_path = tmp_path / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(body, encoding="utf-8")


def test_flags_duplicate_idempotency_scope_class_names_injected_into_src(
    tmp_path, monkeypatch
) -> None:
    """게이트 적색 재현(task-3189, PLT-45) — task-1815 개명(f6986bbb) 이전
    실제 상태(`src/api/contracts/idempotency.py`와
    `src/services/oms/contracts/v1_commands.py`가 똑같이
    `class IdempotencyScope`를 정의)를 임시 트리에 재현해
    `check_duplicate_type_names`가 실제로 잡아내는지 확인한다 — tautology가
    아님을 증명한다."""
    monkeypatch.setattr(check_audit_regressions, "ROOT", tmp_path)
    _write_class_fixture(
        tmp_path,
        "src/api/contracts/idempotency.py",
        "class IdempotencyScope:\n    pass\n",
    )
    _write_class_fixture(
        tmp_path,
        "src/services/oms/contracts/v1_commands.py",
        "class IdempotencyScope:\n    pass\n",
    )

    finding = check_audit_regressions.check_duplicate_type_names()

    assert finding is not None
    assert finding.code == "duplicate_type_names"
    assert any("idempotency.py" in e for e in finding.evidence)
    assert any("v1_commands.py" in e for e in finding.evidence)


def test_current_repository_has_zero_duplicate_idempotency_scope_type_names() -> None:
    """task-1815 개명(f6986bbb, `IdempotencyScope -> OrderIdempotencyScope`)
    이후 실제 저장소는 이 결함이 닫혀 있어야 한다 — task-3189 DoD
    "check_audit_regressions의 duplicate_type_names가 0건"의 직접 증거.
    회귀(OMS 쪽에 `class IdempotencyScope`가 다시 생김)가 들어오면 이
    테스트가 바로 잡는다."""
    finding = check_audit_regressions.check_duplicate_type_names()

    assert finding is None


@pytest.mark.perf
def test_check_ledger_balance_raw_seed_completes_within_time_budget(tmp_path, monkeypatch) -> None:
    """성능단언: 대규모 저장소(수천 개 테스트 파일)에서도 raw seed 스캔이 예산
    내에 끝나는지, 그리고 그 규모 속에서도 유일한 위반을 정확히 찾는지 확인한다."""
    monkeypatch.setattr(check_audit_regressions, "ROOT", tmp_path)
    generated_dir = tmp_path / "tests" / "generated"
    for i in range(500):
        sub = generated_dir / f"pkg_{i % 50}"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / f"test_{i}.py").write_text(
            'async def test_noop(conn):\n    await conn.execute("SELECT 1")\n',
            encoding="utf-8",
        )
    _write_injected_fixture(
        tmp_path,
        "async def create_ledger_account(pool, *, initial_balance):\n"
        "    async with pool.acquire() as conn:\n"
        "        await conn.execute(\n"
        '            "INSERT INTO ledger_balance (account_id, balance, allow_negative) "\n'
        '            "VALUES ($1, $2, $3)",\n'
        "            account_id, initial_balance, allow_negative,\n"
        "        )\n",
    )
    assert (
        len(list(generated_dir.rglob("*.py"))) > 300
    )  # 벤치마크가 무의미해지지 않도록 규모를 보장

    start = time.perf_counter()
    finding = check_audit_regressions.check_ledger_balance_raw_seed()
    elapsed = time.perf_counter() - start

    assert finding is not None
    assert any("conftest.py" in e for e in finding.evidence)
    assert elapsed < 5.0, f"raw seed 스캔이 {elapsed:.3f}s — 예산(5.0s) 초과"


def _marker_check():
    return check_audit_regressions.Finding("marker_ok", "정상 검사 통과 표식", ["marker:1"])


def _raising_check():
    raise ValueError("boom-injected")


def test_run_isolates_a_raising_checker_and_keeps_running_the_rest(monkeypatch) -> None:
    """실패주입: task-1923에서 해소한 격리 성질(검사 하나가 죽어도 나머지가
    돈다)을 직접 검증한다. 예외가 새는 순간 뒤 검사 전부가 조용히 스킵되는
    회귀를 잡기 위해 실패 검사 앞뒤에 정상 검사를 배치한다."""
    monkeypatch.setattr(
        check_audit_regressions,
        "CHECKS",
        [_marker_check, _raising_check, _marker_check],
    )

    findings = check_audit_regressions.run()

    codes = [f.code for f in findings]
    assert codes.count("marker_ok") == 2  # 실패한 검사 앞뒤 둘 다 실행됨
    error_findings = [f for f in findings if f.code.startswith("checker_error_")]
    assert len(error_findings) == 1
    assert error_findings[0].code == "checker_error__raising_check"
    assert "boom-injected" in error_findings[0].detail


def test_run_reports_each_failing_checker_under_its_own_isolated_code(monkeypatch) -> None:
    """네거티브: 서로 다른 두 검사가 각각 다른 예외로 죽어도 서로의 Finding을
    덮어쓰거나 뒤섞지 않고 독립된 checker_error_<이름> 코드로 분리돼야 한다."""

    def _raise_a():
        raise RuntimeError("A-fail")

    def _raise_b():
        raise KeyError("B-fail")

    monkeypatch.setattr(check_audit_regressions, "CHECKS", [_raise_a, _raise_b])

    findings = check_audit_regressions.run()

    by_code = {f.code: f for f in findings}
    assert set(by_code) == {"checker_error__raise_a", "checker_error__raise_b"}
    assert "A-fail" in by_code["checker_error__raise_a"].detail
    assert "B-fail" in by_code["checker_error__raise_b"].detail


def test_main_exits_red_when_a_checker_crash_is_a_new_unbaselined_finding(
    tmp_path, monkeypatch, capsys
) -> None:
    """게이트 적색 재현: local_ci가 실제로 참조하는 rc 계약 그대로, 베이스라인에
    없는 checker_error_* 가 나타나면 main()이 rc=1로 끝나고 에스컬레이션 본문과
    대조 가능한 고정 문구("FAIL: 베이스라인에 없는 결함")를 그대로 찍어야 한다."""
    baseline = tmp_path / "audit-baseline.json"
    baseline.write_text(json.dumps({"open": {}}), encoding="utf-8")
    monkeypatch.setattr(check_audit_regressions, "CHECKS", [_raising_check])
    monkeypatch.setattr(sys, "argv", ["check_audit_regressions.py", "--baseline", str(baseline)])

    rc = check_audit_regressions.main()
    out = capsys.readouterr().out

    assert rc == 1
    assert "FAIL: 베이스라인에 없는 결함" in out
    assert "checker_error__raising_check" in out


@pytest.mark.perf
def test_run_completes_within_time_budget_when_many_checkers_fail(monkeypatch) -> None:
    """성능단언: 격리 로직(try/except)이 검사 수백 개 규모에서도 예외 처리
    오버헤드로 예산을 넘기지 않는지 확인한다."""
    checks = []
    for i in range(300):
        if i % 2 == 0:

            def _c(i: int = i):
                raise ValueError(f"fail-{i}")
        else:

            def _c(i: int = i):
                return None

        checks.append(_c)
    monkeypatch.setattr(check_audit_regressions, "CHECKS", checks)

    start = time.perf_counter()
    findings = check_audit_regressions.run()
    elapsed = time.perf_counter() - start

    assert len(findings) == 150  # 절반만 예외로 죽어 Finding을 남김
    assert elapsed < 1.0, f"run()이 {elapsed:.3f}s — 예산(1.0s) 초과"
