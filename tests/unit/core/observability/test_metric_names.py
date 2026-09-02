"""메트릭 이름 형식 전수 검증.

DoD(docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-04, §6):
`metric_names.py`의 상수 전부가 §6 단위테스트 표의 정규식을 만족해야 하고,
위반 상수가 1개라도 있으면 실패한다.
"""
from __future__ import annotations

import re

from src.core.observability import metric_names

_METRIC_NAME_RE = re.compile(
    r"^aios\.[a-z_]+\.[a-z_]+\.[a-z_]+(_total|_seconds|_bytes|\.gauge)?$"
)


def test_all_metric_names_match_naming_regex() -> None:
    violations = [name for name in metric_names.ALL_METRIC_NAMES if not _METRIC_NAME_RE.match(name)]
    assert violations == [], f"메트릭 이름 형식 위반: {violations}"


def test_all_metric_names_are_nonempty_and_unique() -> None:
    names = list(metric_names.ALL_METRIC_NAMES)
    assert len(names) > 0
    assert len(names) == len(set(names))


def test_module_constants_are_all_registered_in_all_metric_names() -> None:
    """모듈에 정의된 `aios.`로 시작하는 문자열 상수는 전부 `ALL_METRIC_NAMES`에 있어야 한다."""
    declared = {
        value
        for key, value in vars(metric_names).items()
        if key.isupper() and key != "ALL_METRIC_NAMES" and isinstance(value, str)
    }
    assert declared == metric_names.ALL_METRIC_NAMES


def test_to_prom_replaces_dots_with_underscores() -> None:
    assert metric_names.to_prom("aios.api.request.count_total") == "aios_api_request_count_total"


def test_to_prom_is_idempotent_on_already_converted_name() -> None:
    converted = metric_names.to_prom(metric_names.API_REQUEST_COUNT_TOTAL)
    assert metric_names.to_prom(converted) == converted
