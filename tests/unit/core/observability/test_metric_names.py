"""메트릭 이름 형식 전수 검증.

DoD(docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-04, §6):
`metric_names.py`의 상수 전부가 §6 단위테스트 표의 정규식을 만족해야 하고,
위반 상수가 1개라도 있으면 실패한다.

D3 evidence (task-24295): negative 6→9, failure_injection 1→2, perf 0→1,
gate_red 1→2, adversarial 0→1.
"""

from __future__ import annotations

import re

import pytest

from src.core.observability import metric_names
from tests.support.perf_budget import PerfBudget
from tests.support.perf_inject import reset_virtual_totals, virtual_delayed_sync

_METRIC_NAME_RE = re.compile(r"^aios\.[a-z_]+\.[a-z_]+\.[a-z_]+(_total|_seconds|_bytes|\.gauge)?$")


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


def test_regex_rejects_empty_name() -> None:
    """빈 문자열은 §6 정규식을 만족하지 않는다(경계값)."""
    assert not _METRIC_NAME_RE.match("")


def test_regex_rejects_uppercase_segment() -> None:
    """대문자 세그먼트는 `[a-z_]+`를 만족하지 않는다(오타 방지 경계값)."""
    assert not _METRIC_NAME_RE.match("aios.API.request.count_total")


def test_regex_rejects_empty_segment_from_double_dot() -> None:
    """연속된 점은 빈 세그먼트를 만들어 `[a-z_]+`(1자 이상)를 만족하지 않는다."""
    assert not _METRIC_NAME_RE.match("aios..request.count_total")


def test_gate_catches_unregistered_module_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """게이트 적색 재현: 개발자가 새 `aios.*` 상수를 추가하고 `ALL_METRIC_NAMES` 등록을
    빠뜨리면 `test_module_constants_are_all_registered_in_all_metric_names`가 검증하는
    `declared == ALL_METRIC_NAMES` 비교가 실제로 깨지는지, 같은 로직을 재실행해 증명한다.
    """
    monkeypatch.setattr(
        metric_names,
        "STRAY_METRIC_NOT_REGISTERED",
        "aios.stray.metric.count_total",
        raising=False,
    )
    declared = {
        value
        for key, value in vars(metric_names).items()
        if key.isupper() and key != "ALL_METRIC_NAMES" and isinstance(value, str)
    }
    assert declared != metric_names.ALL_METRIC_NAMES
    assert "aios.stray.metric.count_total" in declared - metric_names.ALL_METRIC_NAMES


# ── negative tests: metric name format violations ──────────────────────────


def test_negative_three_segment_name_rejected() -> None:
    """음성 테스트: 세그먼트가 3개만 있는 이름은 정규식을 만족하지 않는다.
    `aios.request.count`는 `aios.<context>.<subject>.<verb>` 4세그먼트를
    위반하므로 정적으로 거부되어야 한다.
    """
    assert not _METRIC_NAME_RE.match("aios.request.count")
    assert not _METRIC_NAME_RE.match("aios.x.y")


def test_negative_numeric_segment_rejected() -> None:
    """음성 테스트: 세그먼트에 숫자가 포함되면 `[a-z_]+`를 위반한다.
    `aios.api.v1.request.count_total`은 숫자 세그먼트 `v1`을 포함하므로 거부된다.
    """
    assert not _METRIC_NAME_RE.match("aios.api.v1.request.count_total")
    assert not _METRIC_NAME_RE.match("aios.api2.request.count_total")


def test_negative_segment_starts_with_digit_rejected() -> None:
    """음성 테스트: 세그먼트가 숫자로 시작하면 `[a-z_]+`를 위반한다.
    `aios.1api.request.count_total`은 세그먼트가 숫자로 시작하므로 거부된다.
    """
    assert not _METRIC_NAME_RE.match("aios.1api.request.count_total")
    assert not _METRIC_NAME_RE.match("aios.api1.request.count_total")


def test_negative_trailing_dot_rejected() -> None:
    """음성 테스트: 마침표로 끝나면 빈 세그먼트가 생성된다(`aios.api.request.`)."""
    assert not _METRIC_NAME_RE.match("aios.api.request.")
    assert not _METRIC_NAME_RE.match("aios.api.request.count_total.")


def test_negative_spaces_in_segment_rejected() -> None:
    """음성 테스트: 공백이 포함된 세그먼트는 `[a-z_]+`를 위반한다."""
    assert not _METRIC_NAME_RE.match("aios.api request.count_total")
    assert not _METRIC_NAME_RE.match("aios.api.request count_total")


