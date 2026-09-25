"""CONSIST-1 집계 · 래칫 baseline I/O -- task-3725 CONSIST-1c로
check_consistency.py에서 분리(순수 이동, 판정 로직 변경 없음).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from scripts.consistency.common import Hit
from scripts.consistency.contracts import (
    check_env_keys,
    check_event_consumers,
    check_feature_flags,
    check_migrations,
    check_openapi_frontend,
)
from scripts.consistency.spec_trace import check_spec_leaf_traceability, check_spec_template
from scripts.consistency.time_money import (
    check_authority_duplication,
    check_money_float,
    check_naive_datetime,
    check_symbol_id_assembly,
)
from scripts.consistency.wiring import (
    check_port_implementations,
    check_port_protocol_implementations,
    check_router_wiring,
)

METRICS: dict[str, Callable[[Path], list[Hit]]] = {
    "router_unregistered": check_router_wiring,
    "port_method_unimplemented": check_port_implementations,
    "port_protocol_unimplemented": check_port_protocol_implementations,
    "env_key_undocumented": check_env_keys,
    "feature_flag_undocumented": check_feature_flags,
    "event_type_unconsumed": check_event_consumers,
    "migration_hygiene": check_migrations,
    "openapi_client_mismatch": check_openapi_frontend,
    "spec_leaf_untraced": check_spec_leaf_traceability,
    "naive_datetime": check_naive_datetime,
    "money_float": check_money_float,
    "symbol_id_assembly": check_symbol_id_assembly,
    "spec_template_incomplete": check_spec_template,
    "authority_duplication": check_authority_duplication,
}


class ConsistencyError(ValueError):
    """baseline JSON 형식 오류."""


def scan_all(root: Path) -> dict[str, list[Hit]]:
    return {name: sorted(fn(root)) for name, fn in METRICS.items()}


def counts_of(hits: dict[str, list[Hit]]) -> dict[str, int]:
    return {metric: len(hits[metric]) for metric in METRICS}


def read_baseline(path: Path) -> dict[str, int] | None:
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ConsistencyError(f"baseline 파일이 비어 있음: {path}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConsistencyError(f"baseline JSON 파싱 실패: {exc}") from exc
    if not isinstance(data, dict):
        raise ConsistencyError("baseline JSON은 객체여야 함")
    result: dict[str, int] = {}
    for metric in METRICS:
        value = data.get(metric)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ConsistencyError(f"baseline 값이 정수가 아님: {metric}={value!r}")
        result[metric] = value
    return result


def write_baseline(path: Path, counts: dict[str, int]) -> None:
    payload = {metric: counts[metric] for metric in METRICS}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
