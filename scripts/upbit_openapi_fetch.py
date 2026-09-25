"""Upbit REST/WebSocket endpoint reference -- BR-21(task-7147, CTO 2026-09-25 approval).

Unlike NH (`nh_openapi_fetch.py`), Upbit does not publish a machine-readable
OpenAPI/Swagger JSON at any stable public URL -- `docs.upbit.com` is a
client-rendered ReadMe.io SPA; its reference pages hydrate via an
authenticated internal API, so there is no `openapi.json` to parse (verified
2026-09-25: `/openapi.json`, `/reference/openapi.json`,
`/kr/reference/openapi.json` all 301/404; `dash.readme.com/api/v1/
api-specification` requires an API key we do not have).

Given that, extraction here works differently: `_CANDIDATE_ENDPOINTS` below
is a human-curated list of Upbit's documented REST/WebSocket surface (from
`docs.upbit.com`), and this script's network step verifies each candidate is
real by probing it live against `api.upbit.com` -- REST candidates get an
HTTP request and are confirmed on `200`/`401` (401 means the route exists and
enforces auth; 404 would mean it doesn't), the WebSocket candidate is
confirmed by completing the opening handshake (`101 Switching Protocols`).
Only confirmed entries are written to the reference snapshot -- an
unconfirmed candidate is dropped and printed as a warning, never guessed
into the output. This keeps the same offline/online split as NH: only this
script touches the network; `upbit_openapi_coverage.py` reads the resulting
snapshot file and makes no network calls.

Usage: `python scripts/upbit_openapi_fetch.py` (from the repo root). Exit
code 0 = snapshot written (possibly with some candidates dropped), 1 = no
candidate could be confirmed at all.
"""

from __future__ import annotations

import json
import socket
import ssl
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "design" / "upbit_openapi_reference.json"

DOCS_SOURCE_URL = "https://docs.upbit.com/kr/reference"
REST_BASE_URL = "https://api.upbit.com"
WS_HOST = "api.upbit.com"
USER_AGENT = "aios-upbit-openapi-coverage/1"
FETCH_TIMEOUT = 10.0

Kind = Literal["REST", "WS"]


class UpbitOpenAPIFetchError(RuntimeError):
    """No candidate endpoint could be confirmed live."""


class _CandidateEndpoint:
    __slots__ = ("path", "method", "kind", "summary", "query")

    def __init__(
        self,
        path: str,
        method: str,
        kind: Kind,
        summary: str,
        query: str = "",
    ) -> None:
        self.path = path
        self.method = method
        self.kind = kind
        self.summary = summary
        # Required query params so public quotation endpoints answer 200
        # instead of 400 (missing-param) -- a 400 doesn't confirm the route
        # exists, so these must be filled in for an honest probe.
        self.query = query


