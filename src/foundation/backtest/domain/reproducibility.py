"""BT-9 — 백테스트 재현 키(`reproducibility_key`).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-9, §3.4(재현 키: `sha256(script_hash ‖ data_lineage_hash ‖
rollup_version ‖ config_hash)`; "같은 키 = 같은 결과(바이트 동일한 체결
로그)"), §9.5 BT-9(DoD: 같은 키=바이트 동일 체결 로그).

선행 리프가 이미 계산한 값을 받아서 하나의 키로 묶기만 하는 순수 조립
함수다 — 각 값의 계산 자체는 이 모듈의 책임이 아니다:
- `script_hash`: DSL-12 `src/core/script/artifact/hash.py::script_hash`.
- `data_lineage_hash`: LA-23b `src/foundation/market_data/domain/lineage.py::batch_hash`
  (또는 그 상위에서 조립한 데이터 계보 다이제스트).
- `rollup_version`: DC-10 `src/foundation/market_data/domain/aggregation/
  timeframe_rollup.py::RollupResult.rollup_version`.
- `config_hash`: BT-1 `domain/models_v2.py::BacktestConfigV2.canonical_json()`의
  sha256 hex — 이 모듈의 `config_hash()`가 그 마지막 단계(직렬화→해시)를
  맡는다(모델 docstring: "해시 계산 자체는 BT-9의 책임").

정준 직렬화는 DSL-12 `artifact/hash.py`와 같은 규칙(정렬된 키, 고정
구분자 `(",", ":")`, `ensure_ascii=True`, `allow_nan=False`)을 그대로
따른다 — 재현 키 계열의 해시들이 서로 다른 정규화 규칙을 쓰면 "같은
입력=같은 해시" 계약의 강도가 리프마다 달라진다.

Fail-closed: 네 입력 중 하나라도 비어 있거나 잘못된 타입이면 `ValueError`
로 거부한다 — 불완전한 재현 키로 "재현됨"을 위장하지 않는다.
"""
from __future__ import annotations

import hashlib
import json
from typing import Final

from src.foundation.backtest.domain.models_v2 import BacktestConfigV2

HASH_ALGORITHM: Final = "sha256"
HASH_SCHEMA: Final = "backtest-reproducibility-key-1"


def _require_nonempty_str(value: str, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"reproducibility_key: {name}가 비어 있습니다")
    return value


def config_hash(config: BacktestConfigV2) -> str:
    """`BacktestConfigV2.canonical_json()` → sha256 hex(64자).

    같은 계약 값(같은 슬리피지·수수료·지연·부분체결·주문유형·비용·조정·
    캘린더 설정) = 같은 `config_hash` — `canonical_json()`이 이미 결정론
    직렬화를 보장하므로 여기서는 그 바이트열을 그대로 해시만 한다.
    """
    if not isinstance(config, BacktestConfigV2):
        raise ValueError(
            f"config_hash: BacktestConfigV2가 아닙니다: {type(config).__name__}"
        )
    canonical = config.canonical_json()
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def reproducibility_key_payload(
    *,
    script_hash: str,
    data_lineage_hash: str,
    rollup_version: str,
    config: BacktestConfigV2,
) -> dict[str, str]:
    """재현 키 입력 4종을 검증해 정준 페이로드(dict)로 만든다. 테스트·감사용 공개."""
    return {
        "schema": HASH_SCHEMA,
        "script_hash": _require_nonempty_str(script_hash, "script_hash"),
        "data_lineage_hash": _require_nonempty_str(data_lineage_hash, "data_lineage_hash"),
        "rollup_version": _require_nonempty_str(rollup_version, "rollup_version"),
        "config_hash": config_hash(config),
    }


def reproducibility_key(
    *,
    script_hash: str,
    data_lineage_hash: str,
    rollup_version: str,
    config: BacktestConfigV2,
) -> str:
    """`script_hash ‖ data_lineage_hash ‖ rollup_version ‖ config_hash` →
    sha256 hex(64자). 같은 네 입력 = 같은 키(§3.4) — 재현 검증 잡이 이
    값만으로 "같은 조건에서 다시 돌렸다"를 판정한다."""
    payload = reproducibility_key_payload(
        script_hash=script_hash,
        data_lineage_hash=data_lineage_hash,
        rollup_version=rollup_version,
        config=config,
    )
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


__all__ = [
    "HASH_ALGORITHM",
    "HASH_SCHEMA",
    "config_hash",
    "reproducibility_key",
    "reproducibility_key_payload",
]
