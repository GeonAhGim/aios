"""MP-11 — 마켓플레이스 리스팅 성과 독립 재현 검증.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §3.5
(`ReputationScore.reproduced_backtests`), §4.3(평판 산식이 `reproduced_
backtests`를 소비), ADR-2026-09-06-G §9 MP-11.

§4.3 평판 산식은 `reproduced_backtests`를 입력으로 소비하지만, 그 값을
"검증된 재현"으로 만드는 리프가 지금까지 없었다 — 판매자가 리스팅에
게시한 Sharpe·DSR 등 성과 주장을 플랫폼이 실제로 다시 돌려 확인하지
않으면, 그 숫자는 자기 신고에 불과하다(현 상태는 TradingView보다 나쁘다
— TradingView는 애초에 그런 주장을 하지 않는다).

이 모듈은 순수 비교 로직이다 — I/O 없음. "판매자가 어떤 해시를 주장했는가"
(리스팅 저장소)와 "플랫폼이 어떻게 그 백테스트를 다시 돌렸는가"
(`run_backtest`, BT-9 `reproducibility_key`로 같은 아티팩트·구간·설정임을
이미 확인했다고 가정)는 호출자 책임이다. 이 모듈은 두 결과를 받아
바이트 단위로 대조할 뿐이다 — BT-19 `parity_harness.py`와 같은 경계
설계(비교만, 재생은 다른 리프).

Fail-closed: 주장 해시가 비어 있거나 재현 결과가 비어 있으면(빈
`equity_curve`) 검증 통과로 위장하지 않고 즉시 `ValueError`로 거부한다.
불일치 시 `MP_UNVERIFIED_RESULT`로 표시하고, `count_verified_backtests()`
는 그 건을 평판 가산 대상에서 제외한다 — 의심되면 가산하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from src.foundation.backtest.domain.models import BacktestResult

MP_UNVERIFIED_RESULT: Final = "MP_UNVERIFIED_RESULT"
RESULT_HASH_SCHEMA: Final = "marketplace-listing-result-hash-1"


def compute_result_hash(result: BacktestResult) -> str:
    """`BacktestResult`(체결·자본곡선·지표) → sha256 hex(64자) 정준 해시.

    `config`와 `warnings`는 입력·진단 정보라 해시에서 뺀다 — 리스팅이
    주장하는 것은 "이 설정으로 실행한 결과"이지 설정 자체가 아니고
    (설정 동일성은 BT-9 `reproducibility_key`가 이미 별도로 보증한다),
    경고 문구는 엔진 버전에 따라 문구가 바뀔 수 있어 성과 주장과 무관하다.
    정준 직렬화 규칙(정렬된 키, 고정 구분자, `ensure_ascii=True`)은 BT-9
    `domain/reproducibility.py`와 동일하다 — 재현 키 계열 해시가 리프마다
    다른 정규화를 쓰면 "같은 입력=같은 해시" 계약의 강도가 흔들린다.
    """
    if not result.equity_curve:
        raise ValueError("compute_result_hash: 빈 equity_curve로는 해시를 낼 수 없습니다")
    payload = {
        "schema": RESULT_HASH_SCHEMA,
        "fills": [f.model_dump(mode="json") for f in result.fills],
        "equity_curve": [e.model_dump(mode="json") for e in result.equity_curve],
        "metrics": result.metrics.model_dump(mode="json"),
    }
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ListingBacktestClaim:
    """판매자가 리스팅에 게시한 성과 주장 — §3.5 `ScriptListing`의
    `result_hash`에 해당(계약 자체는 MP-1이 아직 확정하지 않았으므로,
    이 리프가 검증에 필요한 최소 필드만 정의한다)."""

    listing_id: int
    claimed_result_hash: str

    def __post_init__(self) -> None:
        if not self.claimed_result_hash or not self.claimed_result_hash.strip():
            raise ValueError(
                f"ListingBacktestClaim(listing_id={self.listing_id}): "
                "claimed_result_hash가 비어 있습니다"
            )


@dataclass(frozen=True)
class VerificationOutcome:
    listing_id: int
    is_verified: bool
    claimed_result_hash: str
    reproduced_result_hash: str
    error_code: str | None

    def raise_if_unverified(self) -> None:
        """게이트로 쓸 때(평판 가산 직전 등) — 불일치면 즉시 예외로 막는다."""
        if not self.is_verified:
            raise ListingUnverifiedError(self)


class ListingUnverifiedError(Exception):
    def __init__(self, outcome: VerificationOutcome) -> None:
        self.outcome = outcome
        super().__init__(
            f"{MP_UNVERIFIED_RESULT}: listing_id={outcome.listing_id} "
            f"주장 해시={outcome.claimed_result_hash!r} != "
            f"재현 해시={outcome.reproduced_result_hash!r}"
        )


def verify_listing_backtest(
    claim: ListingBacktestClaim,
    reproduced_result: BacktestResult,
) -> VerificationOutcome:
    """판매자 주장 해시와 플랫폼이 독립 재현한 `BacktestResult`의 해시를
    비트 단위(sha256 hex 전체 일치)로 대조한다.

    일치 → `is_verified=True`, `error_code=None` — `reputation.
    reproduced_backtests` 가산 허용. 불일치 → `is_verified=False`,
    `error_code=MP_UNVERIFIED_RESULT` — 호출자는 이 건을 가산에 반영하면
    안 된다(`count_verified_backtests()` 참고).
    """
    reproduced_hash = compute_result_hash(reproduced_result)
    is_verified = claim.claimed_result_hash == reproduced_hash
    return VerificationOutcome(
        listing_id=claim.listing_id,
        is_verified=is_verified,
        claimed_result_hash=claim.claimed_result_hash,
        reproduced_result_hash=reproduced_hash,
        error_code=None if is_verified else MP_UNVERIFIED_RESULT,
    )


def count_verified_backtests(outcomes: Sequence[VerificationOutcome]) -> int:
    """§4.3 평판 산식의 `reproduced_backtests` 입력 — 검증 통과 건만 센다.

    `MP_UNVERIFIED_RESULT`로 판정된 건은 위조인지 단순 엔진 드리프트인지
    가리지 않고 전부 제외한다(fail-closed) — 의심스러운 재현을 평판에
    반영하면 이 리프가 막으려는 문제(자기 신고 성과의 무비판적 수용)를
    그대로 재현한다.
    """
    return sum(1 for outcome in outcomes if outcome.is_verified)


__all__ = [
    "MP_UNVERIFIED_RESULT",
    "RESULT_HASH_SCHEMA",
    "ListingBacktestClaim",
    "ListingUnverifiedError",
    "VerificationOutcome",
    "compute_result_hash",
    "count_verified_backtests",
    "verify_listing_backtest",
]
