"""alert_rules.yaml의 메트릭 참조·runbook 링크 전수 검증.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §7.4/§9 PLT-11.
`expr` 문자열 안의 모든 `aios_*` 토큰은 `metric_names.py`(단일 출처)를 Prometheus
노출 형식(`.` -> `_`)으로 변환한 이름 집합에 있어야 한다 — 오타·미등록 메트릭을
alert가 조용히 참조하는 사고를 정적으로 막는다. 로그 기반 규칙(`source: logs`)은
Prometheus 메트릭이 아니므로 이 검증에서 자연히 제외된다(expr에 `aios_*` 토큰이 없음).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.core.observability.metric_names import ALL_METRIC_NAMES, to_prom

ALERT_RULES_PATH = Path(__file__).parents[3] / "config" / "observability" / "alert_rules.yaml"
RUNBOOKS_DIR = Path(__file__).parents[3] / "docs" / "runbooks"

_METRIC_TOKEN_RE = re.compile(r"\baios_[a-z0-9_]+\b")
_RUNBOOK_RE = re.compile(r"^RB-\d{2}$")

KNOWN_PROM_METRIC_NAMES: frozenset[str] = frozenset(to_prom(name) for name in ALL_METRIC_NAMES)


def _load_rules(path: Path = ALERT_RULES_PATH) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    rules: list[dict[str, Any]] = []
    for group in doc["groups"]:
        rules.extend(group["rules"])
    return rules


def _metric_tokens(expr: str) -> set[str]:
    return set(_METRIC_TOKEN_RE.findall(expr))


def _unknown_metric_tokens(rules: list[dict[str, Any]]) -> dict[str, set[str]]:
    violations: dict[str, set[str]] = {}
    for rule in rules:
        unknown = _metric_tokens(rule["expr"]) - KNOWN_PROM_METRIC_NAMES
        if unknown:
            violations[rule["alert"]] = unknown
    return violations


def test_alert_rules_yaml_parses_and_has_groups() -> None:
    rules = _load_rules()
    assert len(rules) > 0


def test_every_rule_has_required_fields() -> None:
    rules = _load_rules()
    for rule in rules:
        assert rule.get("alert"), rule
        assert rule.get("expr"), rule
        assert "for" in rule, rule
        labels = rule.get("labels") or {}
        assert labels.get("severity") in {"warn", "critical"}, rule
        assert labels.get("runbook"), rule
        assert (rule.get("annotations") or {}).get("summary"), rule


def test_alert_names_are_unique() -> None:
    rules = _load_rules()
    names = [rule["alert"] for rule in rules]
    assert len(names) == len(set(names))


def test_every_rule_references_a_known_metric_name() -> None:
    """expr 안의 aios_* 토큰은 전부 metric_names.py(Prometheus 형식)에 존재해야 한다."""
    violations = _unknown_metric_tokens(_load_rules())
    assert violations == {}, f"metric_names.py에 없는 메트릭 참조: {violations}"


def test_every_runbook_label_points_to_an_existing_runbook_file() -> None:
    rules = _load_rules()
    for rule in rules:
        runbook = rule["labels"]["runbook"]
        assert _RUNBOOK_RE.match(runbook), f"{rule['alert']}: 잘못된 runbook id 형식 {runbook!r}"
        runbook_path = RUNBOOKS_DIR / f"{runbook}.md"
        assert runbook_path.is_file(), f"{rule['alert']}: runbook 파일 없음 {runbook_path}"


def test_all_eight_runbooks_exist() -> None:
    expected = {f"RB-{n:02d}.md" for n in range(1, 9)}
    actual = {p.name for p in RUNBOOKS_DIR.glob("RB-*.md")}
    assert expected <= actual


def test_unknown_metric_token_is_rejected() -> None:
    """negative: 존재하지 않는 메트릭을 참조하는 규칙은 검증 로직이 반드시 걸러낸다."""
    bogus_rules = [
        {
            "alert": "Bogus",
            "expr": "increase(aios_totally_made_up_count_total[5m]) > 0",
            "for": "0m",
            "labels": {"severity": "warn", "runbook": "RB-01"},
            "annotations": {"summary": "x"},
        }
    ]
    violations = _unknown_metric_tokens(bogus_rules)
    assert violations == {"Bogus": {"aios_totally_made_up_count_total"}}


def test_log_sourced_rule_has_no_metric_tokens_to_validate() -> None:
    """A1은 로그 이벤트 기반(source: logs)이라 aios_* 메트릭 토큰이 없어야 한다 —
    있다면 metric_names.py 대조를 몰래 우회하려는 설계 오류다."""
    rules = _load_rules()
    log_rules = [r for r in rules if r.get("source") == "logs"]
    assert log_rules, "로그 기반 규칙(A1)이 최소 1개 있어야 한다"
    for rule in log_rules:
        assert _metric_tokens(rule["expr"]) == set()


@pytest.mark.parametrize("alert_id", [f"A{i}" for i in range(1, 12)])
def test_every_spec_alert_id_is_present(alert_id: str) -> None:
    """§7.4 표의 A1~A11이 전부 정의됐는지(리프 누락 방지)."""
    rules = _load_rules()
    prefixes = [rule["alert"].split("_", 1)[0] for rule in rules]
    assert alert_id in prefixes, f"{alert_id} 규칙 누락"
