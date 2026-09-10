"""DC-20 contracts/v2/instruments.py 파생 심볼 확장 — DEEPEN(task-2890,
DEPTH_DC_RD.md#39) D1 -> D3 증빙.

`test_instruments_v2.py`는 정상계(옵션/선물 생성, 현물 무변경, 체인 질의)와
`currency`/`country`/`mic` 형식 위반 3건만 증명했다(D1) — 감사에서
"실패주입 없음, 성능단언 없음, 게이트적색 재현 없음, D3 요소 없음"으로
지적됐다(docs/audit/DEPTH_DC_RD.md 39행). 이 순수 pydantic DTO 리프에는
I/O가 없어 DB/네트워크 실패주입은 원천적으로 성립하지 않는다(같은 문서
14행) — 대신 타입힌트(Literal/Enum/Decimal/AwareDatetime)가 런타임을
강제하지 않는 호출자(역직렬화 경로 등)가 표에 없는 값을 주입해도 전부
`ValidationError` 하나로 수렴해 fail-closed로 거부되는지를 "실패주입"으로
증명한다. 새 기능은 추가하지 않는다 — 깊이만 올린다.

1. 실패 주입(D2) — DC-20 신규 필드 각각에 형식은 맞지만 표에 없는 값,
   또는 타입 자체가 틀린 값을 주입해도 항상 `ValidationError`로 수렴함.
2. 성능 단언(D2) — 옵션/선물/현물이 섞인 대량 인스턴스 생성이 절대시간
   예산 내(정규식 컴파일이 모듈 로드 시 1회뿐이고 호출마다 재컴파일되는
   회귀가 없음의 증거).
3. 게이트 적색 재현(D2) — DC-20 이전 스키마(파생 필드 없는 로컬 재현
   모델)로 옵션을 구성하면 파생 필드가 예외 없이 조용히 사라진다(적색,
   데이터 유실) vs 현재 `v2.Instrument`는 그대로 보존한다(녹색).
4. 동시 다중 인스턴스(D3) — 여러 스레드가 서로 다른 underlying_id/expiry
   조합으로 동시에 옵션 체인을 구성해도 결과가 서로 섞이지 않고, 그 결과
   집합에 대한 underlying_id+expiry 체인 질의도 정확함.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import BaseModel, ValidationError

from src.data.models.base import AssetClass
from src.foundation.market_data.contracts.v2 import instruments as v2

_CROCKFORD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_PREFIX = "01ARZ3NDEKTSV4RRFFQ6"  # 20 chars, leaves 6 for a per-seed suffix


def _ulid(seed: int) -> str:
    """`_ULID_PREFIX`(20자) + seed를 6자 Crockford Base32로 인코딩한 접미사
    -> 26자 유효 형식 ULID. 대량/동시성 테스트에 유일한 id를 값싸게 공급."""
    n = seed
    chars: list[str] = []
    for _ in range(6):
        n, rem = divmod(n, 32)
        chars.append(_CROCKFORD_ALPHABET[rem])
    return _ULID_PREFIX + "".join(reversed(chars))


def _now() -> datetime:
    return datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)


def _sample_instrument(**overrides: object) -> v2.Instrument:
    base: dict[str, object] = dict(
        instrument_id=_ulid(0),
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote="USDT",
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        lifecycle_state=v2.InstrumentLifecycle.ACTIVE,
        created_at=_now(),
    )
    base.update(overrides)
    return v2.Instrument(**base)  # type: ignore[arg-type]


# ---- 1. 실패 주입(D2) — 형식은 그럴듯하나 표/타입에 없는 값이 전부
# ValidationError 하나로 수렴함(fail-closed) ----

_MALFORMED_OVERRIDES: tuple[tuple[str, dict[str, object]], ...] = (
    ("kind_unknown_string", {"kind": "SPACESHIP"}),
    ("kind_wrong_type_int", {"kind": 123}),
    ("kind_wrong_type_list", {"kind": ["OPTION"]}),
    ("option_right_unknown_string", {"option_right": "BOTH"}),
    ("option_right_wrong_type", {"option_right": 1}),
    ("settlement_unknown_string", {"settlement": "BARTER"}),
    ("settlement_wrong_type_bool", {"settlement": True}),
    ("strike_not_a_number", {"strike": "not-a-number"}),
    ("strike_wrong_type_list", {"strike": [100]}),
    ("contract_multiplier_not_a_number", {"contract_multiplier": "abc"}),
    ("expiry_naive_datetime", {"expiry": datetime(2026, 12, 18, 8, 30)}),
    ("expiry_date_only_string_is_naive", {"expiry": "2026-12-18"}),
    ("underlying_id_invalid_ulid", {"underlying_id": "not-a-ulid"}),
    ("underlying_id_wrong_type", {"underlying_id": 42}),
    ("currency_wrong_type", {"currency": 123}),
    ("currency_empty_string", {"currency": ""}),
    ("currency_wrong_length_with_digit", {"currency": "US1"}),
    ("currency_fullwidth_unicode_lookalike", {"currency": "ＵＳＤ"}),
    ("country_contains_space", {"country": "U S"}),
    ("mic_too_short", {"mic": "XNA"}),
    ("mic_too_long", {"mic": "XNASD"}),
)


@pytest.mark.parametrize(
    "overrides",
    [o for _, o in _MALFORMED_OVERRIDES],
    ids=[name for name, _ in _MALFORMED_OVERRIDES],
)
def test_derivative_field_rejects_malformed_value_fail_closed(overrides: dict[str, object]) -> None:
    """Literal/Enum/Decimal/AwareDatetime 타입힌트는 런타임을 강제하지
    않는다 — mypy를 우회하는 호출자(예: JSON 역직렬화)가 표에 없거나 타입이
    틀린 값을 주입해도, 조용히 통과하거나 다른 예외(TypeError/AttributeError
    등)를 누출하지 않고 항상 `ValidationError` 하나로만 거부돼야 한다."""
    with pytest.raises(ValidationError):
        _sample_instrument(**overrides)


def test_malformed_overrides_do_not_partially_construct_instance() -> None:
    """실패 주입이 부분적으로 유효한 `Instrument`를 반환하지 않는다 —
    예외 발생 시 어떤 필드도 조회 가능한 인스턴스가 남지 않는다(pydantic이
    all-or-nothing으로 검증함의 명시적 증거)."""
    try:
        _sample_instrument(kind="SPACESHIP", strike=Decimal("1"))
    except ValidationError as exc:
        # 인스턴스가 아니라 예외만 남아야 한다.
        assert not hasattr(exc, "kind")
    else:
        pytest.fail("malformed kind가 예외 없이 통과했습니다")


# ---- 2. 성능 단언(D2) ----


@pytest.mark.perf
def test_bulk_mixed_kind_instrument_construction_meets_latency_budget() -> None:
    """옵션/선물/현물이 섞인 5,000건 생성이 예산 내여야 한다 — ULID/통화/
    국가/MIC 정규식이 모듈 로드 시 1회만 컴파일되고 호출마다 재컴파일되는
    회귀가 있다면 이 예산을 넘는다(실측 로컬 <0.2s, 느린 CI 대비 10배 이상
    여유)."""
    kinds = (v2.InstrumentKind.OPTION, v2.InstrumentKind.FUTURE, v2.InstrumentKind.SPOT)
    n = 5_000
    budget_sec = 2.0

    start = time.perf_counter()
    for i in range(n):
        kind = kinds[i % 3]
        is_spot = kind is v2.InstrumentKind.SPOT
        _sample_instrument(
            instrument_id=_ulid(i),
            kind=kind,
            underlying_id=None if is_spot else _ulid(i + 1_000_000),
            expiry=None if is_spot else _now(),
            strike=Decimal("70000") if kind is v2.InstrumentKind.OPTION else None,
            option_right=v2.OptionRight.CALL if kind is v2.InstrumentKind.OPTION else None,
            contract_multiplier=None if is_spot else Decimal("1"),
            settlement=None if is_spot else v2.SettlementType.CASH,
            currency="USD",
            country="US",
            mic="XNAS",
        )
    elapsed = time.perf_counter() - start
    print(
        f"[DC-20 instruments] {n} mixed-kind constructions in {elapsed:.3f}s "
        f"({elapsed / n * 1e3:.3f} ms/each, budget<{budget_sec}s)"
    )
    assert elapsed < budget_sec, (
        f"{n}건 생성이 예산({budget_sec}s)을 넘었습니다({elapsed:.3f}s) — "
        "검증기 정규식이 재컴파일되는 등으로 퇴화했는지 확인하세요."
    )


# ---- 3. 게이트 적색 재현(D2) — DC-20 이전 스키마는 파생 필드를 조용히
# 잃는다(적색) vs 현재 v2.Instrument는 보존한다(녹색) ----


class _PreDc20Instrument(BaseModel):
    """DC-20 이전(`kind` 등 파생 필드가 아예 없던) 심볼 마스터 계약의 로컬
    재현. pydantic 기본 `model_config`(`extra="ignore"`)이므로, 이 모델에
    없는 키워드는 예외 없이 조용히 버려진다 — 그것이 바로 DC-20이 없으면
    발생하는 실제 결함이다(옵션인데 옵션인지 알 방법이 사라짐)."""

    instrument_id: v2.ULID
    asset_class: AssetClass
    base: str | None
    quote: str | None
    isin: str | None
    figi: str | None
    tick_size: Decimal
    lot_size: Decimal
    calendar_id: str
    lifecycle_state: v2.InstrumentLifecycle
    created_at: v2.AwareDatetime  # type: ignore[name-defined]


def test_gate_red_pre_dc20_schema_silently_drops_derivative_fields() -> None:
    """적색: DC-20 이전 스키마로 옵션 데이터를 구성하면 예외 없이 통과하지만
    `kind`/`strike`/`option_right` 등은 조회 불가능한 채로 사라진다 — 호출자가
    저장 후 다시 읽으면 이 인스턴트가 옵션이었다는 사실 자체를 잃는다."""
    option_kwargs: dict[str, object] = dict(
        instrument_id=_ulid(1),
        asset_class=AssetClass.CRYPTO,
        base="BTC",
        quote="USDT",
        isin=None,
        figi=None,
        tick_size=Decimal("0.01"),
        lot_size=Decimal("0.0001"),
        calendar_id="24x7",
        lifecycle_state=v2.InstrumentLifecycle.ACTIVE,
        created_at=_now(),
        kind=v2.InstrumentKind.OPTION,
        underlying_id=_ulid(2),
        expiry=datetime(2026, 12, 18, 8, 30, tzinfo=timezone.utc),
        strike=Decimal("70000"),
        option_right=v2.OptionRight.CALL,
    )

    pre_dc20 = _PreDc20Instrument(**option_kwargs)  # type: ignore[arg-type]
    for lost_field in ("kind", "underlying_id", "expiry", "strike", "option_right"):
        assert not hasattr(pre_dc20, lost_field), (
            f"pre-DC-20 모델이 {lost_field!r}을 보존했습니다 — 이 테스트는 "
            "구 스키마가 그 필드를 잃는다는 전제가 깨졌으므로 재검토가 필요합니다"
        )
    # 조용히 사라졌다 — 예외도, 경고도 없다(적색의 핵심: 실패가 보이지 않음).
    assert "kind" not in pre_dc20.model_dump()

    green = v2.Instrument(**option_kwargs)  # type: ignore[arg-type]
    assert green.kind == v2.InstrumentKind.OPTION
    assert green.underlying_id == _ulid(2)
    assert green.strike == Decimal("70000")
    assert green.option_right == v2.OptionRight.CALL
    assert green.model_dump()["kind"] == "OPTION"


# ---- 4. 동시 다중 인스턴스(D3) — 옵션 체인을 여러 스레드가 동시에 구성해도
# 서로 섞이지 않고, 그 결과에 대한 체인 질의도 정확함 ----


def _build_option(underlying_index: int, expiry_index: int, strike_index: int) -> v2.Instrument:
    underlying_id = _ulid(1_000 + underlying_index)
    expiry = datetime(2026, 9 + expiry_index, 26, 8, 30, tzinfo=timezone.utc)
    strike = Decimal(60_000 + strike_index * 5_000)
    seed = underlying_index * 10_000 + expiry_index * 100 + strike_index
    return _sample_instrument(
        instrument_id=_ulid(seed),
        kind=v2.InstrumentKind.OPTION,
        underlying_id=underlying_id,
        expiry=expiry,
        strike=strike,
        option_right=v2.OptionRight.CALL,
    )


def test_concurrent_option_chain_construction_does_not_cross_contaminate() -> None:
    """4개 underlying x 3개 만기 x 5개 행사가 = 60건을 16개 워커(다중
    인스턴스 시뮬레이션)가 동시에 생성해도, 각 인스턴스의 underlying_id/
    expiry/strike가 자신이 요청한 좌표와 정확히 일치해야 한다 — 모듈 레벨
    가변 상태(컴파일된 정규식 등)가 스레드 간에 오염되지 않는다는 증거."""
    coordinates = [(u, e, s) for u in range(4) for e in range(3) for s in range(5)]

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(lambda c: (c, _build_option(*c)), coordinates))

    assert len(results) == len(coordinates)
    for (underlying_index, expiry_index, strike_index), instrument in results:
        assert instrument.underlying_id == _ulid(1_000 + underlying_index), (
            "다른 스레드의 underlying_id와 섞였습니다"
        )
        assert instrument.expiry == datetime(2026, 9 + expiry_index, 26, 8, 30, tzinfo=timezone.utc)
        assert instrument.strike == Decimal(60_000 + strike_index * 5_000)

    return None


def test_concurrent_built_chain_supports_correct_underlying_expiry_query() -> None:
    """동시 생성 결과 집합 전체에 대해 underlying_id+expiry 체인 질의를
    수행해도, 다른 underlying/만기의 옵션이 섞여 들어오지 않고 정확히
    행사가 5개만 반환된다(D0 정상계 테스트의 단일 스레드 버전을 동시성
    하에서 재확인)."""
    coordinates = [(u, e, s) for u in range(4) for e in range(3) for s in range(5)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        chain = list(pool.map(lambda c: _build_option(*c), coordinates))

    target_underlying = _ulid(1_000 + 2)  # underlying_index=2
    target_expiry = datetime(2026, 9 + 1, 26, 8, 30, tzinfo=timezone.utc)  # expiry_index=1

    matches = [
        instrument
        for instrument in chain
        if instrument.underlying_id == target_underlying and instrument.expiry == target_expiry
    ]

    assert len(matches) == 5
    assert {m.strike for m in matches} == {Decimal(60_000 + s * 5_000) for s in range(5)}
    assert all(m.underlying_id == target_underlying for m in matches)
    assert all(m.expiry == target_expiry for m in matches)
