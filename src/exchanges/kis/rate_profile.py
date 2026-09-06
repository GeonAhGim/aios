"""BR-2 — KIS 호출 한도 프로파일: (계좌유형 x TR 그룹) -> TokenBucket 설정표.

Spec: docs/specs/L4_execution_oms_and_exchange_v1.0.md §3.2
VenueCapabilityProfile.rate_limits, ADR-2026-09-06-I D4.

D4: "exchanges/common/transport.py의 TokenBucket을 재사용하고(신설 금지)" —
이 모듈은 새 레이트리미터를 만들지 않는다. `rate_limiter.TokenBucket`에
그대로 넘길 (rate_per_sec, burst) 값만 계좌유형·TR그룹별 표로 관리한다.
전수 구현은 호출 종류가 계속 늘어나는 일이라, 한도를 어댑터 코드 여기저기에
흩으면 나중에 전수 수정이 된다 — 표 하나만 고치면 되게 한다.

TR -> 그룹 매핑은 BR-1(task-1779)이 이미 기계 추출해 커밋한
`docs/design/kis_tr_reference.json`의 domain 분류를 그대로 재사용한다 —
여기서 또 다른 taxonomy를 만들면 KIS_TR_COVERAGE.md 매트릭스와 어긋난다.

미검증(§10 정직 표기, `verified` 필드는 스펙 §3.2 VenueCapabilityProfile과
동일 어휘: LIVE_VERIFIED/DOC_ONLY/ESTIMATED) — 정확한 초당 한도는 실계좌로
라이브 재확인하지 못했다. 아래 값(실전 20건/초, 모의 2건/초)은 KIS
공식문서·커뮤니티에 통용되는 상한을 그대로 썼다(DOC_ONLY). TR 그룹 간
세분 한도는 KIS가 공개하지 않아 그룹마다 같은 값을 쓴다 — 그룹 축은
향후 실측/공식 문서로 확인되면 이 표만 갱신하면 되도록 유지한다.
`burst == rate`로 둬 순간 폭주를 허용하지 않는다(사고 방지가 상한을
정확히 맞추는 것보다 우선).
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from pathlib import Path
from typing import Literal

from src.exchanges.common.rate_limiter import TokenBucket
from src.exchanges.common.transport import RateLimitWaitObserver

_REFERENCE_PATH = Path(__file__).resolve().parents[3] / "docs" / "design" / "kis_tr_reference.json"

Verified = Literal["LIVE_VERIFIED", "DOC_ONLY", "ESTIMATED"]


class KisAccountType(str, Enum):
    REAL = "real"
    PAPER = "paper"


@dataclass(frozen=True)
class RateLimitSpec:
    rate_per_sec: float
    burst: float
    verified: Verified

    def new_bucket(self, *, observer: RateLimitWaitObserver | None = None) -> TokenBucket:
        if observer is None:
            return TokenBucket(self.rate_per_sec, self.burst)

        async def _observed_sleep(seconds: float) -> None:
            observer.record_wait()
            await asyncio.sleep(seconds)

        return TokenBucket(self.rate_per_sec, self.burst, sleep=_observed_sleep)


# BR-1(kis_tr_reference.json)이 기계 추출한 도메인 분류와 동일 taxonomy.
_KNOWN_GROUPS = (
    "domestic_stock",
    "domestic_bond",
    "domestic_futureoption",
    "elw",
    "etfetn",
    "overseas_stock",
    "overseas_futureoption",
)

_PROFILE: dict[KisAccountType, dict[str, RateLimitSpec]] = {
    KisAccountType.REAL: {g: RateLimitSpec(20.0, 20.0, "DOC_ONLY") for g in _KNOWN_GROUPS},
    KisAccountType.PAPER: {g: RateLimitSpec(2.0, 2.0, "DOC_ONLY") for g in _KNOWN_GROUPS},
}

# 프로파일 표에 없는 TR·TR그룹(신규 TR·오탈자 등)은 가장 보수적인 한도로
# 취급한다(ADR D4 DoD) — 실전/모의 모두 알려진 최소값보다 더 낮춘 값.
_CONSERVATIVE_FALLBACK: dict[KisAccountType, RateLimitSpec] = {
    KisAccountType.REAL: RateLimitSpec(1.0, 1.0, "ESTIMATED"),
    KisAccountType.PAPER: RateLimitSpec(1.0, 1.0, "ESTIMATED"),
}


@lru_cache(maxsize=1)
def _tr_id_to_group() -> Mapping[str, str]:
    data = json.loads(_REFERENCE_PATH.read_text(encoding="utf-8"))
    return {row["tr_id"]: row["domain"] for row in data["trs"]}


def tr_group_for(tr_id: str) -> str | None:
    """`tr_id`가 속한 BR-1 도메인 그룹. 기준 목록(`kis_tr_reference.json`)에
    없는 TR이면 None을 돌려준다 — `get_rate_limit`이 이를 프로파일에 없는
    그룹과 동일하게(가장 보수적 한도) 처리한다."""
    return _tr_id_to_group().get(tr_id)


def get_rate_limit(account_type: KisAccountType, tr_group: str | None) -> RateLimitSpec:
    """계좌유형·TR그룹에 대한 한도. 표에 없는 그룹(None 포함)은 가장
    보수적 한도로 취급한다(ADR-2026-09-06-I D4 DoD)."""
    if tr_group is None:
        return _CONSERVATIVE_FALLBACK[account_type]
    return _PROFILE[account_type].get(tr_group, _CONSERVATIVE_FALLBACK[account_type])


def build_token_bucket(
    account_type: KisAccountType,
    tr_id: str,
    *,
    observer: RateLimitWaitObserver | None = None,
) -> TokenBucket:
    """`tr_id`를 그룹으로 변환해 계좌유형에 맞는 `TokenBucket`을 새로
    만든다. 호출부가 TR 그룹별로 독립된 버킷을 유지하려면 그룹당 1회만
    불러 `ResilientTransport(rate_limiter=...)`에 캐싱해 넘긴다.

    `observer`를 주면(선택) 버킷이 실제로 대기할 때마다
    `observer.record_wait()`가 호출된다 — 한도 초과가 429가 아니라
    대기·재시도로 흡수됐다는 사실을 지표로 관측하기 위함(DoD)."""
    return get_rate_limit(account_type, tr_group_for(tr_id)).new_bucket(observer=observer)