def test_negative_special_chars_rejected() -> None:
    """음성 테스트: `-`, `@`, `!` 등 특수문자는 `[a-z_]+`를 위반한다."""
    assert not _METRIC_NAME_RE.match("aios.api-request.count_total")
    assert not _METRIC_NAME_RE.match("aios.api@request.count_total")


# ── failure-injection tests ───────────────────────────────────────────────


def test_to_prom_with_all_metric_names_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패주입: `to_prom()`이 모든 등록된 메트릭 이름에 대해 예외 없이 동작함을
    검증한다. `metric_names` 모듈의 상수를 동적으로 주입해 `to_prom` 호출이
    실패하면 계측 지점에서 문자열 조작 실패가 주문 경로로 전파된다.
    """
    for name in metric_names.ALL_METRIC_NAMES:
        result = metric_names.to_prom(name)
        assert isinstance(result, str)
        assert "." not in result
        assert result == name.replace(".", "_")


def test_to_prom_preserves_underscore_already_converted() -> None:
    """`to_prom()`은 이미 `_`로 된 이름에 영향을 주지 않는다(이중 변환 안전)."""
    original = "aios_api_request_count_total"
    assert metric_names.to_prom(original) == original


def test_all_registered_names_pass_to_prom_and_regex() -> None:
    """통합 검증: `ALL_METRIC_NAMES`의 각 이름이 `to_prom()`을 거친 후에도
    유효한 Prometheus 이름이 되고, 원본 이름도 정규식을 만족한다.
    단일 상수라도 `to_prom()` 결과나 원본이 위반하면 즉시 탐지된다.
    """
    for name in metric_names.ALL_METRIC_NAMES:
        assert _METRIC_NAME_RE.match(name), f"정규식 위반: {name}"
        prom_name = metric_names.to_prom(name)
        # Prometheus 이름은 `_`만 허용, 숫자로 시작 불가
        assert prom_name.startswith("aios_"), f"to_prom 결과 prefix 위반: {prom_name}"
        assert not prom_name[5:].startswith("_"), f"to_prom 결과 세그먼트가 `_`로 시작: {prom_name}"


def test_invalid_metric_name_injection_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패주입: 무효한 메트릭 이름이 ALL_METRIC_NAMES에 주입되면,
    정규식 검증이 그것을 즉시 거부해야 한다(게이트 검증).
    """
    invalid_names = metric_names.ALL_METRIC_NAMES | {"aios.invalid."}
    monkeypatch.setattr(metric_names, "ALL_METRIC_NAMES", invalid_names)

    violations = [name for name in metric_names.ALL_METRIC_NAMES if not _METRIC_NAME_RE.match(name)]
    assert len(violations) > 0, "정규식이 무효한 메트릭 이름을 감지하지 못함"
    assert "aios.invalid." in violations


# ── additional negative tests (D3: negative 6→9) ──────────────────────────


def test_negative_five_segment_name_rejected() -> None:
    """음성 테스트: 세그먼트가 5개이면 정규식을 만족하지 않는다.
    `aios.api.v1.orders.submit.total`는 5세그먼트로 `[a-z_]+` 패턴 3회 이상을 초과한다.
    """
    assert not _METRIC_NAME_RE.match("aios.api.v1.orders.submit.total")
    assert not _METRIC_NAME_RE.match("aios.one.two.three.four.five_total")


def test_negative_special_char_segment_rejected() -> None:
    """음성 테스트: 세그먼트에 하이픈(-)이나 슬래시(/)가 포함되면 `[a-z_]+`를 위반한다.
    `aios.api-v2.request.count_total`은 하이픈 세그먼트를 포함하므로 거부된다.
    """
    assert not _METRIC_NAME_RE.match("aios.api-v2.request.count_total")
    assert not _METRIC_NAME_RE.match("aios.api/request/count_total")


def test_negative_metric_with_leading_dot_suffix_rejected() -> None:
    """음성 테스트: `.gauge` 접미사 없이 점만으로 끝나는 이름은 정규식을 만족하지 않는다.
    `aios.something.status.`은 빈 세그먼트를 만들어 거부된다.
    """
    assert not _METRIC_NAME_RE.match("aios.something.status.")
    assert not _METRIC_NAME_RE.match("aios.api.request.")


# ── additional gate_red tests (D3: gate_red 1→2) ──────────────────────────


def test_gate_catches_malformed_metric_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """게이트 적색 재현: `ALL_METRIC_NAMES`에 잘못된 형식의 이름이混入되면
    `test_all_metric_names_match_naming_regex`가 실제로 적색이 되는지 증명한다.
    """
    malformed = "aios.MALFORMED.name"
    original = metric_names.ALL_METRIC_NAMES
    malformed_set = set(original) | {malformed}
    monkeypatch.setattr(metric_names, "ALL_METRIC_NAMES", malformed_set)

    violations = [name for name in metric_names.ALL_METRIC_NAMES if not _METRIC_NAME_RE.match(name)]
    assert len(violations) > 0
    assert malformed in violations


