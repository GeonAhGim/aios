"""scripts/check_audit_regressions.py 단위 테스트 — FA-15a(esc-2115).

DoD: "새 정적 검사가 raw 시드 픽스처를 주입하면 실패함을 증명" — 실제
저장소를 건드리지 않고 임시 디렉터리에 위반 패턴을 주입해
`check_ledger_balance_raw_seed`가 그것을 찾아내는지, 그리고
`# audit-allow` 주석이 있는 문서화된 예외는 봐주는지를 직접 실행해
확인한다. DB·네트워크 접근 없음 — 순수 텍스트 스캔이라 임시 파일만 쓴다.
"""
from __future__ import annotations

import importlib.util
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
        'async def create_ledger_account(pool, *, initial_balance):\n'
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
