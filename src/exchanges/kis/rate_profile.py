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

BR-2b(task-3458, review 3306 REJECT #2) — `build_token_bucket` now always
returns the **same** `TokenBucket` instance for a given (account_type,
tr_group). This invariant used to live only in a docstring ("callers should
fetch it once per group and cache it") — when the caller (`oauth_client.py`)
fetched a fresh bucket on every request instead, the per-group throughput cap
was effectively meaningless. `_BUCKET_REGISTRY` (keyed by (account_type,
tr_group), guarded by `threading.Lock`) now enforces that invariant in code:
even if a caller forgets to cache the result (exactly the bug being fixed),
the limit itself still holds.
"""

from __future__ import annotations

import asyncio
import json
import threading
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


# BR-2b(task-3458) — cache-singleton registry keyed by (account_type,
# tr_group). If `build_token_bucket` handed out a fresh bucket on every call,
# the throughput cap would depend entirely on whether the caller happens to
# cache it (review 3306 REJECT #2) — the registry removes that dependency.
# `threading.Lock` rather than `asyncio.Lock`: an asyncio.Lock is bound to
# the event loop it was created on and breaks if awaited from outside that
# loop (sync code, another thread), while `threading.Lock` is safe from
# either.
_BUCKET_REGISTRY: dict[tuple[KisAccountType, str | None], TokenBucket] = {}
_BUCKET_REGISTRY_LOCK = threading.Lock()


def build_token_bucket(
    account_type: KisAccountType,
    tr_id: str,
    *,
    observer: RateLimitWaitObserver | None = None,
) -> TokenBucket:
    """Resolves `tr_id` to its group and returns the `TokenBucket` for that
    account type.

    Always returns the same `TokenBucket` instance for a given
    (account_type, tr_group) pair (`_BUCKET_REGISTRY`, thread-safe) — the
    per-group throughput cap is enforced regardless of whether the caller
    caches the result. `observer` is only honored the **first** time a
    bucket is created for a given key — an already-cached bucket's sleep
    wrapper is not swapped for a different observer passed in on a later
    call (the singleton keeps its first owner's observability wiring)."""
    group = tr_group_for(tr_id)
    key = (account_type, group)
    with _BUCKET_REGISTRY_LOCK:
        bucket = _BUCKET_REGISTRY.get(key)
        if bucket is None:
            bucket = get_rate_limit(account_type, group).new_bucket(observer=observer)
            _BUCKET_REGISTRY[key] = bucket
        return bucket


def reset_token_bucket_registry_for_test() -> None:
    """Test-only — clears `_BUCKET_REGISTRY`.

    Since the registry is a process-wide singleton, tests need to reset it
    between runs to stay order-independent. Never call this from production
    code — resetting it at runtime would wipe every group's accumulated
    consumption, reproducing the exact bug this module exists to prevent
    (per-group throughput cap silently defeated)."""
    with _BUCKET_REGISTRY_LOCK:
        _BUCKET_REGISTRY.clear()
