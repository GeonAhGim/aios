"""BT-9 — Backtest reproducibility key (`reproducibility_key`).

Spec: docs/specs/L4_analytics_authoring_backtest_marketplace_v1.0.md
§2.5 BT-9, §3.4 (reproducibility key: `sha256(script_hash ‖ data_lineage_hash ‖
rollup_version ‖ config_hash)`; "same key = same result (byte-identical trade
log)"), §9.5 BT-9 (DoD: same key → byte-identical trade log).

This is a pure assembly function that only takes values already computed by
preceding leaves and bundles them into a single key — the computation of each
value itself is not this module's responsibility:
- `script_hash`: DSL-12 `src/core/script/artifact/hash.py::script_hash`.
- `data_lineage_hash`: LA-23b `src/foundation/market_data/domain/lineage.py::batch_hash`
  (or a data-lineage digest assembled at a higher level).
- `rollup_version`: DC-10 `src/foundation/market_data/domain/aggregation/
  timeframe_rollup.py::RollupResult.rollup_version`.
- `config_hash`: sha256 hex of BT-1 `domain/models_v2.py::BacktestConfigV2.canonical_json()`
  — this module's `config_hash()` takes the final step (serialization → hash)
  (model docstring: "hash computation itself is not BT-9's responsibility").

Canonical serialization follows the same rules as DSL-12 `artifact/hash.py`
(sorted keys, fixed delimiters `(",", ":")`, `ensure_ascii=True`,
`allow_nan=False`) — if reproducibility-key hashes use different normalization
rules, the strength of the "same input = same hash" contract varies per leaf.

Fail-closed: if any of the four inputs is empty or has the wrong type, reject
with `ValueError` — do not disguise "reproduced" with an incomplete key.
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
    """`BacktestConfigV2.canonical_json()` → sha256 hex (64 chars).

    Same contractual values (same slippage, fees, latency, partial fills,
    order types, costs, adjustments, calendar settings) = same `config_hash` —
    `canonical_json()` already guarantees deterministic serialization, so we
    only hash its bytes here.
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
    """Validate the four reproducibility-key inputs and produce a canonical
    payload (dict). Published for testing and audit."""
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
    sha256 hex (64 chars). Same four inputs = same key (§3.4) — reproducibility
    verification uses this value alone to determine "ran again under the same
    conditions"."""
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
