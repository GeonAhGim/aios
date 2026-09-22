"""alert_rules.yaml의 메트릭 참조·runbook 링크 전수 검증.

Spec: docs/specs/L4_platform_observability_tenancy_api_v1.0.md §7.4/§9 PLT-11.
`expr` 문자열 안의 모든 `aios_*` 토큰은 `metric_names.py`(단일 출처)를 Prometheus
노출 형식(`.` -> `_`)으로 변환한 이름 집합에 있어야 한다 — 오타·미등록 메트릭을
alert가 조용히 참조하는 사고를 정적으로 막는다. 로그 기반 규칙(`source: logs`)은
Prometheus 메트릭이 아니므로 이 검증에서 자연히 제외된다(expr에 `aios_*` 토큰이 없음).
"""

from __future__ import annotations

import re
import time
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


def _missing_required_fields(rule: dict[str, Any]) -> list[str]:
    """rule에서 빠졌거나 허용되지 않은 값을 가진 필수 필드 이름 목록을 반환한다."""
    missing: list[str] = []
    if not rule.get("alert"):
        missing.append("alert")
    if not rule.get("expr"):
        missing.append("expr")
    if "for" not in rule:
        missing.append("for")
    labels = rule.get("labels") or {}
    if labels.get("severity") not in {"warn", "critical"}:
        missing.append("labels.severity")
    if not labels.get("runbook"):
        missing.append("labels.runbook")
    if not (rule.get("annotations") or {}).get("summary"):
        missing.append("annotations.summary")
    return missing


def _invalid_runbook_reason(rule: dict[str, Any], runbooks_dir: Path = RUNBOOKS_DIR) -> str | None:
    """runbook 라벨이 형식·존재 여부 검증을 통과 못하면 사유를, 통과하면 None을 반환한다."""
    runbook = rule["labels"]["runbook"]
    if not _RUNBOOK_RE.match(runbook):
        return f"잘못된 runbook id 형식 {runbook!r}"
    if not (runbooks_dir / f"{runbook}.md").is_file():
        return f"runbook 파일 없음 {runbook}"
    return None


def test_alert_rules_yaml_parses_and_has_groups() -> None:
    rules = _load_rules()
    assert len(rules) > 0


def test_every_rule_has_required_fields() -> None:
    rules = _load_rules()
    for rule in rules:
        assert _missing_required_fields(rule) == [], rule


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
        reason = _invalid_runbook_reason(rule)
        assert reason is None, f"{rule['alert']}: {reason}"


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


def test_rule_missing_severity_label_is_rejected() -> None:
    """negative: severity 라벨이 아예 없는 규칙은 필수 필드 검증에 걸려야 한다."""
    bad_rule = {
        "alert": "Bogus2",
        "expr": "up == 1",
        "for": "0m",
        "labels": {"runbook": "RB-01"},
        "annotations": {"summary": "x"},
    }
    assert "labels.severity" in _missing_required_fields(bad_rule)


def test_rule_with_invalid_severity_value_is_rejected() -> None:
    """negative: severity가 존재하지만 warn/critical이 아닌 값(오타 등)도 걸려야 한다."""
    bad_rule = {
        "alert": "Bogus3",
        "expr": "up == 1",
        "for": "0m",
        "labels": {"severity": "info", "runbook": "RB-01"},
        "annotations": {"summary": "x"},
    }
    assert "labels.severity" in _missing_required_fields(bad_rule)


def test_runbook_id_with_wrong_format_is_rejected() -> None:
    """negative: RB-NN 정규식에 맞지 않는 runbook id(자릿수 오타 등)는 거부돼야 한다."""
    bad_rule = {"alert": "Bogus4", "labels": {"runbook": "RB-1"}}
    reason = _invalid_runbook_reason(bad_rule)
    assert reason is not None and "형식" in reason


def test_runbook_pointing_to_nonexistent_file_is_rejected() -> None:
    """negative: 형식은 RB-NN이 맞지만 실제 파일이 없는 runbook 참조(RB-99)는 거부돼야 한다."""
    bad_rule = {"alert": "Bogus5", "labels": {"runbook": "RB-99"}}
    reason = _invalid_runbook_reason(bad_rule)
    assert reason is not None and "파일 없음" in reason


