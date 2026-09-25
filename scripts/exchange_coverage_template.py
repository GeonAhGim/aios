"""BR-18(task-6696) 커버리지 매트릭스 템플릿 -- 새 거래소 어댑터 하나를
`scripts/check_exchange_spi.py`와 동일한 검사 행렬로 단독 검증하고 싶을 때
복사해서 쓰는 템플릿이다. 이 파일 자체는 CI 게이트가 아니다(어떤
`SUPPORTED_EXCHANGES` 항목도 가리키지 않는 자리표시자 import를 담고 있어
그대로 실행하면 실패한다) -- `docs/exchanges/ADDING_AN_EXCHANGE.md` §4 참고.

사용법: 이 파일을 `scripts/check_exchange_spi_<venue>.py`로 복사한 뒤
`_TEMPLATE_VENUE`/`_TEMPLATE_ADAPTER_CLS`/`_TEMPLATE_PROFILE` 세 자리만
새 거래소의 실제 값으로 바꾼다. 검사 로직 자체(`_is_default`,
`_check_entry`)는 `check_exchange_spi.py`의 로직을 그대로 재사용한다 --
로직을 복제하지 말고 import해서 쓴다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_exchange_spi import (  # noqa: E402
    AdapterEntry,
    Violation,
    _check_entry,
    spi_surface,
)

# --- 아래 세 줄만 새 거래소에 맞게 바꾼다 -----------------------------------
_TEMPLATE_VENUE = "my_exchange"
_TEMPLATE_ADAPTER_IMPORT = "src.exchanges.my_exchange.adapter.MyExchangeAdapter"
_TEMPLATE_PROFILE_IMPORT = "src.exchanges.my_exchange.venue_profile.MY_EXCHANGE_PROFILE"
# ---------------------------------------------------------------------------


def _import_by_path(dotted: str):
    module_path, _, attr = dotted.rpartition(".")
    module = __import__(module_path, fromlist=[attr])
    return getattr(module, attr)


def build_template_entry() -> AdapterEntry:
    adapter_cls = _import_by_path(_TEMPLATE_ADAPTER_IMPORT)
    profile = _import_by_path(_TEMPLATE_PROFILE_IMPORT)
    return AdapterEntry(_TEMPLATE_VENUE, adapter_cls, profile)


def check_template() -> list[Violation]:
    return _check_entry(build_template_entry())


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    surface = spi_surface()
    print(f"exchange_coverage_template: SPI {len(surface)}개 메서드, 대상 {_TEMPLATE_VENUE}")
    violations = check_template()
    if violations:
        for v in violations:
            print(str(v))
        print(f"exchange_coverage_template: {len(violations)}건 위반")
        return 1
    print("exchange_coverage_template: 위반 0건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
