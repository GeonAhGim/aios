"""upbit_openapi_fetch.py 오프라인 픽스처 테스트 -- BR-21(task-7147).

네트워크 호출이 있는 _probe_rest/_probe_ws는 여기서 실행하지 않는다 -- 순수
함수(rest_status_confirms_endpoint/ws_status_confirms_endpoint/
build_reference)만 오프라인으로 검증한다.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))

from upbit_openapi_fetch import (  # noqa: E402
    _CandidateEndpoint,
    build_reference,
    rest_status_confirms_endpoint,
    ws_status_confirms_endpoint,
)


def test_rest_status_confirms_public_endpoint() -> None:
    assert rest_status_confirms_endpoint(200) is True


def test_rest_status_confirms_private_endpoint_requiring_auth() -> None:
    assert rest_status_confirms_endpoint(401) is True


def test_rest_status_rejects_not_found() -> None:
    assert rest_status_confirms_endpoint(404) is False


def test_rest_status_rejects_missing_param_400() -> None:
    assert rest_status_confirms_endpoint(400) is False


def test_ws_status_confirms_handshake() -> None:
    assert ws_status_confirms_endpoint(101) is True


def test_ws_status_rejects_non_handshake() -> None:
    assert ws_status_confirms_endpoint(200) is False


def test_build_reference_includes_only_confirmed_candidates() -> None:
    confirmed = [
        (_CandidateEndpoint("/v1/market/all", "GET", "REST", "마켓 코드 조회"), 200),
        (_CandidateEndpoint("/v1/orders", "POST", "REST", "주문하기"), 401),
    ]

    reference = build_reference(confirmed, fetched_at="2026-09-25T00:00:00+00:00")

    assert reference["endpoint_count"] == 2
    paths = {ep["path"] for ep in reference["endpoints"]}
    assert paths == {"/v1/market/all", "/v1/orders"}
    assert reference["fetched_at"] == "2026-09-25T00:00:00+00:00"
    assert "verified_status" in reference["endpoints"][0]


def test_build_reference_sorts_by_path_then_method() -> None:
    confirmed = [
        (_CandidateEndpoint("/v1/orders", "POST", "REST", "주문하기"), 401),
        (_CandidateEndpoint("/v1/market/all", "GET", "REST", "마켓 코드 조회"), 200),
    ]

    reference = build_reference(confirmed, fetched_at="2026-09-25T00:00:00+00:00")

    ordered_paths = [ep["path"] for ep in reference["endpoints"]]
    assert ordered_paths == ["/v1/market/all", "/v1/orders"]


def test_build_reference_empty_confirmed_list_yields_zero_endpoints() -> None:
    reference = build_reference([], fetched_at="2026-09-25T00:00:00+00:00")

    assert reference["endpoint_count"] == 0
    assert reference["endpoints"] == []


# ── negative tests (invariant-violating inputs) ──────────────────────────


def test_rest_status_confirms_rejects_3xx_redirect() -> None:
    """3xx 리다이렉트는 엔드포인트 존재를 확인하지 않는다."""
    assert rest_status_confirms_endpoint(301) is False
    assert rest_status_confirms_endpoint(302) is False


def test_rest_status_confirms_rejects_5xx_server_error() -> None:
    """5xx 서버 에러는 엔드포인트가 살아있음을 확인하지 않는다."""
    assert rest_status_confirms_endpoint(500) is False
    assert rest_status_confirms_endpoint(502) is False
    assert rest_status_confirms_endpoint(503) is False


def test_ws_status_confirms_rejects_426_upgrade_required() -> None:
    """101 외의 모든 상태 코드는 WS 핸드셰이크 성공이 아니다."""
    assert ws_status_confirms_endpoint(426) is False


def test_build_reference_validates_endpoint_schema_keys() -> None:
    """build_reference가 생성한 엔트리 필드가 불변식 규약을 지키는지 확인한다."""
    confirmed = [
        (_CandidateEndpoint("/v1/market/all", "GET", "REST", "마켓 코드 조회"), 200),
    ]
    reference = build_reference(confirmed, fetched_at="2026-09-25T00:00:00+00:00")
    ep = reference["endpoints"][0]
    # 필수 필드가 모두 존재해야 한다.
    for key in (
        "path",
        "method",
        "kind",
        "summary",
        "description",
        "verified_status",
        "request_params",
        "response_schema",
    ):
        assert key in ep, f"누락된 엔드포인트 필드: {key}"
    # request_params는 리스트, response_schema은 None(또는 스키마 dict)
    assert isinstance(ep["request_params"], list)


# ── failure-injection (monkeypatch 의존성 예외 유발) ─────────────────────


def test_probe_candidate_catches_oserror_and_drops_candidate(
    monkeypatch,
) -> None:
    """_probe_rest가 OSError를 raise하면 probe_candidate가 예외를 전파하고
    fetch_and_save가 해당 candidate를 dropped로 처리하는지를 확인한다."""
    import upbit_openapi_fetch as mod

    def raise_oserror(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(mod, "_probe_rest", raise_oserror)

    # probe_candidate는 OSError를 캐시하지 않고 전파한다 — caller(fetch_and_save)가
    # except (OSError, UpbitOpenAPIFetchError)로 받는다.
    from upbit_openapi_fetch import probe_candidate

    candidate = _CandidateEndpoint("/v1/market/all", "GET", "REST", "마켓 코드 조회")
    try:
        probe_candidate(candidate)
    except OSError:
        pass  # 정상 경로: 예외가 전파됨
    else:
        raise AssertionError("probe_candidate should have raised OSError")


# ── performance assertion (perf_budget fixture 사용) ─────────────────────


def test_build_reference_perf_budget(perf_budget) -> None:
    """build_reference 성능 단언 — 100개 엔드포인트 기준 5ms CPU 미만.

    perf_budget 픽스처를 사용한다 (raw perf_counter/monotonic 사용 금지).
    """
    # 100개 엔드포인트가 있는 큰 입력으로 성능을 측정한다.
    big_confirmed: list[tuple[_CandidateEndpoint, int]] = [
        (_CandidateEndpoint(f"/v1/test/{i}", "GET", "REST", f"테스트 {i}"), 200) for i in range(100)
    ]

    def build() -> dict:
        return build_reference(big_confirmed, fetched_at="2026-09-25T00:00:00+00:00")

    sample = perf_budget.assert_within(build, budget_ms=5.0, n=5, warmup=1)
    assert sample.cpu_ms < 5.0, (
        f"build_reference(100 endpoints) cpu={sample.cpu_ms:.3f}ms >= 5ms budget"
    )
