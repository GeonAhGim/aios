"""메트릭 이름 형식 전수 검증.

DoD(docs/specs/L4_platform_observability_tenancy_api_v1.0.md §9 PLT-04, §6):
`metric_names.py`의 상수 전부가 §6 단위테스트 표의 정규식을 만족해야 하고,
위반 상수가 1개라도 있으면 실패한다.
"""

from __future__ import annotations

import re

import pytest

from src.core.observability import metric_names

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


@pytest.mark.gate_red
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


@pytest.mark.failure_injection
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


@pytest.mark.failure_injection
def test_invalid_metric_name_injection_detected(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패주입: 무효한 메트릭 이름이 ALL_METRIC_NAMES에 주입되면,
    정규식 검증이 그것을 즉시 거부해야 한다(게이트 검증).
    """
    invalid_names = metric_names.ALL_METRIC_NAMES | {"aios.invalid."}
    monkeypatch.setattr(metric_names, "ALL_METRIC_NAMES", invalid_names)

    violations = [name for name in metric_names.ALL_METRIC_NAMES if not _METRIC_NAME_RE.match(name)]
    assert len(violations) > 0, "정규식이 무효한 메트릭 이름을 감지하지 못함"
    assert "aios.invalid." in violations


# ── performance test with perf_budget.best_of + perf_inject virtual delay ──────────────────────


@pytest.mark.perf
def test_perf_all_metric_names_regex_evaluation() -> None:
    """성능 단언: `ALL_METRIC_NAMES` 전원에 대한 정규식 매칭 총 소요 시간이
    예산 미만이어야 한다. `perf_budget.best_of`로 여러 번 중 최소값을 측정하고,
    `perf_inject.virtual_delayed_sync`로 가상 지연 10배(0.1s)를 주입해
    적색 재현 — 가상 지연이 주입되면 wall_ms가 예산을 초과해 적색으로 전환됨을
    확인한다.

    성능 예산: docs/ADR-2026-09-09-C Decision 1 —
    local实测 regex match 200개 × 40개 ≈ 0.05ms, 30배 여유 = 1.5ms."""
    from tests.support.perf_budget import PerfBudget
    from tests.support.perf_inject import virtual_delayed_sync

    # 전체 검증 로직을 virtual_delayed_sync로 감싸서 측정 구간 지연 주입
    original_validate = _METRIC_NAME_RE.match

    def _delayed_validate(string: str) -> re.Match[str] | None:
        """원래 match를 호출하되 가상 지연을 추가한다."""
        return original_validate(string)

    delayed_validate = virtual_delayed_sync(_delayed_validate, 0.1)

    # 적색 재현: 지연 주입 후 측정 → wall_ms가 예산 초과
    def _validate_with_delay() -> list[str]:
        return [name for name in metric_names.ALL_METRIC_NAMES if not delayed_validate(name)]

    perf_budget = PerfBudget()
    sample = perf_budget.best_of(_validate_with_delay, n=3)
    # 가상 지연 100ms × 40개 ≈ 4000ms wall → budget 1.5ms 초과 (적색 재현)
    assert sample.wall_ms > 1.5, (
        f"가상 지연 주입 후 wall_ms={sample.wall_ms:.1f}ms가 "
        f"예산 1.5ms를 초과해야 함 (적색 재현 실패)"
    )

    # 정상 경로: 지연 없이 측정 → 예산 내 통과
    def _validate_normal() -> list[str]:
        return [name for name in metric_names.ALL_METRIC_NAMES if not original_validate(name)]

    sample = perf_budget.best_of(_validate_normal, n=5)
    assert sample.cpu_ms < 1.5, (
        f"정상 regex 매칭 CPU 시간={sample.cpu_ms:.3f}ms가 예산 1.5ms를 초과함"
    )


# ── adversarial test ──────────────────────────────────────────────────────


@pytest.mark.adversarial
def test_adversarial_metric_name_edge_cases() -> None:
    """적대적 입력 테스트: 정규식 경계 조건을 체계적으로 공격한다.
    INVARIANTS.md I-04(메트릭 이름 형식 불변식) 검증 —
    각 케이스가 정규식을 위반하거나 정확히 매칭되는지를 단언한다.
    """
    # 정적 유효 케이스 — 정규식이 정확히_accept_해야 한다
    valid_cases = [
        "aios.api.request.count_total",
        "aios.order.submit.duration_seconds",
        "aios.risk.decision.count_total",
        "aios.safety.post_fence_side_effect.count_total",
        "aios.loop.tick.count_total",
        "aios.adapter.request.duration_seconds",
        "aios.auth.login.count_total",
        "aios.audit.append.count_total",
        "aios.security.secret_decrypt.count_total",
        "aios.readiness.check.status",
        "aios.positions.reconciliation.mismatch_count_total",
        "aios.order.unknown_state.gauge",
        "aios.positions.scheduler_cycle_success.gauge",
        "aios.event_bus.queue_depth.gauge",
    ]
    for name in valid_cases:
        assert _METRIC_NAME_RE.match(name), f"정규식이 유효한 이름을 거부: {name}"

    # 정적 무효 케이스 — 정규식이 정확히_reject_해야 한다
    invalid_cases = [
        "",  # 빈 문자열
        "aios",  # 세그먼트 1개
        "aios.api",  # 세그먼트 2개
        "aios.api.request",  # 세그먼트 3개 (4개 필요)
        "aios.API.request.count_total",  # 대문자
        "aios.api.v1.request.count_total",  # 숫자 포함
        "aios.api-.request.count_total",  # 특수문자
        "aios.api.request.count_total.extra",  # 5세그먼트 (마지막 .extra가 regex 허용 안 함)
        "aios.api..request.count_total",  # 빈 세그먼트
        "aios.api.request.",  # trailing dot
        "aios api.request.count_total",  # 공백
        "AIOS.api.request.count_total",  # 전체 대문자 prefix
        ".aios.api.request.count_total",  # 선행 dot
        "aios.api.request.count_total.",  # 후행 dot
    ]
    for name in invalid_cases:
        assert not _METRIC_NAME_RE.match(name), f"정규식이 무효한 이름을 허용: {name}"
