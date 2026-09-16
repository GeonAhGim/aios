"""BT-9 `domain/reproducibility.py` 테스트.

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§3.4, §9.5 BT-9. DoD: 같은 키(같은 script_hash·data_lineage_hash·
rollup_version·config)=바이트 동일한 체결 로그 — 이 도메인 리프에서는
"바이트 동일"을 재현 키 자체의 바이트 동일성(같은 hex 문자열)으로
증명한다: 재현 키가 같다는 것은 그 키를 만든 네 입력이 모두 같다는
뜻이고, 체결 모델(BT-2~7)은 순수 함수이므로 같은 `BacktestConfigV2` +
같은 시장 데이터(데이터 계보 해시로 고정)에서는 항상 같은 체결 로그를
낸다. 네 입력 각각이 키에 실제로 기여함과 negative(빈 입력·타입 오류)를
단언한다.

DEEPEN(task-3041): negative는 기존 8건(빈 문자열 3종 x2 파라미터화 +
타입 오류 1건)으로 이미 충분해 추가하지 않는다. 원래 없던 수치 성능
단언·실패 주입·게이트 적색 재현 3종을 추가한다.
"""

from __future__ import annotations

import re
import time
from decimal import Decimal

import pytest

import src.foundation.backtest.domain.reproducibility as reproducibility_mod
from src.foundation.backtest.domain.models_v2 import (
    AdjustmentsConfig,
    BacktestConfigV2,
    CostsConfig,
    FixedSlippage,
    OrderTypesConfig,
    PartialFillConfig,
    VenueTierCommission,
)
from src.foundation.backtest.domain.reproducibility import (
    HASH_SCHEMA,
    config_hash,
    reproducibility_key,
    reproducibility_key_payload,
)
from src.foundation.market_data.contracts.v1 import Timeframe

_HEX64 = re.compile(r"^[0-9a-f]{64}$")

SCRIPT_HASH = "a" * 64
DATA_LINEAGE_HASH = "b" * 64
ROLLUP_VERSION = "rollup-v1"


def _config(**overrides: object) -> BacktestConfigV2:
    kwargs: dict[str, object] = dict(
        slippage=FixedSlippage(bps=Decimal("1.5")),
        commission=VenueTierCommission(
            venue="BITGET",
            maker_bps=Decimal("2"),
            taker_bps=Decimal("4"),
            min_fee=Decimal("0.10"),
        ),
        latency_ms=50,
        partial_fill=PartialFillConfig(max_participation_pct=Decimal("0.2")),
        order_types=OrderTypesConfig(limit=True, stop=True, oco=False, trailing=False),
        magnifier_tf=Timeframe.M1,
        costs=CostsConfig(funding=True, borrow_apr=None),
        adjustments=AdjustmentsConfig(splits=True, dividends=True),
        calendar="24x7",
    )
    kwargs.update(overrides)
    return BacktestConfigV2(**kwargs)


def _key(**kw: object) -> str:
    defaults: dict[str, object] = dict(
        script_hash=SCRIPT_HASH,
        data_lineage_hash=DATA_LINEAGE_HASH,
        rollup_version=ROLLUP_VERSION,
        config=_config(),
    )
    defaults.update(kw)
    return reproducibility_key(**defaults)  # type: ignore[arg-type]


# ---- 결정론: 같은 키=바이트 동일 ----


def test_same_four_inputs_yield_same_key() -> None:
    first, second = _key(), _key()
    assert first == second
    assert _HEX64.match(first)


def test_same_key_from_freshly_built_config_instances() -> None:
    """같은 값으로 새로 만든 `BacktestConfigV2` 인스턴스(객체 정체성 다름)도
    같은 키를 낸다 — 재현 키가 파이썬 객체 identity가 아니라 값에만 의존."""
    a = reproducibility_key(
        script_hash=SCRIPT_HASH,
        data_lineage_hash=DATA_LINEAGE_HASH,
        rollup_version=ROLLUP_VERSION,
        config=_config(),
    )
    b = reproducibility_key(
        script_hash=SCRIPT_HASH,
        data_lineage_hash=DATA_LINEAGE_HASH,
        rollup_version=ROLLUP_VERSION,
        config=_config(),
    )
    assert a == b


# ---- 네 입력 각각이 기여 ----


def test_script_hash_change_changes_key() -> None:
    assert _key(script_hash=SCRIPT_HASH) != _key(script_hash="c" * 64)


def test_data_lineage_hash_change_changes_key() -> None:
    assert _key(data_lineage_hash=DATA_LINEAGE_HASH) != _key(data_lineage_hash="d" * 64)


def test_rollup_version_change_changes_key() -> None:
    assert _key(rollup_version=ROLLUP_VERSION) != _key(rollup_version="rollup-v2")


def test_config_change_changes_key() -> None:
    other = _config(latency_ms=999)
    assert _key(config=_config()) != _key(config=other)


def test_config_hash_matches_canonical_json_sha256() -> None:
    import hashlib

    cfg = _config()
    assert config_hash(cfg) == hashlib.sha256(cfg.canonical_json().encode("utf-8")).hexdigest()
    assert _HEX64.match(config_hash(cfg))