# Curated from docs.upbit.com's "시세 종목 조회"/"자산"/"주문"/"입출금"/"웹소켓"
# sections. `method` for WS entries encodes the subscribed channel type
# (Upbit multiplexes all WS channels over the single `/websocket/v1` path;
# the channel is chosen by the subscription message body, not the URL).
_CANDIDATE_ENDPOINTS: tuple[_CandidateEndpoint, ...] = (
    _CandidateEndpoint("/v1/market/all", "GET", "REST", "마켓 코드 조회"),
    _CandidateEndpoint(
        "/v1/candles/seconds", "GET", "REST", "초(Second) 캔들 조회", "market=KRW-BTC&count=1"
    ),
    _CandidateEndpoint(
        "/v1/candles/minutes/1", "GET", "REST", "분(Minute) 캔들 조회", "market=KRW-BTC&count=1"
    ),
    _CandidateEndpoint(
        "/v1/candles/days", "GET", "REST", "일(Day) 캔들 조회", "market=KRW-BTC&count=1"
    ),
    _CandidateEndpoint(
        "/v1/candles/weeks", "GET", "REST", "주(Week) 캔들 조회", "market=KRW-BTC&count=1"
    ),
    _CandidateEndpoint(
        "/v1/candles/months", "GET", "REST", "월(Month) 캔들 조회", "market=KRW-BTC&count=1"
    ),
    _CandidateEndpoint(
        "/v1/trades/ticks", "GET", "REST", "최근 체결 내역 조회", "market=KRW-BTC&count=1"
    ),
    _CandidateEndpoint("/v1/ticker", "GET", "REST", "현재가 정보 조회", "markets=KRW-BTC"),
    _CandidateEndpoint("/v1/orderbook", "GET", "REST", "호가 정보 조회", "markets=KRW-BTC"),
    _CandidateEndpoint(
        "/v1/orderbook/instruments",
        "GET",
        "REST",
        "호가 모아보기 지원 정보 조회",
        "markets=KRW-BTC",
    ),
    _CandidateEndpoint("/v1/accounts", "GET", "REST", "전체 계좌 조회"),
    _CandidateEndpoint("/v1/orders/chance", "GET", "REST", "주문 가능 정보 조회"),
    _CandidateEndpoint("/v1/order", "GET", "REST", "개별 주문 조회"),
    _CandidateEndpoint("/v1/orders/open", "GET", "REST", "체결 대기 주문 조회"),
    _CandidateEndpoint("/v1/orders/closed", "GET", "REST", "종료된 주문 조회"),
    _CandidateEndpoint("/v1/orders/uuids", "GET", "REST", "UUID로 주문 리스트 조회"),
    _CandidateEndpoint("/v1/orders", "POST", "REST", "주문하기"),
    _CandidateEndpoint("/v1/orders/cancel_and_new", "POST", "REST", "취소 후 재주문"),
    _CandidateEndpoint("/v1/order", "DELETE", "REST", "주문 취소 접수"),
    _CandidateEndpoint("/v1/orders/open", "DELETE", "REST", "주문 일괄 취소 접수"),
    _CandidateEndpoint("/v1/withdraws", "GET", "REST", "출금 리스트 조회"),
    _CandidateEndpoint("/v1/withdraw", "GET", "REST", "개별 출금 조회"),
    _CandidateEndpoint("/v1/withdraws/chance", "GET", "REST", "출금 가능 정보 조회"),
    _CandidateEndpoint("/v1/withdraws/coin", "POST", "REST", "코인 출금하기"),
    _CandidateEndpoint("/v1/withdraws/krw", "POST", "REST", "원화 출금하기"),
    _CandidateEndpoint("/v1/withdraws/coin_addresses", "GET", "REST", "출금 허용 주소 조회"),
    _CandidateEndpoint("/v1/deposits", "GET", "REST", "입금 리스트 조회"),
    _CandidateEndpoint("/v1/deposit", "GET", "REST", "개별 입금 조회"),
    _CandidateEndpoint("/v1/deposits/generate_coin_address", "POST", "REST", "입금 주소 생성 요청"),
    _CandidateEndpoint("/v1/deposits/coin_addresses", "GET", "REST", "전체 입금 주소 조회"),
    _CandidateEndpoint("/v1/deposits/coin_address", "GET", "REST", "개별 입금 주소 조회"),
    _CandidateEndpoint("/v1/deposits/krw", "POST", "REST", "원화 입금하기"),
    _CandidateEndpoint("/v1/status/wallet", "GET", "REST", "입출금 현황 조회"),
    _CandidateEndpoint("/v1/api_keys", "GET", "REST", "API 키 리스트 조회"),
    _CandidateEndpoint("/websocket/v1", "WS:ticker", "WS", "실시간 현재가(ticker) 구독"),
    _CandidateEndpoint("/websocket/v1", "WS:trade", "WS", "실시간 체결(trade) 구독"),
    _CandidateEndpoint("/websocket/v1", "WS:orderbook", "WS", "실시간 호가(orderbook) 구독"),
    _CandidateEndpoint(
        "/websocket/v1", "WS:myOrder", "WS", "실시간 내 주문/체결(myOrder) 구독 -- 인증 필요"
    ),
    _CandidateEndpoint(
        "/websocket/v1", "WS:myAsset", "WS", "실시간 내 자산(myAsset) 구독 -- 인증 필요"
    ),
)


def rest_status_confirms_endpoint(status_code: int) -> bool:
    """200 = public endpoint answered; 401 = private endpoint exists but needs auth."""
    return status_code in (200, 401)


def ws_status_confirms_endpoint(status_code: int) -> bool:
    """101 Switching Protocols = the WS upgrade handshake succeeded."""
    return status_code == 101


