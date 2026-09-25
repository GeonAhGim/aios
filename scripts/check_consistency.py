"""CONSIST-1 배선 일관성 검사 스위트 CLI -- task-2850, 모듈 분리 task-3725(CONSIST-1c).

대시보드 파서가 "§9"를 가정해 명세 1종을 놓친 사고처럼, 코드에도 "당연히
그렇겠지"가 박힌 배선 누락이 있다. `scripts/consistency/` 패키지가 검사군별로
그런 12종의 기계 판정 가능한 누락을 정적 분석만으로(임포트·DB·네트워크 없음)
찾는다:

  1. router_unregistered       -- src/api/routers/*의 APIRouter가 앱에 include 안 됨
  2. port_method_unimplemented -- ABC 포트 메서드가 서브클래스에 없거나
                                   raise NotImplementedError인데 ratchet-allow 없음
  2b. port_protocol_unimplemented -- 같은 컨텍스트(ports/-adapters/ 형제 디렉터리)의
                                   Protocol 포트 메서드가, 이름이 그 Protocol로 끝나는
                                   adapters/ 클래스에 없거나 raise NotImplementedError인데
                                   ratchet-allow 없음(task-3724, CONSIST-1b)
  3. env_key_undocumented      -- os.environ 접근 키가 .env.example에 없음
  4. feature_flag_undocumented -- flag_enabled(...) 이름이 .env.example에 없음
  5. event_type_unconsumed     -- publish()된 topic에 subscribe() 소비자가 없음
  6. migration_hygiene         -- downgrade()가 비어 있거나 head가 2개 이상
  7. openapi_client_mismatch   -- OpenAPI 경로 ↔ frontend apiRoutes 양방향 불일치
  8. spec_leaf_untraced        -- 명세 §9 리프 ID가 코드/커밋 어디에도 없음
  9. naive_datetime            -- datetime.now()/utcnow()가 tz 없이 호출됨
  10. money_float              -- 금액류 이름(amount/price/fee 등)이 float로 선언됨
  11. symbol_id_assembly       -- symbol/instrument_id를 f-string/concat/join으로 직접 조립
  12. spec_template_incomplete -- 명세 파일에 "## 9." 리프 목록 또는 미확정 절이 없음
  13. authority_duplication    -- 같은 bounded context에서 같은 "authority"(멱등키 등)를
                                   2개 이상의 파일이 각자 raw assembly로 재조립(RATCHET-2,
                                   task-3256)

검사 로직은 `scripts/consistency/{common,wiring,contracts,time_money,
spec_trace,registry}.py`에 검사군별로 있다 -- 이 파일은 CLI 진입점(argparse ·
래칫 판정 · exit code)만 유지한다. `check_code_ratchets.py`와 같은 래칫 방식:
`consistency-baseline.json`에 지표별 현재 위반 수를 기록하고, 이후 실행에서 그
수가 늘면 exit 2(적색). 각 검사는 과탐(false positive)을 baseline이 흡수하므로
완벽한 판정을 목표하지 않는다 -- "늘어나면 막는다"가 목표다.

사용: `python scripts/check_consistency.py [--update] [--root PATH] [--baseline PATH]`.
"""

from __future__ import annotations

# ruff: noqa: E402 -- sys.path 보정(직접 실행 시 scripts/consistency.* 절대
# 임포트를 가능하게 함)이 다른 임포트보다 먼저 실행돼야 한다.
import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    # `python scripts/check_consistency.py` 직접 실행 시 sys.path[0]은
    # scripts/가 된다 -- `scripts.consistency.*`를 절대 임포트로 쓰려면 저장소
    # 루트가 sys.path에 있어야 한다(ci_recheck 호출 경로: `python
    # scripts/check_consistency.py`, 모듈 형태 `-m` 아님).
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.consistency.common import DEFAULT_BASELINE, ROOT
from scripts.consistency.contracts import (
    check_env_keys,
    check_event_consumers,
    check_feature_flags,
    check_migrations,
    check_openapi_frontend,
)
from scripts.consistency.registry import (
    METRICS,
    ConsistencyError,
    counts_of,
    read_baseline,
    scan_all,
    write_baseline,
)
from scripts.consistency.spec_trace import (
    _collect_spec_leaf_ids,
    _expand_leaf_ids,
    _git_commit_subjects,
    check_spec_leaf_traceability,
    check_spec_template,
)
from scripts.consistency.time_money import (
    check_authority_duplication,
    check_money_float,
    check_naive_datetime,
    check_symbol_id_assembly,
)
from scripts.consistency.wiring import (
    check_port_implementations,
    check_port_protocol_implementations,
    check_router_wiring,
)

__all__ = [
    "ROOT",
    "DEFAULT_BASELINE",
    "METRICS",
    "ConsistencyError",
    "check_router_wiring",
    "check_port_implementations",
    "check_port_protocol_implementations",
    "check_env_keys",
    "check_feature_flags",
    "check_event_consumers",
    "check_migrations",
    "check_openapi_frontend",
    "check_spec_leaf_traceability",
    "check_naive_datetime",
    "check_money_float",
    "check_symbol_id_assembly",
    "check_spec_template",
    "check_authority_duplication",
    "_expand_leaf_ids",
    "_collect_spec_leaf_ids",
    "_git_commit_subjects",
    "scan_all",
    "counts_of",
    "read_baseline",
    "write_baseline",
    "main",
]


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--update", action="store_true", help="감소분을 baseline 파일에 반영")
    parser.add_argument("--top", type=int, default=10, help="증가한 지표별로 보여줄 목록 개수")
    args = parser.parse_args(argv)

    try:
        baseline = read_baseline(args.baseline)
    except ConsistencyError as exc:
        print(f"FAIL: {exc}")
        return 1

    hits = scan_all(args.root)
    current = counts_of(hits)

    if baseline is None:
        write_baseline(args.baseline, current)
        print(f"BASELINE 초기화: {current} -> {args.baseline}")
        return 0

    increased = {m: (baseline[m], current[m]) for m in METRICS if current[m] > baseline[m]}
    if increased:
        for metric, (before, after) in increased.items():
            print(f"FAIL: {metric} {before}개 -> {after}개 (증가)")
            for rel, lineno in hits[metric][: args.top]:
                print(f"    {rel}:{lineno}")
        return 2

    decreased = {m for m in METRICS if current[m] < baseline[m]}
    if decreased and args.update:
        write_baseline(args.baseline, current)
        print(f"OK: 감소, baseline 갱신 {baseline} -> {current}")
        return 0

    if decreased:
        print(f"OK: 감소했으나 baseline 유지(--update로 반영) {baseline} (현재 {current})")
        return 0

    print(f"OK: {current} (baseline {baseline})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
