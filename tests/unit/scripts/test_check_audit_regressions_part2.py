"""scripts/check_audit_regressions.py 단위 테스트 — FA-15a(esc-2115).

DoD: "새 정적 검사가 raw 시드 픽스처를 주입하면 실패함을 증명" — 실제
저장소를 건드리지 않고 임시 디렉터리에 위반 패턴을 주입해
`check_ledger_balance_raw_seed`가 그것을 찾아내는지, 그리고
`# audit-allow` 주석이 있는 문서화된 예외는 봐주는지를 직접 실행해
확인한다. DB·네트워크 접근 없음 — 순수 텍스트 스캔이라 임시 파일만 쓴다.
"""

from __future__ import annotations

import importlib.util
import re
import sys
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


def _write_class_fixture(tmp_path: Path, rel_path: str, body: str) -> None:
    file_path = tmp_path / rel_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(body, encoding="utf-8")


def _marker_check():
    return check_audit_regressions.Finding("marker_ok", "정상 검사 통과 표식", ["marker:1"])


def _raising_check():
    raise ValueError("boom-injected")


@pytest.mark.perf
@pytest.mark.perf
def test_run_completes_within_time_budget_when_many_checkers_fail(monkeypatch, perf_budget) -> None:
    """성능단언: 격리 로직(try/except)이 검사 수백 개 규모에서도 예외 처리
    오버헤드로 예산을 넘기지 않는지 확인한다.

    raw perf_counter → perf_budget.sample(batch) 전환(task-11824).
    batch=100으로 묶어 process_time 양자화 오차(15.6ms 틱)를 호출당 0.16ms로
    낮춤. 예산 1000ms(ms 단위 일치)."""
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

    sample = perf_budget.sample(lambda: check_audit_regressions.run(), batch=100)
    findings = sample.result

    assert len(findings) == 150  # 절반만 예외로 죽어 Finding을 남김
    assert sample.cpu_ms < 1000.0, f"run()이 {sample.cpu_ms:.3f}ms — 예산(1000ms) 초과"


def test_finding_rejects_missing_constructor_arguments() -> None:
    """네거티브: `Finding`은 code/detail/evidence 세 값을 모두 요구한다 — 호출자가
    하나라도 빠뜨리면 조용히 반쪽짜리 Finding을 만드는 대신 즉시 TypeError로 죽어야
    한다(그래야 `run()`의 try/except가 "검사기 자체 실패"로 분류해 잡는다)."""
    with pytest.raises(TypeError):
        check_audit_regressions.Finding("only_code")


def test_hits_raises_on_invalid_regex_pattern() -> None:
    """네거티브: `_hits`에 컴파일 불가능한 정규식을 넘기면 빈 결과로 삼키지 않고
    `re.error`를 그대로 올려야 한다 — 검사 코드가 오타난 패턴을 쓰고도 "결함
    없음"으로 거짓 통과하는 것을 막는다."""
    with pytest.raises(re.error):
        check_audit_regressions._hits("(unclosed(", [])


def test_main_exits_nonzero_on_unknown_cli_flag(monkeypatch, capsys) -> None:
    """네거티브: argparse가 모르는 플래그를 받으면 `main()`이 베이스라인을 읽어
    보려 시도하는 대신 바로 `SystemExit(2)`로 fail-closed해야 한다 — 잘못된 호출이
    rc=0으로 조용히 넘어가는 것을 막는다."""
    monkeypatch.setattr(sys, "argv", ["check_audit_regressions.py", "--no-such-flag"])

    with pytest.raises(SystemExit) as exc_info:
        check_audit_regressions.main()

    assert exc_info.value.code == 2


def test_main_raises_when_baseline_path_is_a_directory_not_a_file(tmp_path, monkeypatch) -> None:
    """네거티브: `--baseline`에 디렉터리 경로를 주면 `read_text()`가 `OSError`
    서브클래스(`IsADirectoryError`)를 던진다 — 현재 `main()`은 `OSError`만
    JSONDecodeError와 함께 잡으므로 통과해야 하지만, 이 경계가 깨져 다른
    예외 타입으로 새면 이 테스트가 바로 잡는다."""
    baseline_dir = tmp_path / "audit-baseline.json"
    baseline_dir.mkdir()
    monkeypatch.setattr(
        sys, "argv", ["check_audit_regressions.py", "--baseline", str(baseline_dir)]
    )
    monkeypatch.setattr(check_audit_regressions, "CHECKS", [])

    rc = check_audit_regressions.main()

    assert rc == 0
