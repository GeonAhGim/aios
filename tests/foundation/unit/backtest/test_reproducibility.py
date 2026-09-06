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
"""
from __future__ import annotations

import re
from decimal import Decimal

import pytest

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