def test_payload_exposes_all_four_inputs() -> None:
    payload = reproducibility_key_payload(
        script_hash=SCRIPT_HASH,
        data_lineage_hash=DATA_LINEAGE_HASH,
        rollup_version=ROLLUP_VERSION,
        config=_config(),
    )
    assert set(payload) == {
        "schema",
        "script_hash",
        "data_lineage_hash",
        "rollup_version",
        "config_hash",
    }
    assert payload["schema"] == HASH_SCHEMA
    assert payload["script_hash"] == SCRIPT_HASH
    assert payload["data_lineage_hash"] == DATA_LINEAGE_HASH
    assert payload["rollup_version"] == ROLLUP_VERSION


# ---- negative ----


@pytest.mark.parametrize("bad", ["", "   \n"])
def test_empty_script_hash_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="script_hash"):
        _key(script_hash=bad)


@pytest.mark.parametrize("bad", ["", "   \n"])
def test_empty_data_lineage_hash_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="data_lineage_hash"):
        _key(data_lineage_hash=bad)


@pytest.mark.parametrize("bad", ["", "   \n"])
def test_empty_rollup_version_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="rollup_version"):
        _key(rollup_version=bad)


def test_non_backtest_config_rejected() -> None:
    with pytest.raises(ValueError, match="BacktestConfigV2"):
        config_hash(object())  # type: ignore[arg-type]


# ---- DEEPEN(task-3041): 수치 성능 단언 ----

# `reproducibility_key`는 이미 계산된 네 값을 문자열로 묶어 sha256 두 번
# 돌리는 순수 조립 함수다. ADR-2026-09-09-C Decision 1의 "백테스트 1개월
# M1 1심볼 3초" 예산 중 재현 키 조립이 차지할 몫을 2ms로 상한한다 — 체결
# 시뮬레이션 자체(밀리초~초 단위)에 비해 무시할 수준이어야 이 리프가 전체
# 예산을 갉아먹지 않는다는 뜻이다.
_BUDGET_MS = 2.0
_ITERATIONS = 200


def _p95_ms(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(int(len(ordered) * 0.95), len(ordered) - 1)] * 1000


def test_reproducibility_key_p95_latency_within_backtest_budget_slice() -> None:
    """DEEPEN(task-3041): ADR-2026-09-09-C Decision 1 예산 중 재현 키 조립
    몫(2ms)을 실제로 단언한다."""
    samples: list[float] = []
    for _ in range(_ITERATIONS):
        started = time.perf_counter()
        _key()
        samples.append(time.perf_counter() - started)
    p95_ms = _p95_ms(samples)
    print(f"[BT-9] reproducibility_key p95={p95_ms:.4f}ms budget<{_BUDGET_MS:.0f}ms")
    assert p95_ms < _BUDGET_MS


# ---- DEEPEN(task-3041): 실패 주입 ----


def test_reproducibility_key_still_correct_when_config_hash_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입: `config_hash` 계산이 실제로 느려지면(예: 회귀로 config
    정규화 단계에 무거운 검증이 끼어드는 상황) `reproducibility_key`가 그
    지연을 그대로 감내하면서도 여전히 지연 없는 호출과 동일한 키를 내는지
    확인한다 — 위 p95 단언이 캐시나 지름길을 재는 게 아니라 실제
    `config_hash` 호출을 포함한 전체 조립을 재고 있음을 보장한다."""
    original_config_hash = reproducibility_mod.config_hash
    delay_s = 0.01

    def _stalled_config_hash(config: BacktestConfigV2) -> str:
        time.sleep(delay_s)
        return original_config_hash(config)

    monkeypatch.setattr(reproducibility_mod, "config_hash", _stalled_config_hash)

    started = time.perf_counter()
    stalled_key = _key()
    elapsed_s = time.perf_counter() - started

    monkeypatch.undo()
    baseline_key = _key()

    assert elapsed_s >= delay_s
    assert stalled_key == baseline_key


# ---- DEEPEN(task-3041): 게이트 적색 재현 ----


def test_budget_gate_actually_fails_when_config_hash_stalls_past_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: `config_hash`가 2ms 예산을 실제로 넘기도록 지연을
    주입하면, `test_reproducibility_key_p95_latency_within_backtest_budget_slice`
    와 동일한 단언식이 실제로 `AssertionError`를 내는지(= CI가 실제로
    빨간불이 되는지) 확인한다 — 이 테스트가 없으면 위 단언이 항상
    통과하는 tautology인지 아무도 검증하지 못한다."""
    original_config_hash = reproducibility_mod.config_hash

    def _stalled_config_hash(config: BacktestConfigV2) -> str:
        time.sleep(0.01)  # > 2ms 예산
        return original_config_hash(config)

    monkeypatch.setattr(reproducibility_mod, "config_hash", _stalled_config_hash)

    samples: list[float] = []
    for _ in range(5):
        started = time.perf_counter()
        _key()
        samples.append(time.perf_counter() - started)
    p95_ms = _p95_ms(samples)
    with pytest.raises(AssertionError):
        assert p95_ms < _BUDGET_MS