def _probe_rest(path: str, method: str, query: str = "", timeout: float = FETCH_TIMEOUT) -> int:
    url = f"{REST_BASE_URL}{path}?{query}" if query else f"{REST_BASE_URL}{path}"
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT})  # noqa: S310
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return int(resp.status)
    except urllib.error.HTTPError as exc:
        return exc.code


def _ws_nonce() -> str:
    """RFC 6455 Sec-WebSocket-Key: 16바이트 난수의 base64. RFC 예제 상수를 리터럴로 두면
    gitleaks(Quality Gate Secret scan)가 시크릿으로 잡아 매 실행 적색이었다(CTO 2026-09-25)."""
    import base64
    import os

    return base64.b64encode(os.urandom(16)).decode("ascii")


def _probe_ws(path: str, timeout: float = FETCH_TIMEOUT) -> int:
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {WS_HOST}\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: websocket\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Key: {_ws_nonce()}\r\n"
        "\r\n"
    )
    ctx = ssl.create_default_context()
    with socket.create_connection((WS_HOST, 443), timeout=timeout) as sock:
        with ctx.wrap_socket(sock, server_hostname=WS_HOST) as tls:
            tls.sendall(request.encode("ascii"))
            tls.settimeout(timeout)
            raw = tls.recv(64)
    status_line = raw.split(b"\r\n", 1)[0].decode("ascii", errors="replace")
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or not parts[1].isdigit():
        raise UpbitOpenAPIFetchError(f"WS 핸드셰이크 응답이 예상 형식이 아님: {status_line!r}")
    return int(parts[1])


def probe_candidate(candidate: _CandidateEndpoint) -> tuple[int, bool]:
    """Probe one candidate live; returns (status_code, confirmed)."""
    if candidate.kind == "WS":
        status = _probe_ws(candidate.path)
        return status, ws_status_confirms_endpoint(status)
    status = _probe_rest(candidate.path, candidate.method, candidate.query)
    return status, rest_status_confirms_endpoint(status)


def build_reference(
    confirmed: list[tuple[_CandidateEndpoint, int]], fetched_at: str
) -> dict[str, Any]:
    """Pure assembly step -- no network. Kept separate so it's unit-testable offline."""
    endpoints: list[dict[str, Any]] = [
        {
            "path": c.path,
            "method": c.method,
            "kind": c.kind,
            "summary": c.summary,
            "description": "",
            "verified_status": status,
            "request_params": [],
            "response_schema": None,
        }
        for c, status in sorted(confirmed, key=lambda pair: (pair[0].path, pair[0].method))
    ]
    return {
        "source_url": DOCS_SOURCE_URL,
        "extraction_method": (
            "no public OpenAPI/Swagger JSON exists for docs.upbit.com (ReadMe.io SPA, "
            "requires an authenticated internal API to hydrate reference pages); entries "
            "are human-curated candidates confirmed live against api.upbit.com "
            "(REST: 200/401, WS: 101 handshake) -- unconfirmed candidates are dropped"
        ),
        "fetched_at": fetched_at,
        "spec_version": "unversioned (curated + live-verified, not machine-parsed)",
        "spec_title": "Upbit Open API (Quotation/Exchange/WebSocket)",
        "endpoint_count": len(endpoints),
        "endpoints": endpoints,
    }


def fetch_and_save(output: Path = DEFAULT_OUTPUT) -> int:
    confirmed: list[tuple[_CandidateEndpoint, int]] = []
    dropped: list[tuple[_CandidateEndpoint, int | str]] = []

    for candidate in _CANDIDATE_ENDPOINTS:
        try:
            status, ok = probe_candidate(candidate)
        except (OSError, UpbitOpenAPIFetchError) as exc:
            dropped.append((candidate, str(exc)))
            continue
        if ok:
            confirmed.append((candidate, status))
        else:
            dropped.append((candidate, status))

    if not confirmed:
        print("FAIL: 확인된 엔드포인트가 하나도 없음")
        return 1

    for candidate, why in dropped:
        print(f"DROP: {candidate.method} {candidate.path} ({why}) -- 기준 목록에서 제외")

    reference = build_reference(confirmed, datetime.now(timezone.utc).isoformat(timespec="seconds"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(reference, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        f"OK: {len(confirmed)}개 확인(드롭 {len(dropped)}개) -> {output} "
        f"(fetched: {reference['fetched_at']})"
    )
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    return fetch_and_save()


if __name__ == "__main__":
    raise SystemExit(main())
