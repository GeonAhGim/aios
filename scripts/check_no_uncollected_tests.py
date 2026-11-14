"""수집되지 않는 모듈 안의 테스트 함수 차단 게이트 -- 2026-11-14.

task-24922(QA, 머지 큐 우회 직접 푸시)가 tests/support/deep_downgrade.py 안에
test_ 함수 6개를 넣어 main이 적색이 됐다. 판정은
`tests/unit/meta/test_no_uncollected_tests.py`의 `_offenders`를 그대로 쓴다(재구현 금지).
그 메타 테스트는 머지 큐 배치·full에서만 돌아서, 큐를 거치지 않는 레인(QA·리뷰·외부 엔진)은
push 시점에 이 스크립트로 같은 규칙을 먼저 만난다. AST만 읽어 1초 미만이다.

사용: `python scripts/check_no_uncollected_tests.py`. 종료코드 0=통과, 1=위반.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "tests" / "unit" / "meta" / "test_no_uncollected_tests.py"


def find_offenders(root: Path = ROOT) -> dict[str, list[str]]:
    spec = importlib.util.spec_from_file_location("_uncollected_guard", META)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {META}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._offenders(root / "tests")  # noqa: SLF001 -- 판정 로직 재사용이 목적


def main() -> int:
    if not META.is_file():
        print("SKIP: meta guard missing")
        return 0
    offenders = find_offenders()
    if not offenders:
        print("OK: no test functions in uncollected modules")
        return 0
    print("FAIL: test functions in modules pytest does not collect (move them into a test_*.py):")
    for rel, names in offenders.items():
        print(f"  tests/{rel}: {', '.join(names)}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
