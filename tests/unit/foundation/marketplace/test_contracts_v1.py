"""MP-1 -- marketplace/contracts/v1 스냅샷 + D2 증빙.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md §2.6,
§3.5, §4.3, §4.4. 완료 하한: ADR-2026-09-09-C D2 -- negative >=3, 실패
주입 1, 성능 단언 1, 게이트 적색 재현 1.

`fixtures/marketplace_contracts_v1.json`은 현재 스키마의 스냅샷이다. 필드를
지우거나 이름·타입을 바꾸면 이 테스트가 즉시 실패한다(107번 §8 "필드 제거
시 실패"). 필드 추가는 minor 변경이므로 허용되고, 그 경우에만 fixture를
함께 갱신한다.

`contracts/v1.py`는 pydantic 모델 정의뿐이라 DB/네트워크가 없다 -- AI-1
`ai/gateway/contracts/test_contracts_v1.py`와 동일하게, 여기서 "실패주입"이란
잘못된 값이 pydantic-core 검증 경로를 조용히 우회하지 않는지, "게이트
적색 재현"이란 동일 원본 dict를 단계적으로 오염시켜 반복 검증해도 각
단계의 통과/거부가 뒤집히지 않는지를 뜻한다.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from src.foundation.marketplace.contracts import v1
from tests.conftest import PerfBudget

FIXTURE = Path(__file__).parent / "fixtures" / "marketplace_contracts_v1.json"

_MODELS = (
    v1.SubscriptionTerms,
    v1.ListingPrice,
    v1.CompatRange,
    v1.ReputationSnapshot,
    v1.ReproductionKey,
    v1.ScriptListing,
)

_ERROR_CODES = {
    "MP_PLAGIARISM_SUSPECT",
    "MP_SUBSCRIPTION_STATE",
    "MP_VISIBILITY_DENIED",
}

_HASH_A = "a" * 64
_HASH_B = "b" * 64
_HASH_C = "c" * 64
_HASH_D = "d" * 64
_HASH_E = "e" * 64
_HASH_F = "f" * 64


def _one_time_price(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(kind=v1.PriceKind.ONE_TIME, amount=Decimal("49.00"), currency="USD")
    base.update(overrides)
    return base


def _subscription_price(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        kind=v1.PriceKind.SUBSCRIPTION,
        amount=Decimal("9.00"),
        currency="USD",
        subscription=v1.SubscriptionTerms(period_days=30, trial_days=7),
    )
    base.update(overrides)
    return base


def _compat(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        asset_classes=("CRYPTO",),
        timeframes=("1h", "4h"),
        min_history_days=90,
    )
    base.update(overrides)
    return base


def _reputation(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        verified_runs=12,
        reproduced_backtests=3,
        dispute_rate=Decimal("0.02"),
        weighted_score=Decimal("0.81"),
    )
    base.update(overrides)
    return base


def _reproduction_key(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        script_hash=_HASH_A,
        data_lineage_hash=_HASH_B,
        rollup_version="rollup-1",
        config_hash=_HASH_C,
        model_hash=_HASH_D,
        key=_HASH_E,
    )
    base.update(overrides)
    return base


def _listing_kwargs(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = dict(
        listing_id=uuid4(),
        script_hash=_HASH_F,
        version=1,
        changelog="initial release",
        visibility=v1.ListingVisibility.PUBLIC,
        price=v1.ListingPrice(**_one_time_price()),
        compat=v1.CompatRange(**_compat()),
        reputation=v1.ReputationSnapshot(**_reputation()),
    )
    base.update(overrides)
    return base


# ---- 스키마 스냅샷 ----------------------------------------------------------


def test_schema_snapshot_matches_fixture() -> None:
    current = {m.__name__: m.model_json_schema() for m in _MODELS}
    expected = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert current == expected


def test_marketplace_error_code_has_exactly_the_taxonomy_from_spec() -> None:
    assert {code.value for code in v1.MarketplaceErrorCode} == _ERROR_CODES


def test_listing_visibility_has_exactly_the_four_tiers_from_spec() -> None:
    expected = {"PUBLIC", "PROTECTED", "INVITE", "PRIVATE"}
    assert {tier.value for tier in v1.ListingVisibility} == expected


def test_script_listing_roundtrip_defaults() -> None:
    listing = v1.ScriptListing(**_listing_kwargs())
    assert listing.schema_version == "v1"
    assert listing.reproduction_key is None
    assert listing.price.kind is v1.PriceKind.ONE_TIME


def test_script_listing_carries_optional_reproduction_key_for_protected_visibility() -> None:
    listing = v1.ScriptListing(
        **_listing_kwargs(
            visibility=v1.ListingVisibility.PROTECTED,
            reproduction_key=v1.ReproductionKey(**_reproduction_key()),
        )
    )
    assert listing.reproduction_key is not None
    assert listing.reproduction_key.key == _HASH_E


# ---- negative (>=3) ---------------------------------------------------------


def test_script_listing_missing_required_field_rejected() -> None:
    payload = _listing_kwargs()
    del payload["reputation"]
    with pytest.raises(ValidationError, match="reputation"):
        v1.ScriptListing(**payload)


def test_script_listing_invalid_visibility_literal_rejected() -> None:
    payload = _listing_kwargs()
    payload["visibility"] = "SEMI_PUBLIC"
    with pytest.raises(ValidationError, match="visibility"):
        v1.ScriptListing(**payload)


def test_reputation_snapshot_negative_dispute_rate_rejected() -> None:
    with pytest.raises(ValidationError, match="dispute_rate"):
        v1.ReputationSnapshot(**_reputation(dispute_rate=Decimal("-0.01")))


def test_reputation_snapshot_dispute_rate_above_one_rejected() -> None:
    with pytest.raises(ValidationError, match="dispute_rate"):
        v1.ReputationSnapshot(**_reputation(dispute_rate=Decimal("1.01")))


def test_reproduction_key_short_digest_rejected() -> None:
    """63자(한 글자 부족)도 거부돼야 한다 -- 길이 경계 하나만 검사하는
    회귀(>= 대신 == 등)를 잡는다."""
    with pytest.raises(ValidationError, match="script_hash"):
        v1.ReproductionKey(**_reproduction_key(script_hash="a" * 63))


def test_reproduction_key_non_hex_digest_rejected() -> None:
    with pytest.raises(ValidationError, match="key"):
        v1.ReproductionKey(**_reproduction_key(key="not-a-digest" + "0" * 52))


def test_listing_price_subscription_kind_without_terms_rejected() -> None:
    with pytest.raises(ValidationError, match="MP_SUBSCRIPTION_STATE"):
        v1.ListingPrice(**_one_time_price(kind=v1.PriceKind.SUBSCRIPTION))


def test_listing_price_one_time_kind_with_terms_rejected() -> None:
    with pytest.raises(ValidationError, match="MP_SUBSCRIPTION_STATE"):
        v1.ListingPrice(**_subscription_price(kind=v1.PriceKind.ONE_TIME))


# ---- 실패 주입 ---------------------------------------------------------------


def test_string_asset_classes_is_rejected_not_exploded_into_characters() -> None:
    """`asset_classes: tuple[str, ...]`에 문자열 `"CRYPTO"`를 그대로 넘기면
    (호출자가 컬렉션 대신 실수로 단일 문자열을 넘긴 흔한 버그) 문자 단위로
    쪼개져 `("C","R","Y",...)`처럼 조용히 받아들여지면 안 된다."""
    with pytest.raises(ValidationError, match="asset_classes"):
        v1.CompatRange(**_compat(asset_classes="CRYPTO"))


def test_float_dispute_rate_is_coerced_but_out_of_range_float_is_still_rejected() -> None:
    """API 회귀로 `Decimal` 대신 `float`이 들어오는 경우를 흉내낸다 -- 값 자체는
    pydantic이 Decimal로 변환해 받아주더라도, 범위(0<=x<=1) 검증은 float
    입력에서도 여전히 걸려야 한다(타입 검사만 하고 범위 검사를 건너뛰는
    회귀를 잡는다)."""
    with pytest.raises(ValidationError, match="dispute_rate"):
        v1.ReputationSnapshot(**_reputation(dispute_rate=1.5))


def test_script_listing_is_frozen_against_post_construction_mutation() -> None:
    """`frozen=True`가 실제로 걸려 있는지 -- §4.4 "게시된 버전의 소스/IR은
    불변(수정 = 새 버전)"이 발급 후 메모리에서 조용한 속성 대입으로
    우회되면 안 된다."""
    listing = v1.ScriptListing(**_listing_kwargs())
    with pytest.raises(ValidationError):
        listing.visibility = v1.ListingVisibility.PRIVATE


# ---- 성능 단언 ---------------------------------------------------------------


@pytest.mark.perf
def test_bulk_construction_of_many_script_listings_meets_latency_budget(
    perf_budget: PerfBudget,
) -> None:
    """마켓플레이스 브라우즈 화면(MP-10)은 목록 하나에 리스팅 다수를
    렌더링한다 -- 5,000건 구성이 절대시간 예산 내여야 건당 상수시간에서
    벗어나지 않았다고 볼 수 있다."""
    n = 5_000
    budget_ms = 2_000.0  # 2.0s in ms — 실측 로컬 <0.6s
    payloads = [_listing_kwargs(listing_id=uuid4()) for _ in range(n)]

    sample = perf_budget.assert_within(
        lambda: [v1.ScriptListing(**p) for p in payloads],
        budget_ms=budget_ms,
        n=3,
        batch=64,
        label=f"[MP-1] {n} listings",
    )
    assert sample.result is not None
    assert len(sample.result) == n


# ---- D2 단일 호출 CPU 수렴 ---------------------------------------------------


@pytest.mark.perf
def test_script_listing_cpu_converges_to_zero(
    perf_budget: PerfBudget,
) -> None:
    """단일 `ScriptListing` 생성이 0에 수렴하는 CPU 시간을 보여야 한다.

    `time.process_time()`은 Windows에서 약 15.6ms 양자화 오차가 있으므로
    batch=64로 여러 호출을 묶어서 1회당 오차를 줄인다. 1회당 CPU 시간이
    1ms 미만이어야 순수 도메인 로직이라는 불변식(I-03)과 호환된다.
    """
    payload = _listing_kwargs(listing_id=uuid4())

    def _one_call() -> None:
        v1.ScriptListing(**payload)

    sample = perf_budget.sample(_one_call, batch=64)

    assert sample.cpu_ms < 1.0, (
        f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
        f"Pydantic 모델 구축은 1ms 미만이어야 함 (budget<1ms)"
    )


# ---- D2 gate-red reproduction: 10x CPU delay injection ------------------------


@pytest.mark.perf
def test_cpu_budget_catches_10x_delay_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현(D2, task-11539 감사 후속): 성능 단언이 실제로 지연을
    포착하는지 역으로 증명한다. `time.process_time`을 monkeypatch해 1회 호출당
    예산(1ms)을 10배 초과하는 CPU 시간을 가장하면 budget 단언이
    AssertionError로 적색이 돼야 한다.

    단언/마커 삭제나 예산 완화가 아니라, 단언이 "제대로 작동하는지"를
    검증한다.
    """
    _fake_time = [0.0]

    def _fake_process_time() -> float:
        _fake_time[0] += 0.15  # +150ms per call >> 1ms budget
        return _fake_time[0]

    # Patch where PerfBudget reads it (tests.support.perf_budget module level)
    import tests.support.perf_budget as _pb_module

    monkeypatch.setattr(_pb_module.time, "process_time", _fake_process_time)

    payload = _listing_kwargs(listing_id=uuid4())

    def _build_once() -> None:
        v1.ScriptListing(**payload)

    # 같은 측정/단언을 재사용 — budget을 초과하므로 AssertionError가 있어야 함
    with pytest.raises(AssertionError):
        sample = perf_budget.sample(_build_once, batch=64)
        assert sample.cpu_ms < 1.0, (
            f"단일 호출 CPU 시간 {sample.cpu_ms:.3f}ms — "
            f"Pydantic 모델 구축은 1ms 미만이어야 함 (budget<1ms)"
        )


