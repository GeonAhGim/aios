"""KIS 예제 원문 순수 파서 — BR-11(ADR-2026-09-06-I D7).

I/O 없음(정규식만). `kis_tr_fetch.py`가 네트워크로 받아온 예제 파일 원문을 넘기면
tr_id·라벨에 더해 path·HTTP 메서드·파라미터(필수/선택)·응답 필드를 뽑아낸다.

공식 예제(`examples_llm/**/*.py`)는 실행되는 코드이므로 문서보다 정확하다는 것이
ADR D7의 전제다 — 그래서 모든 정규식은 코드 리터럴(`API_URL = "..."`, `params = {...}`,
`getBody().output*`, `postFlag=True`, 함수 시그니처의 기본값 유무)만 본다. 예제가 이
패턴을 벗어나면(신규 KIS 저장소 구조 변경 등) 조용히 빈 값을 채우지 않고 `error`에
사유를 남긴다 — 호출자가 그 사유를 열거해야 한다(ADR D2.4 "조용히 빠지는 TR이 없다"와
같은 원칙).

관찰된 예제 구조(2026-09-07, `koreainvestment/open-trading-api@main`, 330개 파일 전수
확인):
  - REST: 모듈 상단 `API_URL = "/uapi/..."` 상수 + `ka._url_fetch(API_URL, tr_id, tr_cont,
    params, ..., postFlag=True/생략)`. `postFlag` 생략 시 기본값 False → GET(`_url_fetch`
    정의 자체가 그렇다). 응답은 `res.getBody().output`/`output1..4`/`outblock1` 중 실제
    접근한 컨테이너만 있고, 필드 단위 이름은 코드에 없다(동적 접근이라 예제에 안 남는다).
  - WebSocket: `ka.data_fetch(tr_id, tr_type, params)` + 리턴 직전 `columns = [...]` 리스트
    — 이 경우에만 필드명이 예제에 그대로 있다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_TR_ID_RE = re.compile(r"""tr_id\s*=\s*["']([A-Z][A-Z0-9]{5,13})["']""")
_HEADER_RE = re.compile(r"^#\s*(\[.+?\].+)$", re.MULTILINE)

_API_URL_RE = re.compile(r'^[ \t]*[A-Z_][A-Z0-9_]*\s*=\s*["\'](/[^"\']+)["\']', re.MULTILINE)
_URL_FETCH_CALL_RE = re.compile(r"_url_fetch\(([^)]*)\)", re.DOTALL)
_DATA_FETCH_MARKER = "data_fetch("
_POST_FLAG_TRUE_RE = re.compile(r"postFlag\s*=\s*True")

_PARAMS_BLOCK_RE = re.compile(r"\bparams\s*=\s*\{(.*?)\n[ \t]*\}", re.DOTALL)
_PARAMS_EMPTY_RE = re.compile(r"\bparams\s*=\s*\{\s*\}")
_PARAM_ENTRY_RE = re.compile(r'"([A-Za-z0-9_]+)"\s*:\s*([^,\n#]+)')

_RESPONSE_CONTAINER_RE = re.compile(r"getBody\(\)\.((?:output|outblock)\d*)\b")
_WS_COLUMNS_RE = re.compile(r"\bcolumns\s*=\s*\[(.*?)\n[ \t]*\]", re.DOTALL)
_WS_COLUMN_ITEM_RE = re.compile(r'"([a-zA-Z0-9_]+)"')

_DEF_RE = re.compile(r"\bdef\s+\w+\s*\(")
_PARAM_DEF_RE = re.compile(r"^([A-Za-z_]\w*)\s*(?::\s*[^=]+)?(?:=\s*(.+))?$")


@dataclass(frozen=True)
class ParamSpec:
    name: str
    required: bool

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "required": self.required}


@dataclass(frozen=True)
class RequestMeta:
    path: str | None
    method: str | None  # "GET" | "POST" | "WS"
    params: tuple[ParamSpec, ...]
    error: str | None


@dataclass(frozen=True)
class ResponseMeta:
    kind: str | None  # "rest" | "ws"
    containers: tuple[str, ...]
    fields: tuple[str, ...]
    error: str | None


def _strip_line_comments(sig: str) -> str:
    return "\n".join(line[: line.find("#")] if "#" in line else line for line in sig.split("\n"))


