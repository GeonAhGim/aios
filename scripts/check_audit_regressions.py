"""감사 발견 재발 방지 검사 — 2026-09-06 전면 감사(ADR-E·G)에서 나온 결함 양식을 영구 고정한다.

문제 의식: 감사는 사람이 물어봐야 돌았고, 고쳐진 뒤에는 아무도 다시 보지 않았다.
그래서 같은 결함이 다시 들어와도 알 수 없었다(실제로 `legal_entity.tenant_id`가
ADR-E 발행 당일에 같은 결함으로 다시 만들어졌다). 발견을 문장이 아니라 **단언**으로 바꾼다.

동작은 `coverage_ratchet.py`와 같은 래칫이다:
  * `audit-baseline.json`에 "지금 열려 있음이 알려진" 결함을 담당 task와 함께 적어 둔다.
  * 베이스라인에 없는 결함이 새로 나타나면 **실패**한다(회귀·신규 유입 차단).
  * 베이스라인에 있는데 실제로는 사라진 결함이 있으면 **실패**한다 — 베이스라인에서 지우라는 뜻이다.
    한 번 닫힌 항목은 조용히 다시 열릴 수 없다.

DB·네트워크·모듈 임포트를 하지 않는다. 순수 텍스트 스캔이라 어떤 환경에서도 돈다.
사용: `python scripts/check_audit_regressions.py`. 종료코드 0=통과.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASELINE = ROOT / "audit-baseline.json"


class Finding:
    def __init__(self, code: str, detail: str, evidence: list[str]) -> None:
        self.code = code
        self.detail = detail
        self.evidence = evidence

    def __repr__(self) -> str:  # pragma: no cover - 진단 출력용
        return f"<{self.code}: {len(self.evidence)}건>"


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _hits(pattern: str, paths: list[Path], flags: int = 0) -> list[str]:
    """정규식에 걸리는 줄을 `상대경로:줄번호` 목록으로 돌려준다."""
    rx = re.compile(pattern, flags)
    out: list[str] = []
    for p in paths:
        for i, line in enumerate(_read(p).splitlines(), 1):
            if rx.search(line):
                out.append(f"{p.relative_to(ROOT).as_posix()}:{i}")
    return out


def _py(*rel: str) -> list[Path]:
    out: list[Path] = []
    for r in rel:
        base = ROOT / r
        if base.is_file():
            out.append(base)
        elif base.is_dir():
            out.extend(sorted(base.rglob("*.py")))
    return out


# --------------------------------------------------------------------------- 검사

def check_optional_safety_gate() -> Finding | None:
    """I-01 — 안전 게이트 인자가 Optional이면 넘기지 않아도 통과한다(P0-B 양식)."""
    hits = _hits(r"(pre_submit_gate|pre_send_gate|pre_start_gate)\s*:\s*[^=\n]*\|\s*None",
                 _py("src/services/order_service", "src/services/oms",
                     "src/services/execution_loop"))
    if hits:
        return Finding(
            "optional_safety_gate",
        "안전 게이트 인자가 Optional이다 — 안 넘겨도 통과한다(I-01 위반).", hits)
    return None


def check_require_mandate_false() -> Finding | None:
    """I-09 — 이중 권위 중 컴플라이언스 절반이 감사로그 전용 no-op이 된다."""
    hits = _hits(r"require_mandate\s*=\s*False", _py("src/api", "src/services"))
    hits = [h for h in hits if "wiring.py:14" not in h]  # docstring 설명 줄은 제외
    if hits:
        return Finding(
            "require_mandate_false",
        "운영 조립이 require_mandate=False다 — 컴플라이언스 판정이 무력하다.", hits)
    return None


def check_executor_gate() -> Finding | None:
    """운영 주문 경로가 게이트를 아예 넘기지 않는 형태(P0-B의 본체)."""
    p = ROOT / "src/core/executor/executor.py"
    body = _read(p)
    if not body:
        return None
    if "submit_order(" in body and "pre_submit_gate" not in body:
        return Finding("executor_gate_missing",
                       "Executor가 submit_order를 부르면서 pre_submit_gate를 넘기지 않는다.",
                       [f"{p.relative_to(ROOT).as_posix()}"])
    return None


def check_freshness_tracker() -> Finding | None:
    """P0-A — 미주입 시 data_delay=None으로 서킷브레이커가 영구 HALTED가 된다."""
    body = _read(ROOT / "src/main.py")
    if body and "freshness_tracker" not in body:
        return Finding(
            "freshness_tracker_unwired",
        "main.py가 DataFreshnessTracker를 안 만든다 — 기동 직후 영구 HALTED.",
                       ["src/main.py"])
    return None


def check_xfail_in_gate_tests() -> Finding | None:
    """검사가 자기 자신을 무력화하는 형태(P0-C)."""
    hits = _hits(r"xfail", _py("tests/unit/test_gate_params_required.py"))
    def _is_real(h: str) -> bool:
        path, num = h.split(":")[0], int(h.split(":")[1])
        return "xfail 없음" not in _read(ROOT / path).splitlines()[num - 1]

    hits = [h for h in hits if _is_real(h)]
    if hits:
        return Finding("xfail_in_gate_tests",
                       "I-01 정적 검사에 xfail이 있다 — 위반이 있어도 CI가 녹색이 된다.", hits)
    return None


def check_tenant_fk_to_users() -> Finding | None:
    """FA-0a 양식 — tenant_id가 users를 FK하면 조직 테넌트 도입 시 전부 깨진다."""
    mig = ROOT / "src/db/migrations/versions"
    hits: list[str] = []
    for p in sorted(mig.glob("*.py")):
        text = _read(p)
        # 교정 마이그레이션은 DROP/재생성 과정에서 옛 정의를 문자열로 담는다 — 파일명으로 제외한다.
        if "_fk_fix" in p.name:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            if re.search(r"tenant_id[^,\n]*REFERENCES\s+users\s*\(", line, re.I):
                hits.append(f"{p.relative_to(ROOT).as_posix()}:{i}")
    if hits:
        return Finding(
            "tenant_fk_to_users",
        "tenant_id가 users를 FK한다 — 실제 테넌트 테이블은 tenant다(ADR-G D0).", hits)
    return None


def check_rls_enable_without_force() -> Finding | None:
    """ENABLE만 걸면 테이블 소유자가 정책을 우회한다(P0-E)."""
    mig = ROOT / "src/db/migrations/versions"
    enabled: set[str] = set()
    forced: set[str] = set()
    for p in sorted(mig.glob("*.py")):
        text = _read(p)
        for m in re.finditer(r"ALTER TABLE\s+\{?(\w+)\}?\s+ENABLE ROW LEVEL SECURITY", text, re.I):
            enabled.add(m.group(1))
        for m in re.finditer(r"ALTER TABLE\s+\{?(\w+)\}?\s+FORCE ROW LEVEL SECURITY", text, re.I):
            forced.add(m.group(1))
        # 루프 변수로 테이블을 도는 형태는 이름을 못 잡는다 — 파일 단위로 FORCE 존재만 본다.
        if re.search(r"FORCE ROW LEVEL SECURITY", text, re.I):
            forced.add("__loop__")
    missing = sorted(t for t in enabled if t not in forced and t != "__loop__")
    if missing and "__loop__" not in forced:
        return Finding(
            "rls_enable_without_force",
        f"RLS ENABLE만 하고 FORCE 안 함: {', '.join(missing)} — 소유자가 우회한다.",
                       missing)
    return None


def check_duplicate_type_names() -> Finding | None:
    """동명이의 계약 타입 — 잘못 임포트해도 타입검사를 통과한다."""
    watch = ["IdempotencyScope"]
    out: list[str] = []
    for name in watch:
        hits = _hits(rf"^class {name}\b", _py("src"), re.M)
        if len(hits) > 1:
            out.extend(f"{name} @ {h}" for h in hits)
    if out:
        return Finding(
            "duplicate_type_names",
        "동명이의 계약 타입이 있다 — 잘못 임포트해도 타입검사를 통과한다.", out)
    return None


CHECKS = [
    check_optional_safety_gate,
    check_require_mandate_false,
    check_executor_gate,
    check_freshness_tracker,
    check_xfail_in_gate_tests,
    check_tenant_fk_to_users,
    check_rls_enable_without_force,
    check_duplicate_type_names,
]


# --------------------------------------------------------------------------- 실행

def run() -> list[Finding]:
    out: list[Finding] = []
    for fn in CHECKS:
        try:
            f = fn()
        except Exception as exc:  # noqa: BLE001 - 검사 하나가 죽어도 나머지는 돌아야 한다
            f = Finding(f"checker_error_{fn.__name__}", f"검사 자체가 실패했다: {exc!r}", [])
        if f is not None:
            out.append(f)
    return out


def main() -> int:
    # 콘솔이 cp949여도 죽지 않는다 — 검사 결과가 인코딩 때문에 사라지면 안 된다.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    ap.add_argument("--json", action="store_true", help="결과를 JSON으로 출력")
    a = ap.parse_args()

    findings = run()
    found = {f.code: f for f in findings}
    try:
        baseline = json.loads(a.baseline.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        baseline = {"open": {}}
    known = baseline.get("open", {})

    new = [c for c in found if c not in known]
    fixed = [c for c in known if c not in found]

    if a.json:
        print(json.dumps({"found": {c: f.evidence[:20] for c, f in found.items()},
                          "new": new, "fixed": fixed}, ensure_ascii=False, indent=2))

    for code, f in found.items():
        mark = "신규" if code in new else f"기지({known.get(code, {}).get('task', '-')})"
        print(f"[{mark}] {code}: {f.detail}")
        for e in f.evidence[:6]:
            print(f"    {e}")
        if len(f.evidence) > 6:
            print(f"    ... 외 {len(f.evidence) - 6}건")

    rc = 0
    if new:
        print(f"\nFAIL: 베이스라인에 없는 결함 {len(new)}건 — {', '.join(new)}")
        print("고치거나, 담당 task를 정해 audit-baseline.json에 등록한다.")
        rc = 1
    if fixed:
        print(f"\nFAIL: 해소된 항목이 베이스라인에 남아 있다 — {', '.join(fixed)}")
        print("audit-baseline.json에서 지운다(한 번 닫힌 항목은 다시 열릴 수 없다).")
        rc = 1
    if rc == 0:
        print(f"OK: 감사 회귀 검사 통과 (열린 항목 {len(found)}건, 전부 등록됨)")
    return rc


if __name__ == "__main__":
    sys.exit(main())