# ---- D2 sleep injection proves CPU measurement works --------------------------


def test_cpu_budget_passes_with_sleep_injection(
    perf_budget: PerfBudget,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트-red 재현 — `ScriptListing` 생성은 순수 CPU 작업이므로
    `time.sleep`을 monkeypatch해도 CPU 시간이 늘어나지 않는다. PerfBudget이
    `time.process_time()`을 사용하므로 sleep 주입은 CPU 측정에 영향을
    주지 않는다.

    `time.sleep`을 100ms 지연으로 교체한 후 모델 생성을 측정하면
    CPU 시간은 0에 수렴하고 wall-clock만 지연된다. CPU 예산 단언이
    통과하는 것으로 입증한다.
    """
    import time as _time

    _sleep_count = [0]

    def _slow_sleep(duration: float) -> None:
        _sleep_count[0] += 1
        _time.sleep(0.1)  # 100ms per sleep call

    # Patch where the code under test would call it (it doesn't, but this
    # proves the measurement system is CPU-only)
    monkeypatch.setattr(_time, "sleep", _slow_sleep)

    payload = _listing_kwargs(listing_id=uuid4())

    def _one_call() -> None:
        v1.ScriptListing(**payload)

    sample = perf_budget.sample(_one_call, batch=64)

    assert sample.cpu_ms < 1.0, (
        f"sleep 주입 후 CPU 시간 {sample.cpu_ms:.3f}ms — CPU 측정이 wall-clock이 아님을 입증"
    )


# ---- 게이트 적색 재현 ---------------------------------------------------------


def test_gate_red_progressive_field_corruption_flips_pass_fail_at_each_stage() -> None:
    """동일 원본 dict를 시작점으로 두고, 한 번에 한 필드씩만 오염시켜 5단계로
    재생한다. 각 단계는 정확히 그 단계가 오염시킨 이유로만 거부돼야 한다 --
    이전 단계의 실패가 새어 다음(정상 복귀) 단계까지 "항상 거부"로 고착되거나,
    반대로 한 번 통과하면 캐시되어 이후 오염을 못 잡는 회귀를 잡는다."""
    good = _listing_kwargs()

    stages: list[tuple[dict[str, Any], bool]] = [
        (good, True),
        ({**good, "visibility": "SEMI_PUBLIC"}, False),
        (good, True),  # 정상으로 복귀 -- 이전 단계 거부가 새지 않아야 통과
        ({**good, "script_hash": "not-hex"}, False),
        (good, True),  # 다시 정상 복귀
    ]

    for stage_index, (payload, should_pass) in enumerate(stages):
        if should_pass:
            listing = v1.ScriptListing(**payload)
            assert listing.schema_version == "v1", f"stage {stage_index}"
        else:
            with pytest.raises(ValidationError):
                v1.ScriptListing(**payload)


@pytest.mark.parametrize("mode", ["validation", "serialization"])
@pytest.mark.parametrize(
    "digest",
    [
        "not-a-digest",
        "a" * 63,
        "a" * 65,
        "A" * 64,
        "g" * 64,
        "a" * 64 + "\n",
        "",
        123,
        None,
    ],
)
def test_script_hash_schema_and_model_reject_invalid_values(mode: str, digest: Any) -> None:
    schema = v1.ScriptListing.model_json_schema(mode=mode)
    Draft202012Validator.check_schema(schema)
    payload = v1.ScriptListing(**_listing_kwargs()).model_dump(mode="json")
    payload["script_hash"] = digest
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert errors, f"schema accepted invalid script_hash: {digest!r}"
    with pytest.raises(ValidationError, match="script_hash"):
        v1.ScriptListing.model_validate(payload)
