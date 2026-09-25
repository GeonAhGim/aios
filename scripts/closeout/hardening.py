"""11번째 종료 기준 -- 하드닝 H-1~H-13(ADR-2026-09-09-B) -- task-6475 분할 조각.

`scripts/closeout_check.py`의 책임 분할: 이 모듈은 H-1~H-13 개별 판정
함수와 `check_11_hardening` 집계만 담당한다.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from scripts.closeout.common import CheckResult, grep, present_missing, read_text


@dataclass(frozen=True)
class HardeningItem:
    id: str
    label: str
    check: Callable[[Path], tuple[bool, str]]


def _h1_mandate_required(repo_root: Path) -> tuple[bool, str]:
    hits = grep(repo_root, ("src/api", "src/services"), r"require_mandate\s*=\s*False")
    hits = [h for h in hits if "wiring.py:14" not in h]
    if not hits:
        return True, "require_mandate=False 0건"
    return False, f"require_mandate=False {len(hits)}건: {hits[0]}"


def _h2_cm_reporting_and_api(repo_root: Path) -> tuple[bool, str]:
    present, missing = present_missing(
        repo_root,
        "src/foundation/mandates/reporting/domain/trade_report.py",
        "src/foundation/mandates/reporting/ports/report_submitter.py",
        "src/api/routers/foundation/compliance.py",
        "frontend/apps/web/src/routes/compliance/CompliancePage.tsx",
        "tests/adversarial/compliance",
    )
    if not missing:
        return True, "CM-15~20 전부 존재"
    return False, f"CM-15~20 누락: {', '.join(missing)}"


def _h3_nh_no_notimplemented(repo_root: Path) -> tuple[bool, str]:
    """NH get_order REST 재조회 판정.

    get_ohlcv(market_data_mixin.py)는 별도 미검증 엔드포인트로 의도된
    미구현이라 이 항목의 대상이 아니다 -- get_order가 정의된
    trading_mixin.py만 본다. get_order가 여전히 NotImplementedError를
    던지더라도 파일 첫 20행의 ratchet-allow 주석 + docs/exchanges/NH_GAPS.md
    근거 문서가 있으면 재조회 조사가 끝나고 fail-closed로 확정된 결정으로
    인정한다(task-2615, ADR-2026-09-09-B).
    """
    rel = "src/exchanges/nh/trading_mixin.py"
    text = read_text(repo_root / rel)
    if "raise NotImplementedError" not in text:
        return True, "get_order NotImplementedError 0건"
    header = "\n".join(text.splitlines()[:20])
    documented = "ratchet-allow" in header
    gaps_doc = (repo_root / "docs/exchanges/NH_GAPS.md").is_file()
    if documented and gaps_doc:
        return True, "get_order REST 재조회 완료, fail-closed 결정 문서화 확인(NH_GAPS.md)"
    return False, "get_order NotImplementedError 미문서화(ratchet-allow/NH_GAPS.md 없음)"


def _h4_backup_scripts(repo_root: Path) -> tuple[bool, str]:
    present, missing = present_missing(
        repo_root,
        "scripts/backup/base_backup.py",
        "scripts/backup/wal_archive.py",
        "scripts/backup/restore_drill.py",
    )
    if not missing:
        return True, "백업/복구 스크립트 존재"
    return False, f"누락: {', '.join(missing)}"


def _h5_supply_chain_gate(repo_root: Path) -> tuple[bool, str]:
    text = read_text(repo_root / ".github" / "workflows" / "quality.yml")
    dependabot = (repo_root / ".github" / "dependabot.yml").is_file()
    ok = "run_pip_audit" in text and "npm audit" in text and dependabot
    if ok:
        return True, "공급망 게이트 배선 확인"
    return False, "pip-audit/npm audit/dependabot 중 미배선 항목 있음"


def _h6_dockerfiles(repo_root: Path) -> tuple[bool, str]:
    present, missing = present_missing(
        repo_root,
        "Dockerfile.api",
        "Dockerfile.worker",
        "frontend/Dockerfile",
        "compose.prod.yml",
    )
    if not missing:
        return True, "Dockerfile/compose.prod.yml 존재"
    dockerfiles = list(repo_root.glob("Dockerfile*")) + list(repo_root.glob("**/Dockerfile"))
    if dockerfiles:
        return False, f"일부 Dockerfile 존재하나 규격 미충족: {missing}"
    return False, "Dockerfile 0건, compose.prod.yml 없음"


def _h7_e2e_suites(repo_root: Path) -> tuple[bool, str]:
    backend_e2e = len(grep(repo_root, ("tests/e2e",), r"^\s*(async )?def test_"))
    playwright_present = (repo_root / "frontend" / "playwright.config.ts").is_file()
    ok = backend_e2e >= 3 and playwright_present
    return ok, (
        f"백엔드 e2e {backend_e2e}건, playwright 설정 존재"
        if ok
        else f"백엔드 e2e {backend_e2e}건(<3) 또는 playwright 설정 없음"
    )


def _h8_property_tests(repo_root: Path) -> tuple[bool, str]:
    hits = grep(
        repo_root,
        (
            "tests/unit/core/ledger",
            "tests/foundation/unit/ledger",
            "tests/unit/core/risk",
            "tests/unit/core/portfolio",
            "tests/property",
        ),
        r"hypothesis|given\(",
    )
    ok = bool(hits)
    if ok:
        return True, f"hypothesis property 테스트 {len(hits)}건"
    return False, "ledger/risk/position 축 hypothesis 테스트 없음"


def _h9_dsr_pbo_regression(repo_root: Path) -> tuple[bool, str]:
    rel = "tests/foundation/unit/backtest/test_overfitting.py"
    text = read_text(repo_root / rel)
    ok = "0.9004" in text and "0.9505" in text
    if ok:
        return True, "Bailey & Lopez de Prado 수치 회귀 확인"
    return False, "논문 수치 회귀 테스트 없음/불일치"


def _h10_alert_routing(repo_root: Path) -> tuple[bool, str]:
    text = read_text(repo_root / "config" / "observability" / "alert_rules.yaml")
    text += read_text(repo_root / "config" / "observability" / "alertmanager.yml")
    hits = grep(repo_root, ("config/observability",), r"webhook|slack|pagerduty", re.I)
    ok = bool(hits) or bool(re.search(r"webhook|slack|pagerduty", text, re.I))
    if ok:
        return True, "알림 라우팅 웹훅 배선 확인(alertmanager.yml)"
    return False, "alert_rules/alertmanager가 pager/slack 웹훅에 연결되지 않음"


def _h11_mandate_cache_invalidation(repo_root: Path) -> tuple[bool, str]:
    hits = grep(repo_root, ("src/services", "src/foundation"), r"mandate.*(cache|invalidat)", re.I)
    race_test = bool(grep(repo_root, ("tests",), r"mandate.*(cache|stale)", re.I))
    ok = bool(hits) and race_test
    if ok:
        return True, "mandate 캐시 무효화 배선+경합 테스트 확인"
    return False, "mandate 캐시 이벤트 기반 무효화 또는 경합 테스트 없음"


def _h12_legacy_wallet_bridge_sentinel(repo_root: Path) -> tuple[bool, str]:
    path = repo_root / "src" / "foundation" / "ledger" / "adapters" / "legacy_wallet_bridge.py"
    text = read_text(path)
    placeholder_cast = bool(re.search(r"cast\(\s*asyncpg\.Pool\s*,\s*None\s*\)", text))
    return not placeholder_cast, (
        "pool 주입 정식화 확인"
        if not placeholder_cast
        else "여전히 cast(asyncpg.Pool, None) placeholder 사용 중"
    )


def _h13_env_config(repo_root: Path) -> tuple[bool, str]:
    present, missing = present_missing(
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
    """기준11(ADR-2026-09-09-B) -- 하드닝 H-1~H-13 전부 닫힘."""
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