def test_malformed_yaml_file_raises_instead_of_silently_passing(tmp_path: Path) -> None:
    """실패 주입: 문법이 깨진 alert_rules.yaml(닫히지 않은 리스트)을 실제로 디스크에
    써서 실 로더(_load_rules)에 태운다 — fail-closed 기본 원칙(CLAUDE.md §3)에 따라
    파싱 실패는 조용히 규칙 0건으로 넘어가지 않고 예외로 드러나야 한다."""
    bad_path = tmp_path / "alert_rules.yaml"
    bad_path.write_text("groups:\n  - name: x\n    rules: [\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        _load_rules(bad_path)


def test_missing_groups_key_raises_instead_of_returning_empty(tmp_path: Path) -> None:
    """실패 주입: 최상위 `groups` 키가 없는 파일(들여쓰기·키 이름 오타로 실제 발생 가능)을
    실 로더에 태워 KeyError로 드러나는지 확인한다 — 빈 리스트를 반환해 "규칙 0건"을
    정상으로 오인하면 alert_rules.yaml 전체가 조용히 무력화되는 사고로 이어진다."""
    bad_path = tmp_path / "alert_rules.yaml"
    bad_path.write_text("not_groups: []\n", encoding="utf-8")
    with pytest.raises(KeyError):
        _load_rules(bad_path)


def test_full_validation_pipeline_p95_latency_within_budget() -> None:
    """수치 성능 단언: 이 파일의 정적 검증은 CI 게이트마다 매번 실행된다. 전체 규칙
    (11개)에 대해 메트릭 토큰 대조 + 필수 필드 + runbook 검증을 1회 통과하는 시간의
    p95가 5ms를 넘지 않아야 한다 — 순수 정적 스캔(디스크 I/O 없이 이미 로드된 파이썬
    객체만 순회)이므로 여유는 충분히 크게 잡았다."""
    rules = _load_rules()
    samples: list[float] = []
    for _ in range(200):
        start = time.perf_counter()
        _unknown_metric_tokens(rules)
        for rule in rules:
            _missing_required_fields(rule)
            if "runbook" in (rule.get("labels") or {}):
                _invalid_runbook_reason(rule)
        samples.append(time.perf_counter() - start)
    samples.sort()
    p95 = samples[int(len(samples) * 0.95)]
    assert p95 < 0.005, f"p95={p95 * 1000:.3f}ms >= 5ms 예산"


def test_rule_missing_expr_field_is_rejected() -> None:
    """negative: expr 필드가 아예 없는 규칙은 필수 필드 검증에 걸려야 한다."""
    bad_rule = {
        "alert": "BogusExpr",
        "for": "0m",
        "labels": {"severity": "warn", "runbook": "RB-01"},
        "annotations": {"summary": "x"},
    }
    assert "expr" in _missing_required_fields(bad_rule)


def test_rule_missing_alert_field_is_rejected() -> None:
    """negative: alert 필드가 아예 없는 규칙은 필수 필드 검증에 걸려야 한다."""
    bad_rule = {
        "expr": "up == 1",
        "for": "0m",
        "labels": {"severity": "warn", "runbook": "RB-01"},
        "annotations": {"summary": "x"},
    }
    assert "alert" in _missing_required_fields(bad_rule)


def test_rule_missing_for_field_is_rejected() -> None:
    """negative: for 필드가 없는 규칙은 필수 필드 검증에 걸려야 한다."""
    bad_rule = {
        "alert": "BogusFor",
        "expr": "up == 1",
        "labels": {"severity": "warn", "runbook": "RB-01"},
        "annotations": {"summary": "x"},
    }
    assert "for" in _missing_required_fields(bad_rule)


def test_runbook_label_missing_is_rejected() -> None:
    """negative: labels.runbook가 아예 없는 규칙은 필수 필드 검증에 걸려야 한다."""
    bad_rule = {
        "alert": "BogusRunbook",
        "expr": "up == 1",
        "for": "0m",
        "labels": {"severity": "warn"},
        "annotations": {"summary": "x"},
    }
    assert "labels.runbook" in _missing_required_fields(bad_rule)


def test_annotations_summary_missing_is_rejected() -> None:
    """negative: annotations.summary가 없거나 빈 문자열인 규칙은 필수 필드 검증에 걸려야 한다."""
    bad_rule = {
        "alert": "BogusSummary",
        "expr": "up == 1",
        "for": "0m",
        "labels": {"severity": "warn", "runbook": "RB-01"},
        "annotations": {},
    }
    assert "annotations.summary" in _missing_required_fields(bad_rule)


def test_unknown_severity_value_rejected() -> None:
    """negative: severity가 공백, 대문자, 기타 값인 경우 모두 거부된다."""
    for bad_value in ["", "WARN", "Critical", "error", "none"]:
        bad_rule = {
            "alert": "BogusSeverity",
            "expr": "up == 1",
            "for": "0m",
            "labels": {"severity": bad_value, "runbook": "RB-01"},
            "annotations": {"summary": "x"},
        }
        assert "labels.severity" in _missing_required_fields(bad_rule), (
            f"severity={bad_value!r}가 통과하면 안 된다"
        )


def test_metric_token_with_uppercase_is_not_matched() -> None:
    """negative: aios_* 토큰은 소문자만 매칭되므로 대문자 혼합 메트릭은 무시된다 —
    Prometheus 이름 규칙과 일치한다."""
    tokens = _metric_tokens("aios_MixedCase_total > 0")
    assert tokens == set(), "대문자가 섞인 토큰은 메트릭 토큰으로 매칭되면 안 된다"


def test_metric_token_with_digits_at_end() -> None:
    """음수 검증: aios_로 시작하고 숫자로 끝나는 토큰도 허용 문자집합[a-z0-9_] 내에 들어와 매칭된다."""
    tokens = _metric_tokens("aios_metric_123 > 0")
    assert "aios_metric_123" in tokens


def test_full_validation_on_corrupted_runbook_file_raises_monkeypatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입(monkeypatch): _invalid_runbook_reason 이 내부적으로 호출하는
    Path.is_file() 를 모의 객체로 바꿔서 FileNotFoundError 를 유발한다 —
    실제 파일 시스템 I/O 가 실패해도 검증 로직이 예외를 잡거나 최소한 정당한
    사유를 반환하는지 확인한다."""
    bad_rule = {
        "alert": "MonkeyPatchTest",
        "expr": "up == 1",
        "for": "0m",
        "labels": {"severity": "warn", "runbook": "RB-01"},
        "annotations": {"summary": "x"},
    }
    # Path.is_file() 가 항상 False 를 반환하도록 모의: runbook 파일이
    # "존재하지 않는" 상태로 간주되어야 한다.
    monkeypatch.setattr(
        "pathlib.Path.is_file",
        lambda self: False,
    )
    reason = _invalid_runbook_reason(bad_rule, RUNBOOKS_DIR)
    assert reason is not None and "파일 없음" in reason


def test_full_validation_on_missing_labels_dict_raises_monkeypatch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 주입(monkeypatch): labels 가 None 인 규칙에서 _missing_required_fields 가
    NoneType 에 .get() 을 호출해도 예외 없이 처리되는지 확인한다 — YAML 에서
    labels: null 이 올 수 있으므로 fail-closed 가 null 도 허용해야 한다."""
    bad_rule = {
        "alert": "NullLabels",
        "expr": "up == 1",
        "for": "0m",
        "labels": None,
        "annotations": {"summary": "x"},
    }
    # labels 가 None 이면 .get() 은 AttributeError 를 일으켜야 하지만
    # _missing_required_fields 는 "labels.severity" 를 missing 에 추가해야 한다.
    missing = _missing_required_fields(bad_rule)
    assert "labels.severity" in missing


def test_gate_goes_red_when_a_real_rule_expr_is_corrupted_with_unknown_metric() -> None:
    """게이트 적색 재현: 실제 alert_rules.yaml에서 로드한 규칙 중 하나의 expr에
    존재하지 않는 메트릭 토큰을 주입한 뒤, test_every_rule_references_a_known_metric_name
    이 쓰는 것과 동일한 단언식을 그대로 실행해 실제로 AssertionError가 나는지 확인한다 —
    오타 메트릭이 실 파일에 병합돼도 CI가 실제로 빨간불이 됨을 증명한다(정적 검증만으로
    "게이트가 통과할 것"이라 가정하지 않는다)."""
    rules = _load_rules()
    corrupted = [dict(r) for r in rules]
    corrupted[0] = dict(corrupted[0])
    corrupted[0]["expr"] = corrupted[0]["expr"] + " and aios_typo_metric_total > 0"

    violations = _unknown_metric_tokens(corrupted)
    with pytest.raises(AssertionError):
        assert violations == {}, f"metric_names.py에 없는 메트릭 참조: {violations}"


def test_full_validation_on_corrupted_yaml_with_real_metric_name_raises(
    tmp_path: Path,
) -> None:
    """실패 주입: 유효한 메트릭 이름이 담긴 YAML 이지만 구문이 깨진 파일을 로드할 때
    예외가 발생하는지 확인 — 메트릭 이름이 정확해도 YAML 구문 오류는 조용히 넘어가지
    않아야 한다."""
    bad_path = tmp_path / "alert_rules.yaml"
    bad_path.write_text(
        "groups:\n"
        "  - name: x\n"
        "    rules:\n"
        "      - alert: A1\n"
        "        expr: aios_loop_last_success_age_seconds > 900\n"
        "        for:\n"
        "        labels:\n          severity: warn\n"
        "        # 깨진 들여쓰기\n"
        "      - alert: broken_indent\n"
        "        expr: up\n"
        "    rules:\n",
        encoding="utf-8",
    )
    # yaml.safe_load 는 구문을 허용할 수 있으나, 두 번째 rules: 키가
    # 첫 번째 rules 리스트를 덮어써서 None 이 되므로 _load_rules 가
    # TypeError 를 일으켜야 한다 — 조용히 통과하지 않음.
    with pytest.raises(TypeError):
        _load_rules(bad_path)