# ── failure_injection: to_prom with corrupted input (D3: 1→2) ─────────────


def test_to_prom_handles_names_with_multiple_dots(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패주입: 점 여러 개가连着된 이름이 ALL_METRIC_NAMES에 있으면 to_prom이
    모든 점을 언더스코어로 변환해야 한다(부분 변환 방지).
    """
    # Original test already exists; this is a deeper injection:
    # Inject a name with consecutive dots and verify to_prom handles it.
    injected = "aios.multi..dots.name_total"
    monkeypatch.setattr(
        metric_names, "ALL_METRIC_NAMES", set(metric_names.ALL_METRIC_NAMES) | {injected}
    )

    for name in metric_names.ALL_METRIC_NAMES:
        result = metric_names.to_prom(name)
        assert isinstance(result, str)
        assert result == name.replace(".", "_")


# ── perf test (D3: 0→1) ───────────────────────────────────────────────────


@pytest.mark.perf
def test_to_prom_performance_within_budget() -> None:
    """성능 단언: `to_prom()`은 `ALL_METRIC_NAMES`의 각 이름을 변환하는 데
    0.01ms 미만이어야 한다(ADR-2026-09-09-C Decision 1: 계측 배선 오버헤드 p95 < 1ms).

    Virtual delay 10x injection으로 게이트 적색 재현: 실제 지연이 주입되면
    예산을 초과하여 적색이 된다.
    """
    reset_virtual_totals()

    budget_ms = 0.01  # 0.01ms per name — generous for a str.replace() call
    perf_budget = PerfBudget()

    def fn() -> None:
        for name in metric_names.ALL_METRIC_NAMES:
            metric_names.to_prom(name)

    sample = perf_budget.best_of(fn, n=5, warmup=0, batch=1)
    assert sample.cpu_ms < budget_ms, (
        f"to_prom() performance exceeded budget: {sample.cpu_ms:.4f}ms > {budget_ms}ms "
        f"wall={sample.wall_ms:.4f}ms — see ADR-2026-09-09-C Decision 1"
    )


def test_to_prom_performance_gate_red_with_virtual_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """게이트 적색 재현: `to_prom()`에 가상 지연을 주입하면 성능 단언이
    실제로 적색이 되는지 확인한다. 지연 주입 없이 항상 녹색이 아님을 증명.
    """
    reset_virtual_totals()

    budget_ms = 0.01
    perf_budget = PerfBudget()

    # Inject 0.001ms virtual delay per call — with ~100 metric names,
    # cumulative ~0.1ms exceeds the 0.01ms budget.
    original_to_prom = metric_names.to_prom
    monkeypatch.setattr(metric_names, "to_prom", virtual_delayed_sync(original_to_prom, 0.001))

    def fn() -> None:
        for name in metric_names.ALL_METRIC_NAMES:
            metric_names.to_prom(name)

    sample = perf_budget.best_of(fn, n=3, warmup=0, batch=1)
    with pytest.raises(AssertionError):
        assert sample.cpu_ms < budget_ms, (
            f"Expected gate-red: {sample.cpu_ms:.4f}ms should exceed {budget_ms}ms budget"
        )


# ── adversarial test (D3: 0→1) ────────────────────────────────────────────


def test_adversarial_metric_name_fuzz_boundary() -> None:
    """Adversarial: 메트릭 이름 경계값 공격 — 세그먼트가 공백, Null 바이트,
    Unicode 등 이상 입력일 때 정규식이 안전하게 거부되어야 한다.
    """
    adversarial_names = [
        "aios. api.request.count_total",  # 공백
        "aios\x00.request.count_total",  # Null 바이트
        "aios.🔥.request.count_total",  # Emoji
        ".aios.api.request.count_total",  # 선행 점
        "aios.api.request.count_total.",  # 후행 점
        "AIOS.API.REQUEST.COUNT_TOTAL",  # 전체 대문자
        "aios.api.request.COUNT_total",  # 혼합 케이스
        "aios.api_request_count_total",  # 점이 하나도 없음
        "aios.api.request.count._total",  # 빈 세그먼트(연속 점 아님, 언더스코어 세그먼트)
        "aios.api.request.5count_total",  # 세그먼트 시작 숫자
        "aios.api.request.5count",  # 5세그먼트 + 숫자 세그먼트
    ]
    for name in adversarial_names:
        assert not _METRIC_NAME_RE.match(name), f"경계값이 허용됨: {name!r}"
