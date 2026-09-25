"""BR-18(task-6696) 정적 검사 -- 등록된 거래소 어댑터가 SPI(`ExchangeAdapter`)를
전 메서드 구현(또는 명시적 `_unsupported`)했는지, `venue_profile()`이 선언하는
capability와 실제 구현이 일치하는지 검사한다.

Spec: ADR-2026-09-06-I("브로커 우선, 100% 커버리지"), ADR-2026-09-24-A Decision 5,
docs/exchanges/ADDING_AN_EXCHANGE.md.

ExchangeAdapter는 14개 추상 메서드/프로퍼티(서브클래스가 인스턴스화 가능하다는
사실 자체로 ABCMeta가 구현을 강제)와 5개의 L4-13 확장 조회
(`get_open_orders`/`get_fills`/`find_order_by_client_id`/`venue_profile`/
`subscribe_order_stream`) 기본 구현을 갖는다 -- 기본 구현은 전부
`UnsupportedCapabilityError`를 던지는 명시적 "미지원" 신호이며, 그 자체로는
위반이 아니다(`NotImplementedError`나 조용한 `[]`/`None`이 아니라는 게 핵심).

이 게이트가 실제로 잡는 결함은 둘이다:
  1. venue 모듈(`exchanges/<venue>/venue_profile.py`)에 `*_PROFILE` 상수가
     있는데 어댑터의 `venue_profile()`이 그걸 반환하도록 배선돼 있지 않은 경우
     (ABC 기본값에 조용히 가려짐 -- 이 리프에서 KIS/NH 양쪽에 실존했던 결함).
  2. `venue_profile().supports_ws_orders=True`인데 `subscribe_order_stream`이
     ABC 기본값(미지원)에 머물러 capability 선언과 실제 구현이 모순인 경우.

MRO 기반 IMPLEMENTED/DEFAULT 판정은 클래스 속성 동일성
(`getattr(cls, name) is not getattr(ExchangeAdapter, name)`)으로만 하고
인스턴스화하지 않는다 -- 실계좌 자격증명 없이도 돌아가야 하고(DB 접속 없음),
믹스인 상속 때문에 텍스트/AST만으로는 신뢰성 있게 못 가른다
(`tests/unit/exchanges/test_adapter_abc_defaults.py`가 이미 쓰는 관례).

paper_sim(`PaperSimulatorAdapter`)은 정적 `*_PROFILE` 상수가 없고
`profile_for(reference)`로 참조 거래소 프로파일을 복제한다 -- "4번째/5번째
거래소로 가정한 드라이런"(BR-18 DoD)은 bitget 프로파일을 참조로 넘겨
같은 검사 행렬을 통과시키는 것으로 만족한다.

사용: `python scripts/check_exchange_spi.py` (저장소 루트에서).
종료코드 0=위반 없음, 1=위반 있음(각 위반을 표준출력에 나열).
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.exchanges.bitget.adapter import BitgetAdapter  # noqa: E402
from src.exchanges.bitget.venue_profile import BITGET_SPOT_PROFILE  # noqa: E402
from src.exchanges.common.adapter import ExchangeAdapter  # noqa: E402
from src.exchanges.kis.adapter import KISAdapter  # noqa: E402
from src.exchanges.kis.venue_profile import KIS_KR_EQUITY_PROFILE  # noqa: E402
from src.exchanges.nh.adapter import NHAdapter  # noqa: E402
from src.exchanges.nh.venue_profile import NH_KR_EQUITY_PROFILE  # noqa: E402
from src.exchanges.paper.simulator_adapter import PaperSimulatorAdapter  # noqa: E402
from src.exchanges.paper.venue_profile import profile_for  # noqa: E402
from src.services.oms.domain.venue_profile import VenueCapabilityProfile  # noqa: E402

# L4-13 확장 조회 -- ABC 기본값이 `_unsupported`를 던지는 다섯 메서드.
_OPTIONAL_METHODS = (
    "get_open_orders",
    "get_fills",
    "find_order_by_client_id",
    "venue_profile",
    "subscribe_order_stream",
)


def spi_surface() -> tuple[str, ...]:
    """`ExchangeAdapter`의 공개 메서드/프로퍼티 이름을 동적으로 나열한다 --
    하드코딩된 개수 대신 ABC 자체를 진실 소스로 삼아 ABC가 바뀌면 이 게이트도
    같이 갱신되게 한다."""
    names = []
    for name, value in vars(ExchangeAdapter).items():
        if name.startswith("_"):
            continue
        if callable(value) or isinstance(value, property):
            names.append(name)
    return tuple(sorted(names))


@dataclass(frozen=True)
class AdapterEntry:
    venue: str
    adapter_cls: type[ExchangeAdapter]
    profile: VenueCapabilityProfile


@dataclass(frozen=True)
class Violation:
    venue: str
    reason: str

    def __str__(self) -> str:
        return f"{self.venue}: {self.reason}"


def _is_default(adapter_cls: type[ExchangeAdapter], name: str) -> bool:
    return getattr(adapter_cls, name) is getattr(ExchangeAdapter, name)


def _check_entry(entry: AdapterEntry) -> list[Violation]:
    violations: list[Violation] = []
    cls = entry.adapter_cls

    # (1) 추상 메서드 14종 회귀 감시 -- 서브클래스가 인스턴스화 가능한 구체
    # 클래스라는 사실 자체가 ABCMeta의 강제를 증명하지만, 방어적으로 다시 확인.
    if cls.__abstractmethods__:
        violations.append(
            Violation(
                entry.venue,
                f"추상 메서드 미구현: {sorted(cls.__abstractmethods__)}",
            )
        )

    # (2) venue_profile.py의 프로파일 상수가 adapter.venue_profile()에
    # 배선되지 않아 ABC 기본값(UnsupportedCapabilityError)에 가려진 경우.
    if _is_default(cls, "venue_profile"):
        violations.append(
            Violation(
                entry.venue,
                "venue_profile.py에 프로파일 상수가 있지만 adapter.venue_profile()"
                "이 ABC 기본값(UnsupportedCapabilityError)에 머물러 있다 -- 배선 누락",
            )
        )

    # (3) capability<->구현 교차검증: supports_ws_orders 선언과
    # subscribe_order_stream 구현 상태가 모순이면 안 된다.
    if entry.profile.supports_ws_orders and _is_default(cls, "subscribe_order_stream"):
        violations.append(
            Violation(
                entry.venue,
                "venue_profile().supports_ws_orders=True인데 subscribe_order_stream()"
                "은 ABC 기본값(미지원)이다 -- capability 선언과 구현 불일치",
            )
        )
    return violations


def adapter_matrix() -> list[AdapterEntry]:
    return [
        AdapterEntry("bitget", BitgetAdapter, BITGET_SPOT_PROFILE),
        AdapterEntry("kis", KISAdapter, KIS_KR_EQUITY_PROFILE),
        AdapterEntry("nh", NHAdapter, NH_KR_EQUITY_PROFILE),
        # BR-18 드라이런 -- paper_sim을 "4번째 거래소"로 가정해 같은 검사를
        # 통과시킨다(DoD). paper는 정적 프로파일 상수가 없고
        # `profile_for(reference)`로 참조 거래소 프로파일을 복제하므로,
        # 대표 참조로 bitget 프로파일을 넘긴다.
        AdapterEntry(
            "paper_sim(dry-run)",
            PaperSimulatorAdapter,
            profile_for(BITGET_SPOT_PROFILE),
        ),
    ]


def check() -> list[Violation]:
    violations: list[Violation] = []
    for entry in adapter_matrix():
        violations.extend(_check_entry(entry))
    return violations


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    surface = spi_surface()
    venues = [e.venue for e in adapter_matrix()]
    print(f"check_exchange_spi: SPI {len(surface)}개 메서드, 대상 거래소 {venues}")
    violations = check()
    if violations:
        for v in violations:
            print(str(v))
        print(f"check_exchange_spi: {len(violations)}건 위반")
        return 1
    print("check_exchange_spi: 위반 0건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
