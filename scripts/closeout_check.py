"""MVP-1 종료조건 기계 검사 — ADR-2026-09-09-D Decision 3.

ADR-2026-09-04-D의 T3 종료 기준 1~10과 ADR-2026-09-09-B로 추가된 11항
(H-1~H-13 전부 CI 증빙으로 닫힘)을 각각 검사 함수로 판정해 PASS/FAIL과
증빙 경로를 마크다운 표로 출력한다.

전부 저장소 안 정적 증거(파일 존재·grep·순수 모듈 import)만 본다 —
`check_release_gate.py`·`check_audit_regressions.py`와 같은 방식으로
DB·네트워크 접근이 없어 CI worktree에서도 그대로 돈다. "최근 CI 통과"·
"Guard veto 0" 같이 저장소 밖 상태에 의존하는 항목은 이 스크립트 혼자서는
완전히 판정할 수 없다 — `--ci-report`/`--guard-report`로 pm/ 쪽 JSON 리포트
경로를 넘기면 그 값을 쓰고, 안 넘기면 "미검증(외부 리포트 미지정)"으로
FAIL 처리한다(하나라도 적색이면 종결 불가라는 ADR-D 원칙 — 모른다=통과 아님).

사용: `python scripts/closeout_check.py [--ci-report P] [--guard-report P] [--write PATH]`.
`--write`는 전부 PASS일 때만 그 경로에 `MVP-1_CLOSEOUT.md`류 문서를 쓴다
(ADR-D: "전부 녹색이면 문서 생성", 하나라도 적색이면 쓰지 않는다).
종료코드: 0=전부 PASS, 1=하나 이상 FAIL.
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

UNVERIFIED = "미검증(외부 리포트 미지정)"


@dataclass(frozen=True)
class CheckResult:
    key: str
    title: str
    passed: bool
    evidence: tuple[str, ...]
    detail: str


# --------------------------------------------------------------------------- 공용 헬퍼


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _iter_py(repo_root: Path, *rels: str) -> list[Path]:
    out: list[Path] = []
    for rel in rels:
        base = repo_root / rel
        if base.is_file():
            out.append(base)
        elif base.is_dir():
            out.extend(p for p in sorted(base.rglob("*.py")) if "__pycache__" not in p.parts)
    return out


def _has_test_def(repo_root: Path, rel: str) -> bool:
    path = repo_root / rel
    return path.is_file() and re.search(r"^\s*(async )?def test_", _read(path), re.M) is not None


def _grep(repo_root: Path, rels: Sequence[str], pattern: str, flags: int = 0) -> list[str]:
    rx = re.compile(pattern, flags)
    hits: list[str] = []
    for p in _iter_py(repo_root, *rels):
        for i, line in enumerate(_read(p).splitlines(), 1):
            if rx.search(line):
                hits.append(f"{p.relative_to(repo_root).as_posix()}:{i}")
    return hits


def _present_missing(repo_root: Path, *rels: str) -> tuple[list[str], list[str]]:
    present = [r for r in rels if (repo_root / r).exists()]
    missing = [r for r in rels if r not in present]
    return present, missing


# --------------------------------------------------------------------------- 1~10: T3 종료 기준


def check_01_parity(repo_root: Path) -> CheckResult:
    """기준1 — 패리티(I-05): 백테스트=PAPER 동일 신호·체결 테스트 존재."""
    paths = (
        "tests/integration/backtest/test_parity_harness.py",
        "tests/integration/foundation/backtest/test_vector_event_parity.py",
    )
    ok = [p for p in paths if _has_test_def(repo_root, p)]
    missing = [p for p in paths if p not in ok]
    passed = not missing
    detail = (
        "패리티 테스트 파일 존재(정적 확인). \"최근 CI 통과\"는 --ci-report로만 판정한다."
        if passed
        else f"패리티 테스트 파일 누락: {', '.join(missing)}"
    )
    return CheckResult("01_parity", "패리티(I-05)", passed, tuple(ok + missing), detail)


def check_02_safety_wiring(repo_root: Path) -> CheckResult:
    """기준2 — kill switch/DataDistrust 적대 테스트 + 게이트 인자 Optional 0건(I-01)."""
    kill_switch = "tests/adversarial/order_service/test_kill_switch_blocks_execution_loop.py"
    kill_switch_ok = _has_test_def(repo_root, kill_switch)
    distrust_hits = _grep(repo_root, ("tests/adversarial", "tests/foundation/adversarial"),
                          r"DataDistrust|DEGRADED")
    optional_gate_hits = _grep(
        repo_root,
        ("src/services/order_service", "src/services/oms", "src/services/execution_loop"),
        r"(pre_submit_gate|pre_send_gate|pre_start_gate)\s*:\s*[^=\n]*\|\s*None",
    )
    passed = kill_switch_ok and bool(distrust_hits) and not optional_gate_hits
    evidence = [kill_switch, *distrust_hits, *(f"OPTIONAL:{h}" for h in optional_gate_hits)]
    parts = []
    if not kill_switch_ok:
        parts.append("kill switch 적대 테스트 없음")
    if not distrust_hits:
        parts.append("DataDistrust/DEGRADED 적대 테스트 없음")
    if optional_gate_hits:
        parts.append(f"안전 게이트 인자 Optional {len(optional_gate_hits)}건(I-01 위반)")
    detail = "안전 배선 증명 통과" if passed else "; ".join(parts)
    return CheckResult("02_safety_wiring", "안전 배선 증명", passed, tuple(evidence), detail)


def check_03_validation_gates(repo_root: Path) -> CheckResult:
    """기준3 — 임계 미달 전략이 실제 FAIL(I-07) + DSR/PBO가 승인 경로에서 조회됨."""
    hard_fail_test = _has_test_def(repo_root, "tests/foundation/unit/validation/test_rules.py")
    dsr_pbo_wired = _grep(
        repo_root,
        ("src/foundation/validation", "src/api"),
        r"from\s+src\.foundation\.backtest\.domain\.overfitting\s+import|overfitting\.(deflated_sharpe|pbo_cscv)",
    )
    passed = hard_fail_test and bool(dsr_pbo_wired)
    evidence = ["tests/foundation/unit/validation/test_rules.py", *dsr_pbo_wired]
    detail = (
        "검증 게이트 실효 확인"
        if passed
        else "DSR/PBO(overfitting.py)가 마켓 승인 경로(validation/api)에서 아직 조회되지 않는다"
    )
    return CheckResult("03_validation_gates", "검증 게이트 실효", passed, tuple(evidence), detail)


def check_04_strategy_language(repo_root: Path) -> CheckResult:
    """기준4 — DSL property 테스트 + cond-v2 변환 동일성 + 컴파일 ≤300ms 벤치 결과."""
    property_test = _has_test_def(repo_root, "tests/unit/core/script/test_interpreter_property.py")
    cond_v2_hits = _grep(repo_root, ("tests/unit/core/script",), r"cond.?v2", re.I)
    bench_present, bench_missing = _present_missing(repo_root, "docs/perf/dsl_compile_bench.json")
    passed = property_test and bool(cond_v2_hits) and not bench_missing
    evidence = [
        "tests/unit/core/script/test_interpreter_property.py",
        *cond_v2_hits,
        *bench_present,
        *bench_missing,
    ]
    parts = []
    if not property_test:
        parts.append("DSL property 테스트 없음")
    if not cond_v2_hits:
        parts.append("cond-v2 변환 동일성 테스트 없음")
    if bench_missing:
        parts.append(f"컴파일 ≤300ms 벤치 결과 파일 없음: {bench_missing[0]}")
    detail = "전략 언어 기준 통과" if passed else "; ".join(parts)
    return CheckResult("04_strategy_language", "전략 언어(DSL)", passed, tuple(evidence), detail)


def _default_indicator_count(repo_root: Path) -> int | None:
    """`src.core.indicators.specs_talib.TALIB_SPECS` 개수(순수 모듈, I/O 없음).

    스크립트가 어디서 실행되든 `src` 패키지를 찾도록 repo_root를 sys.path
    맨 앞에 넣는다 — `python scripts/closeout_check.py`처럼 직접 실행하면
    sys.path[0]이 scripts/가 되어 `from src...`가 실패하기 때문이다.
    """
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    sys.modules.pop("src.core.indicators.specs_talib", None)
    try:
        module = importlib.import_module("src.core.indicators.specs_talib")
    except Exception:  # noqa: BLE001 - import 실패 자체가 이 검사의 FAIL 신호다
        return None
    count = getattr(module, "TALIB_SPECS", None)
    return len(count) if count is not None else None


def check_05_indicators(
    repo_root: Path,
    *,
    count_indicators: Callable[[Path], int | None] = _default_indicator_count,
) -> CheckResult:
    """기준5 — 지표 ≥100종 + 참조 벡터/증분=일괄 동일성 테스트."""
    count = count_indicators(repo_root)
    ref_test = _has_test_def(repo_root, "tests/unit/core/indicators/test_engine_equivalence.py")
    passed = count is not None and count >= 100 and ref_test
    evidence = [f"registry_count={count}", "tests/unit/core/indicators/test_engine_equivalence.py"]
    if count is None:
        detail = "지표 레지스트리 import 실패"
    elif count < 100:
        detail = f"지표 {count}종 < 100"
    elif not ref_test:
        detail = "참조 벡터/증분=일괄 동일성 테스트 없음"
    else:
        detail = f"지표 {count}종, 참조 벡터 테스트 확인"
    return CheckResult("05_indicators", "지표 ≥100종", passed, tuple(evidence), detail)


def check_06_backtest_realism(repo_root: Path) -> CheckResult:
    """기준6 — 슬리피지·수수료·지연·부분체결·주문유형·펀딩 계약 테스트 + ≤5s 즉시 백테스트 벤치."""
    contract_hits = _grep(
        repo_root,
        ("tests/foundation/unit/backtest", "tests/foundation/integration/backtest",
         "tests/integration/backtest"),
        r"slippage|fee_tier|funding|partial_fill|latency",
        re.I,
    )
    bench_present, bench_missing = _present_missing(
        repo_root, "docs/perf/backtest_instant_bench.json"
    )
    passed = bool(contract_hits) and not bench_missing
    evidence = [*contract_hits[:10], *bench_present, *bench_missing]
    parts = []
    if not contract_hits:
        parts.append("체결 현실성 계약 테스트 없음")
    if bench_missing:
        parts.append(f"즉시 백테스트 ≤5s 벤치 결과 파일 없음: {bench_missing[0]}")
    detail = "백테스트 현실성 기준 통과" if passed else "; ".join(parts)
    return CheckResult("06_backtest_realism", "백테스트 현실성", passed, tuple(evidence), detail)


def check_07_execution(repo_root: Path) -> CheckResult:
    """기준7 — OMS 상태기계 실배선 통합테스트 + pre-trade 지연 CI 단언(R-57)."""
    wiring_hits = _grep(repo_root, ("tests/integration/oms", "tests/adversarial/oms"),
                        r"partial.?fill|cancel|amend|recover|reconcil", re.I)
    latency_hits = _grep(repo_root, ("tests",), r"@pytest\.mark\.perf")
    pretrade_latency_hits = [
        h for h in latency_hits if "pre_trade" in h.lower() or "pre_submit" in h.lower()
    ]
    passed = bool(wiring_hits) and bool(pretrade_latency_hits)
    evidence = [*wiring_hits[:10], *pretrade_latency_hits[:5]]
    parts = []
    if not wiring_hits:
        parts.append("OMS 부분체결/취소/정정/복구/대사 통합테스트 없음")
    if not pretrade_latency_hits:
        parts.append("pre-trade 지연 perf 단언(R-57) 없음")
    detail = "실행 기준 통과" if passed else "; ".join(parts)
    return CheckResult("07_execution", "실행(OMS)", passed, tuple(evidence), detail)


def check_08_data(repo_root: Path) -> CheckResult:
    """기준8 — instrument_id p95 200ms 벤치 + 커버리지 밖 fail-closed + 거래소 2곳 SPI 계약."""
    bench_present, bench_missing = _present_missing(
        repo_root, "docs/perf/instrument_lookup_bench.json"
    )
    coverage_hits = _grep(
        repo_root, ("tests",), r"coverage.*(fail.?closed|deny)|out.?of.?coverage", re.I
    )
    spi_dirs = ("tests/exchanges", "tests/unit/exchanges", "tests/integration/exchanges")
    spi_hits = _grep(repo_root, spi_dirs, r"def test_.*(contract|spi)", re.I)
    passed = not bench_missing and bool(coverage_hits) and len(spi_hits) > 0
    evidence = [*bench_present, *bench_missing, *coverage_hits[:5], *spi_hits[:10]]
    parts = []
    if bench_missing:
        parts.append(f"instrument_id p95 200ms 벤치 결과 파일 없음: {bench_missing[0]}")
    if not coverage_hits:
        parts.append("커버리지 밖 요청 fail-closed 테스트 없음")
    if not spi_hits:
        parts.append("거래소 SPI 계약 테스트 없음")
    detail = "데이터 기준 통과" if passed else "; ".join(parts)
    return CheckResult("08_data", "데이터", passed, tuple(evidence), detail)


def check_09_chart(repo_root: Path) -> CheckResult:
    """기준9 — 캔들·오버레이·드로잉 저장/복원(교차 테넌트 404) + 빌드/vitest 설정."""
    cross_tenant_hits = _grep(repo_root, ("tests",), r"chart.*tenant|drawing.*tenant", re.I)
    frontend_dir = repo_root / "frontend"
    vitest_present, vitest_missing = _present_missing(
        repo_root, "frontend/vitest.config.ts", "frontend/package.json"
    )
    passed = bool(cross_tenant_hits) and not vitest_missing and frontend_dir.is_dir()
    evidence = [*cross_tenant_hits[:5], *vitest_present, *vitest_missing]
    parts = []
    if not cross_tenant_hits:
        parts.append("차트/드로잉 교차 테넌트 404 테스트 없음")
    if vitest_missing:
        parts.append(f"vitest/frontend 설정 없음: {', '.join(vitest_missing)}")
    detail = "차트 기준 통과(정적)" if passed else "; ".join(parts)
    return CheckResult("09_chart", "차트", passed, tuple(evidence), detail)


def check_10_ops(
    repo_root: Path, *, ci_report: Path | None, guard_report: Path | None
) -> CheckResult:
    """기준10 — 로컬 CI 녹색 + Guard veto 0 + INVARIANTS 위반 0 + RED_TEAM P0 미해결 0."""
    ci_ok, ci_note = _load_bool_report(ci_report, "passed")
    guard_ok, guard_note = _load_bool_report(guard_report, "veto_count", expect_zero=True)
    invariants_ok, invariants_note = _check_invariants(repo_root)
    red_team_ok, red_team_note = _check_red_team_open(repo_root)

    passed = ci_ok and guard_ok and invariants_ok and red_team_ok
    evidence = [ci_note, guard_note, invariants_note, red_team_note]
    detail = "운영 기준 통과" if passed else "; ".join(e for e in evidence if "OK" not in e[:2])
    return CheckResult("10_ops", "운영", passed, tuple(evidence), detail)


def _load_bool_report(
    path: Path | None, key: str, *, expect_zero: bool = False
) -> tuple[bool, str]:
    if path is None:
        return False, UNVERIFIED
    if not path.is_file():
        return False, f"리포트 없음: {path}"
    try:
        data = json.loads(_read(path))
    except json.JSONDecodeError:
        return False, f"리포트 JSON 파싱 실패: {path}"
    value = data.get(key)
    if expect_zero:
        ok = isinstance(value, int) and value == 0
        return ok, f"OK {key}=0" if ok else f"{key}={value!r} (0이어야 함): {path}"
    ok = bool(value)
    return ok, f"OK {key}=True" if ok else f"{key}={value!r}: {path}"


def _check_invariants(repo_root: Path) -> tuple[bool, str]:
    script = repo_root / "scripts" / "check_audit_regressions.py"
    if not script.is_file():
        return False, "scripts/check_audit_regressions.py 없음"
    baseline = repo_root / "audit-baseline.json"
    try:
        open_findings = json.loads(_read(baseline)).get("open", {})
    except json.JSONDecodeError:
        open_findings = {}
    result = subprocess.run(  # noqa: S603 - 저장소 내 고정 경로 스크립트, 사용자 입력 없음
        [sys.executable, str(script)],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    ok = result.returncode == 0 and not open_findings
    if not ok and result.returncode != 0:
        return False, "check_audit_regressions.py FAIL(신규/미해소 회귀 있음)"
    if open_findings:
        names = ", ".join(open_findings)
        return False, f"audit-baseline.json에 열린 항목 {len(open_findings)}건: {names}"
    return True, "OK INVARIANTS 위반 0"


def _check_red_team_open(repo_root: Path) -> tuple[bool, str]:
    path = repo_root / "docs" / "RED_TEAM_FINDINGS.md"
    text = _read(path)
    open_count = text.count("⏳ OPEN")
    ok = open_count == 0
    return ok, "OK RED_TEAM 미해결 0" if ok else f"RED_TEAM_FINDINGS.md 미해결(OPEN) {open_count}건"


CHECKS_1_10: tuple[Callable[..., CheckResult], ...] = (
    check_01_parity,
    check_02_safety_wiring,
    check_03_validation_gates,
    check_04_strategy_language,
    check_05_indicators,
    check_06_backtest_realism,
    check_07_execution,
    check_08_data,
    check_09_chart,
)


# --------------------------------------------------------------------------- 11번째: 하드닝


@dataclass(frozen=True)
class HardeningItem:
    id: str
    label: str
    check: Callable[[Path], tuple[bool, str]]


def _h1_mandate_required(repo_root: Path) -> tuple[bool, str]:
    hits = _grep(repo_root, ("src/api", "src/services"), r"require_mandate\s*=\s*False")
    hits = [h for h in hits if "wiring.py:14" not in h]
    if not hits:
        return True, "require_mandate=False 0건"
    return False, f"require_mandate=False {len(hits)}건: {hits[0]}"


def _h2_cm_reporting_and_api(repo_root: Path) -> tuple[bool, str]:
    present, missing = _present_missing(
        repo_root,
        "src/foundation/reporting/domain/trade_report.py",
        "src/foundation/reporting/ports/report_submitter.py",
        "src/api/routers/compliance.py",
        "frontend/src/pages/CompliancePage.tsx",
        "tests/adversarial/compliance",
    )
    if not missing:
        return True, "CM-15~20 전부 존재"
    return False, f"CM-15~20 누락: {', '.join(missing)}"


def _h3_nh_no_notimplemented(repo_root: Path) -> tuple[bool, str]:
    hits = _grep(repo_root, ("src/exchanges/nh",), r"raise NotImplementedError")
    if not hits:
        return True, "NH get_order NotImplementedError 0건"
    return False, f"NH NotImplementedError {len(hits)}건: {hits[0]}"


def _h4_backup_scripts(repo_root: Path) -> tuple[bool, str]:
    present, missing = _present_missing(
        repo_root,
        "scripts/backup/pg_basebackup.py",
        "scripts/backup/wal_archive.py",
        "scripts/backup/restore_drill.py",
    )
    if not missing:
        return True, "백업/복구 스크립트 존재"
    return False, f"누락: {', '.join(missing)}"


def _h5_supply_chain_gate(repo_root: Path) -> tuple[bool, str]:
    text = _read(repo_root / ".github" / "workflows" / "quality.yml")
    dependabot = (repo_root / ".github" / "dependabot.yml").is_file()
    ok = "run_pip_audit" in text and "npm audit" in text and dependabot
    if ok:
        return True, "공급망 게이트 배선 확인"
    return False, "pip-audit/npm audit/dependabot 중 미배선 항목 있음"


def _h6_dockerfiles(repo_root: Path) -> tuple[bool, str]:
    present, missing = _present_missing(
        repo_root,
        "Dockerfile.api",
        "Dockerfile.worker",
        "Dockerfile.frontend",
        "compose.prod.yml",
    )
    if not missing:
        return True, "Dockerfile/compose.prod.yml 존재"
    dockerfiles = list(repo_root.glob("Dockerfile*")) + list(repo_root.glob("**/Dockerfile"))
    if dockerfiles:
        return False, f"일부 Dockerfile 존재하나 규격 미충족: {missing}"
    return False, "Dockerfile 0건, compose.prod.yml 없음"


def _h7_e2e_suites(repo_root: Path) -> tuple[bool, str]:
    backend_e2e = len(_grep(repo_root, ("tests/e2e",), r"^\s*(async )?def test_"))
    playwright_present = (repo_root / "frontend" / "playwright.config.ts").is_file()
    ok = backend_e2e >= 3 and playwright_present
    return ok, (
        f"백엔드 e2e {backend_e2e}건, playwright 설정 존재" if ok
        else f"백엔드 e2e {backend_e2e}건(<3) 또는 playwright 설정 없음"
    )


def _h8_property_tests(repo_root: Path) -> tuple[bool, str]:
    hits = _grep(
        repo_root,
        ("tests/unit/core/ledger", "tests/foundation/unit/ledger", "tests/unit/core/risk",
         "tests/unit/core/portfolio"),
        r"hypothesis|given\(",
    )
    ok = bool(hits)
    if ok:
        return True, f"hypothesis property 테스트 {len(hits)}건"
    return False, "ledger/risk/position 축 hypothesis 테스트 없음"


def _h9_dsr_pbo_regression(repo_root: Path) -> tuple[bool, str]:
    rel = "tests/foundation/unit/backtest/test_overfitting.py"
    text = _read(repo_root / rel)
    ok = "0.9004" in text and "0.9505" in text
    if ok:
        return True, "Bailey & Lopez de Prado 수치 회귀 확인"
    return False, "논문 수치 회귀 테스트 없음/불일치"


def _h10_alert_routing(repo_root: Path) -> tuple[bool, str]:
    text = _read(repo_root / "config" / "observability" / "alert_rules.yaml")
    hits = _grep(repo_root, ("config/observability",), r"webhook|slack|pagerduty", re.I)
    ok = bool(hits) or bool(re.search(r"webhook|slack|pagerduty", text, re.I))
    if ok:
        return True, "알림 라우팅 웹훅 배선 확인"
    return False, "alert_rules가 pager/slack 웹훅에 연결되지 않음"


def _h11_mandate_cache_invalidation(repo_root: Path) -> tuple[bool, str]:
    hits = _grep(
        repo_root, ("src/services", "src/foundation"), r"mandate.*(cache|invalidat)", re.I
    )
    race_test = bool(_grep(repo_root, ("tests",), r"mandate.*(cache|stale)", re.I))
    ok = bool(hits) and race_test
    if ok:
        return True, "mandate 캐시 무효화 배선+경합 테스트 확인"
    return False, "mandate 캐시 이벤트 기반 무효화 또는 경합 테스트 없음"


def _h12_legacy_wallet_bridge_sentinel(repo_root: Path) -> tuple[bool, str]:
    path = repo_root / "src" / "foundation" / "ledger" / "adapters" / "legacy_wallet_bridge.py"
    text = _read(path)
    placeholder_cast = bool(re.search(r"cast\(\s*asyncpg\.Pool\s*,\s*None\s*\)", text))
    return not placeholder_cast, (
        "pool 주입 정식화 확인" if not placeholder_cast
        else "여전히 cast(asyncpg.Pool, None) placeholder 사용 중"
    )


def _h13_env_config(repo_root: Path) -> tuple[bool, str]:
    present, missing = _present_missing(
        repo_root, "config/dev.yaml", "config/staging.yaml", "config/live.yaml"
    )
    if not missing:
        return True, "env별 설정 파일 존재"
    return False, f"누락: {', '.join(missing)}"


HARDENING_ITEMS: tuple[HardeningItem, ...] = (
    HardeningItem("H-1", "mandate 바인딩 require_mandate=True", _h1_mandate_required),
    HardeningItem("H-2", "CM-11/15/16/17/20 완결", _h2_cm_reporting_and_api),
    HardeningItem("H-3", "NH get_order REST 재조회", _h3_nh_no_notimplemented),
    HardeningItem("H-4", "백업·PITR·복구 리허설", _h4_backup_scripts),
    HardeningItem("H-5", "공급망 취약점 게이트", _h5_supply_chain_gate),
    HardeningItem("H-6", "Dockerfile/compose.prod", _h6_dockerfiles),
    HardeningItem("H-7", "backend e2e 3 + Playwright 3", _h7_e2e_suites),
    HardeningItem("H-8", "hypothesis property 테스트", _h8_property_tests),
    HardeningItem("H-9", "Deflated Sharpe/PBO 원문 검증", _h9_dsr_pbo_regression),
    HardeningItem("H-10", "alert_rules pager/slack 라우팅", _h10_alert_routing),
    HardeningItem("H-11", "mandate 캐시 즉시 무효화", _h11_mandate_cache_invalidation),
    HardeningItem("H-12", "legacy_wallet_bridge sentinel", _h12_legacy_wallet_bridge_sentinel),
    HardeningItem("H-13", "env별 설정 분리", _h13_env_config),
)


def check_11_hardening(
    repo_root: Path, *, items: tuple[HardeningItem, ...] = HARDENING_ITEMS
) -> CheckResult:
    """기준11(ADR-2026-09-09-B) — 하드닝 H-1~H-13 전부 닫힘."""
    evidence: list[str] = []
    all_ok = True
    for item in items:
        ok, note = item.check(repo_root)
        all_ok = all_ok and ok
        mark = "PASS" if ok else "FAIL"
        evidence.append(f"[{mark}] {item.id} {item.label}: {note}")
    fail_ids = [e.split()[1] for e in evidence if e.startswith("[FAIL]")]
    detail = "H-1~H-13 전부 닫힘" if all_ok else f"미완료: {', '.join(fail_ids)}"
    title = "하드닝(ADR-09-09-B H-1~13)"
    return CheckResult("11_hardening", title, all_ok, tuple(evidence), detail)


# --------------------------------------------------------------------------- 리포트


def run_all(
    repo_root: Path, *, ci_report: Path | None = None, guard_report: Path | None = None
) -> list[CheckResult]:
    results = [fn(repo_root) for fn in CHECKS_1_10]
    results.append(check_10_ops(repo_root, ci_report=ci_report, guard_report=guard_report))
    results.append(check_11_hardening(repo_root))
    return results


def render_markdown(results: list[CheckResult]) -> str:
    lines = ["| # | 항목 | 판정 | 요약 |", "|---|---|---|---|"]
    for i, r in enumerate(results, 1):
        mark = "PASS" if r.passed else "FAIL"
        lines.append(f"| {i} | {r.title} | {mark} | {r.detail} |")
    lines.append("")
    lines.append("## 증빙")
    for i, r in enumerate(results, 1):
        lines.append(f"\n### {i}. {r.title} — {'PASS' if r.passed else 'FAIL'}")
        for e in r.evidence:
            lines.append(f"- {e}")
    return "\n".join(lines) + "\n"


def write_closeout_doc(path: Path, results: list[CheckResult]) -> None:
    body = (
        "# MVP-1 종료 확인서 (CLOSEOUT)\n\n"
        "ADR-2026-09-04-D 종료 기준 1~10 + ADR-2026-09-09-B 11항(H-1~13) — "
        "`scripts/closeout_check.py` 전항 PASS 시점 스냅샷.\n\n" + render_markdown(results)
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Windows cp949 방지

    parser = argparse.ArgumentParser(description="MVP-1 종료조건 기계 검사(ADR-2026-09-09-D)")
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument(
        "--ci-report", type=Path, default=None, help='pm/ci/latest.json류 {"passed": bool}'
    )
    parser.add_argument(
        "--guard-report", type=Path, default=None, help='{"veto_count": int}'
    )
    parser.add_argument(
        "--write", type=Path, default=None, help="전부 PASS일 때만 이 경로에 문서를 쓴다"
    )
    args = parser.parse_args(argv)

    results = run_all(args.repo_root, ci_report=args.ci_report, guard_report=args.guard_report)
    print(render_markdown(results))

    all_passed = all(r.passed for r in results)
    if args.write is not None:
        if all_passed:
            write_closeout_doc(args.write, results)
            print(f"OK: {args.write} 작성 완료")
        else:
            print(f"FAIL: 하나 이상 적색 — {args.write} 미작성")

    if all_passed:
        print("OK: MVP-1 종료조건 전항 PASS")
        return 0
    fail_titles = [r.title for r in results if not r.passed]
    print(f"FAIL: 적색 항목 {len(fail_titles)}건 — {', '.join(fail_titles)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