def _split_top_level(sig: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    buf: list[str] = []
    for ch in sig:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return parts


def parse_def_args(text: str) -> tuple[frozenset[str], frozenset[str]]:
    """예제 함수의 시그니처에서 (필수 인자명, 선택 인자명)을 뽑는다.

    기본값이 없으면 필수, 있으면 선택 — 독스트링의 "[필수]" 표기는 사람이 쓴 프로즈라
    믿지 않는다(ADR D7: 예제 코드 자체만 신뢰). 괄호 깊이를 세어 첫 `def NAME(`의 짝이
    맞는 `)`까지만 시그니처로 본다.
    """
    match = _DEF_RE.search(text)
    if not match:
        return frozenset(), frozenset()
    start = match.end()
    depth = 1
    i = start
    while i < len(text) and depth > 0:
        if text[i] in "([{":
            depth += 1
        elif text[i] in ")]}":
            depth -= 1
        i += 1
    signature = _strip_line_comments(text[start : i - 1])
    required: set[str] = set()
    optional: set[str] = set()
    for part in _split_top_level(signature):
        part = part.strip()
        if not part or part.startswith("*"):
            continue
        arg_match = _PARAM_DEF_RE.match(part)
        if not arg_match:
            continue
        name = arg_match.group(1)
        if arg_match.group(2) is not None:
            optional.add(name)
        else:
            required.add(name)
    return frozenset(required), frozenset(optional)


def extract_request_meta(text: str) -> RequestMeta:
    """path·HTTP 메서드·파라미터를 뽑는다. 실패하면 error에 사유를 남긴다(여러 개면 이어붙임)."""
    required, _optional = parse_def_args(text)
    params, params_error = _extract_params(text, required)

    if _DATA_FETCH_MARKER in text:
        return RequestMeta(path=None, method="WS", params=params, error=params_error)

    urls = dict.fromkeys(_API_URL_RE.findall(text))
    errors = [params_error] if params_error else []
    if not urls:
        errors.append("API_URL 상수도 data_fetch(...) 호출도 찾지 못함 — 예제 구조 변경 의심")
        return RequestMeta(path=None, method=None, params=params, error="; ".join(errors))
    if len(urls) > 1:
        errors.append(f"API_URL 후보가 여러 개({sorted(urls)}) — 어느 것이 이 TR의 path인지 불명확")
        return RequestMeta(path=None, method=None, params=params, error="; ".join(errors))
    path = next(iter(urls))

    call_match = _URL_FETCH_CALL_RE.search(text)
    if not call_match:
        errors.append("API_URL은 있으나 _url_fetch(...) 호출을 찾지 못해 HTTP 메서드 불명")
        return RequestMeta(path=path, method=None, params=params, error="; ".join(errors))
    method = "POST" if _POST_FLAG_TRUE_RE.search(call_match.group(1)) else "GET"
    return RequestMeta(path=path, method=method, params=params, error="; ".join(errors) or None)


def _extract_params(
    text: str, required: frozenset[str]
) -> tuple[tuple[ParamSpec, ...], str | None]:
    block_match = _PARAMS_BLOCK_RE.search(text)
    if not block_match:
        if _PARAMS_EMPTY_RE.search(text):
            return (), None
        return (), "params={...} 딕셔너리 리터럴을 찾지 못함 — 예제 구조 변경 의심"
    specs: list[ParamSpec] = []
    seen: set[str] = set()
    for key, value in _PARAM_ENTRY_RE.findall(block_match.group(1)):
        if key in seen:
            continue
        seen.add(key)
        value = value.strip().rstrip(",").strip()
        specs.append(ParamSpec(name=key, required=value in required))
    return tuple(specs), None


def extract_response_meta(text: str) -> ResponseMeta:
    """응답 컨테이너(REST) 또는 필드명(WebSocket)을 뽑는다."""
    if _DATA_FETCH_MARKER in text:
        columns_match = _WS_COLUMNS_RE.search(text)
        if not columns_match:
            return ResponseMeta(
                kind="ws",
                containers=(),
                fields=(),
                error="websocket 예제인데 columns=[...] 리스트를 찾지 못함",
            )
        fields = tuple(_WS_COLUMN_ITEM_RE.findall(columns_match.group(1)))
        return ResponseMeta(kind="ws", containers=(), fields=fields, error=None)

    containers = tuple(sorted(dict.fromkeys(_RESPONSE_CONTAINER_RE.findall(text))))
    if not containers:
        return ResponseMeta(
            kind="rest",
            containers=(),
            fields=(),
            error="getBody().output* 접근을 찾지 못해 응답 컨테이너 불명",
        )
    return ResponseMeta(kind="rest", containers=containers, fields=(), error=None)


def extract_trs_from_text(text: str, path: str) -> list[dict[str, Any]]:
    """한 예제 파일 원문에서 TR 행 전부를 뽑는다(순수 함수 — 네트워크 없음).

    `tr_id = "..."` 대입문이 하나도 없으면 이 파일은 TR을 정의하지 않는 것이므로 빈
    목록을 반환한다(체크 러너 등). path·method·params·응답은 파일(엔드포인트) 단위로
    한 번만 계산해 그 파일에서 나온 모든 tr_id 행에 동일하게 붙인다 — 실전/모의 tr_id가
    같은 함수 안에서 갈리는 예제(주문 등)도 요청 구조 자체는 하나이기 때문이다.
    """
    tr_ids = dict.fromkeys(_TR_ID_RE.findall(text))
    if not tr_ids:
        return []
    header_match = _HEADER_RE.search(text)
    label = header_match.group(1).strip() if header_match else ""
    domain = path.split("/")[1]

    request = extract_request_meta(text)
    response = extract_response_meta(text)
    errors = [err for err in (request.error, response.error) if err]
    extraction_error = "; ".join(errors) if errors else None

    row_base = {
        "domain": domain,
        "label": label,
        "source_path": path,
        "path": request.path,
        "method": request.method,
        "params": [p.as_dict() for p in request.params],
        "response": {
            "kind": response.kind,
            "containers": list(response.containers),
            "fields": list(response.fields),
        },
        "extraction_error": extraction_error,
    }
    return [{"tr_id": tr_id, **row_base} for tr_id in tr_ids]
