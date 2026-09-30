"""MP-11 DEEPEN(task-9374) -- negative/실패주입 보강.

원 리프 task-6704(고아 산출물 회수)가 패키지 마커만 남기고 비워 둔
파일이다. `test_verify_listing_backtest.py`가 이미 이 디렉터리의 주된
verified/forged/hash-변경 증거를 갖고 있으므로, 여기서는 그 파일이 다루지
않은 세 공백만 메운다:

1. `ListingBacktestClaim.__post_init__`은 완전히 빈 문자열만 실측됐다 --
   공백만으로 된 해시(`"   "`)도 `.strip()` 검사로 동일하게 거부되는지는
   아직 증명되지 않았다.
2. `verify_listing_backtest`의 "bit-for-bit" 계약은 완전히 다른 위조
   해시로만 실측됐다 -- 대소문자만 다른(그러나 sha256 hex는 항상
   소문자인) 변형처럼, "거의 맞는" 값도 대소문자 그대로 정확히 일치해야만
   통과하는지는 아직 증명되지 않았다.
3. `compute_result_hash`가 fills 안의 미세한 값 차이(가격 1틱)도 놓치지
   않고 다른 해시를 내는지 -- 기존 `test_different_reproduced_result_
   changes_hash`는 완전히 다른 config(초기자본)로만 차이를 만든다.

실패주입: `compute_result_hash`가 직렬화 계층(`hashlib.sha256`)에서 터지는
예상치 못한 의존성 예외를 삼키지 않고 그대로 전파하는지(fail-closed).
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.foundation.marketplace.application.verify_listing_backtest import (
    MP_UNVERIFIED_RESULT,
    ListingBacktestClaim,
    compute_result_hash,
    verify_listing_backtest,
)
from tests.integration.marketplace.test_verify_listing_backtest import _platform_reproduction

# ---------------------------------------------------------------------------
# Negative tests
# ---------------------------------------------------------------------------


def test_claim_rejects_whitespace_only_hash() -> None:
    """`__post_init__`의 `not self.claimed_result_hash or not
    self.claimed_result_hash.strip()` -- 기존 테스트는 완전히 빈 문자열만
    실측한다. 공백만으로 채워진 값도 "실질적으로 비어 있음"으로 동일하게
    거부해야 fail-closed 계약이 성립한다."""
    with pytest.raises(ValueError, match="claimed_result_hash"):
        ListingBacktestClaim(listing_id=1, claimed_result_hash="   ")


def test_verify_rejects_uppercase_variant_of_correct_hash() -> None:
    """`verify_listing_backtest`는 대소문자를 구분하지 않는 비교로
    몰래 완화되면 안 된다 -- sha256 hex는 항상 소문자만 내므로, 대문자로
    바꾼 "거의 맞는" 값도 정확히 같은 바이트열이 아니면 그대로 거부돼야
    한다."""
    reproduced = _platform_reproduction()
    correct_hash = compute_result_hash(reproduced)
    uppercased_claim = ListingBacktestClaim(listing_id=1, claimed_result_hash=correct_hash.upper())

    outcome = verify_listing_backtest(uppercased_claim, reproduced)

    assert outcome.is_verified is False
    assert outcome.error_code == MP_UNVERIFIED_RESULT


def test_compute_result_hash_detects_single_fill_price_tick_difference() -> None:
    """기존 `test_different_reproduced_result_changes_hash`는 config 전체를
    바꿔 큰 차이를 만든다 -- 체결 하나의 가격을 1틱만 바꿔도(예: 실제로는
    없었던 체결가로 위조) 해시가 달라져야 "bit-for-bit" 계약이 성립한다."""
    reproduced = _platform_reproduction()
    assert reproduced.fills, "fixture must produce at least one fill"

    tampered_fill = reproduced.fills[0].model_copy(
        update={"price": reproduced.fills[0].price + Decimal("0.00000001")}
    )
    tampered = reproduced.model_copy(update={"fills": [tampered_fill, *reproduced.fills[1:]]})

    assert compute_result_hash(reproduced) != compute_result_hash(tampered)


# ---------------------------------------------------------------------------
# Failure injection
# ---------------------------------------------------------------------------


def test_compute_result_hash_propagates_unexpected_hashing_dependency_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`compute_result_hash`가 해싱 계층(`hashlib.sha256`)에서 터지는
    예상치 못한 의존성 예외를 삼키지 않고 그대로 호출자에게 전파해야
    한다(fail-closed, 조용한 성공으로 위장 금지)."""
    import hashlib

    def _boom(_data: bytes) -> None:
        raise RuntimeError("hashing backend exploded")

    monkeypatch.setattr(hashlib, "sha256", _boom)

    reproduced = _platform_reproduction()
    with pytest.raises(RuntimeError, match="hashing backend exploded"):
        compute_result_hash(reproduced)
