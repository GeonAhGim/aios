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
