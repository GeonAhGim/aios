"""KIS 어댑터 메서드 자동 생성 — BR-12(ADR-2026-09-06-I D7).

BR-11(`kis_tr_reference.json`)에서 `kis_tr_coverage.classify()` 기준 "미착수"로
분류되는 TR을 그대로 기계 생성한다. 수기 작성 금지 — 예외는 `_EXCEPTION_OVERRIDES`
(현재 0건, `kis_tr_coverage._SCOPE_OVERRIDES`와 동일 원칙)에 tr_id와 근거를 남기고
필드를 보정하는 것만 허용한다.

생성 대상 판정은 `kis_tr_coverage.classify()`를 그대로 재사용한다 — 매트릭스와
생성물이 서로 다른 기준으로 "미착수"를 정의하면 어긋난다. 스캔 대상은 수기 작성
파일만(`src/exchanges/kis/*.py`, 하위 `generated/`는 재귀에 포함하지 않음) —
생성물 자체를 스캔에 포함하면 재생성마다 대상 집합이 줄어드는 피드백 루프가 된다.

생성물은 요청 조립(REST: method·path·tr_id·params/body, WS: 구독 tr_id)만 하고
응답은 파싱하지 않은 채 그대로 돌려준다 — 필드 단위 타입 매핑은 기계 추출로 알 수
없는 도메인 지식이라 손대지 않는다(ADR D7: 계정 없이 진행, 계약 테스트는 요청
조립·원문 왕복만 확인). `# type: ignore`를 늘리지 않으려고(PLT-40 예산 래칫)
`generated/_protocols.py`의 Protocol 베이스로 구조적 타입을 제공한다 — 실제
구현은 `KISAdapter`의 다른 mixin이 MRO 앞쪽에서 채운다.

결정론: 같은 `kis_tr_reference.json` + 같은 수기 파일 집합이면 바이트 동일 출력.
tr_id 오름차순 정렬, 고정 폭 텍스트랩(textwrap), 라인 예산 기반 청크 분할.

주문성 하드가드(review:1971 REJECT 후속, 레드팀 #2026-09-02-32와 동일 결함
클래스): `is_order_method()`가 `method == "POST"`인 행을 주문성으로 판정해
`@require_paper_sandbox`를 자동 방출한다. 손으로 쓴 `trading_mixin.py`가 가진
가드를 생성기가 재현하지 못해 19건이 무방비였던 결함(task-1975)을 이름 규칙이
아니라 TR 메타데이터로 고쳤다 — 이 기준 목록의 POST 48건은 예외 없이
매수/매도/정정/취소/예약주문이라(레이블에 "주문" 포함, 코드 리뷰로 확인) 이
저장소 안에서는 method 하나로 충분하다.

사용: `python scripts/kis_generate_adapters.py` (저장소 루트에서). 종료코드
0=생성 완료, 1=입력 오류(기준 목록 없음/형식 오류/미지원 메서드).
"""
from __future__ import annotations

import importlib.util
import re
import sys
import unicodedata
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = ROOT / "docs" / "design" / "kis_tr_reference.json"
ADAPTER_DIR = ROOT / "src" / "exchanges" / "kis"
GENERATED_DIR = ADAPTER_DIR / "generated"

# 파일당 300줄 상한(BR-12 DoD) — 헤더(모듈 docstring+import)에 넉넉히 40줄을 남기고
# 본문(메서드 텍스트)은 260줄 예산으로 청크를 채운다.
_FILE_LINE_CAP = 300
_CHUNK_BODY_BUDGET = 260

# 예외 오버라이드 표 — ADR 근거와 함께 tr_id별 필드(path/method/params/response)를
# 보정할 때만 채운다(`kis_tr_coverage._SCOPE_OVERRIDES`와 동일 원칙). 현재 BR-11
# 기계 추출 결과에 추출 오류가 0건이라 비어 있다.
_EXCEPTION_OVERRIDES: dict[str, dict[str, Any]] = {}


class KisGenerateError(ValueError):
    """기준 목록 형식 오류 또는 생성 불가능한 행(미지원 HTTP 메서드 등)."""


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise KisGenerateError(f"모듈 로드 실패: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_coverage = _load_module("kis_tr_coverage", Path(__file__).resolve().parent / "kis_tr_coverage.py")


def scan_handwritten_source(adapter_dir: Path) -> str:
    """수기 작성 파일만(비재귀) 스캔한다 — `generated/`는 하위 디렉터리라 제외된다."""
    files = sorted(adapter_dir.glob("*.py"))
    return "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)


def select_todo_rows(reference: dict[str, Any], handwritten_source: str) -> list[dict[str, Any]]:
    trs: list[dict[str, Any]] = reference["trs"]
    all_ids = frozenset(r["tr_id"] for r in trs)
    todo = []
    for row in sorted(trs, key=lambda r: str(r["tr_id"])):
        implemented = row["tr_id"] in handwritten_source
        reason = _coverage.classify(row["tr_id"], all_ids, implemented)
        if reason == "미착수":
            todo.append({**row, **_EXCEPTION_OVERRIDES.get(row["tr_id"], {})})
    return todo


def _pascal(domain: str) -> str:
    return "".join(part.capitalize() for part in domain.split("_"))


def _method_name(row: dict[str, Any]) -> str:
    stem = Path(row["source_path"]).stem
    slug = re.sub(r"[^a-z0-9]+", "_", stem.lower()).strip("_")
    return f"{slug}_{row['tr_id'].lower()}"


def _summary_line(row: dict[str, Any]) -> str:
    params = row["params"]
    required = ", ".join(p["name"] for p in params if p["required"]) or "없음"
    optional = ", ".join(p["name"] for p in params if not p["required"]) or "없음"
    label = row["label"] or "(제목 없음)"
    if row["method"] == "WS":
        fields = ", ".join(row["response"]["fields"]) or "없음"
        return (
            f"{label} -- WS tr_id={row['tr_id']}. "
            f"필수 파라미터: {required}. 응답 필드: {fields}."
        )
    containers = ", ".join(row["response"]["containers"]) or "없음"
    return (
        f"{label} -- {row['method']} {row['path']} (tr_id={row['tr_id']}). "
        f"필수: {required}. 선택: {optional}. 응답 컨테이너: {containers}."
    )


_DOC_WRAP_WIDTH = 88  # ruff E501(line-length=100)은 동아시아 폭 문자를 2로 센다 -- 들여쓰기
# 8칸을 빼고도 안전하도록 문자수가 아니라 표시폭(_display_width) 기준으로 감싼다.


def _display_width(text: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in text)


def _wrap_display(text: str, max_width: int) -> list[str]:
    """`textwrap.wrap`은 문자수만 세어 한글 등 폭 2 문자가 섞이면 실제 렌더 폭을
    과소평가한다 -- 표시폭 기준으로 직접 감싼다(순수 함수, 결정론)."""
    lines: list[str] = []
    current = ""
    current_width = 0
    for word in text.split(" "):
        word_width = _display_width(word)
        if word_width > max_width:
            if current:
                lines.append(current)
                current, current_width = "", 0
            chunk = ""
            chunk_width = 0
            for ch in word:
                ch_width = _display_width(ch)
                if chunk_width + ch_width > max_width and chunk:
                    lines.append(chunk)
                    chunk, chunk_width = "", 0
                chunk += ch
                chunk_width += ch_width
            current, current_width = chunk, chunk_width
            continue
        sep_width = 1 if current else 0
        if current and current_width + sep_width + word_width > max_width:
            lines.append(current)
            current, current_width = word, word_width
        else:
            current = f"{current} {word}" if current else word
            current_width += sep_width + word_width
    if current:
        lines.append(current)
    return lines or [text]


def _docstring_lines(row: dict[str, Any], indent: str) -> list[str]:
    summary = _summary_line(row).replace('"""', "'''")
    wrapped = _wrap_display(summary, _DOC_WRAP_WIDTH)
    lines = [f'{indent}"""BR-12 생성(ADR-2026-09-06-I D7). 미검증: 실계좌 왕복 미확인.']
    lines.extend(f"{indent}{w}" for w in wrapped)
    lines.append(f'{indent}"""')
    return lines


def render_rest_method(row: dict[str, Any]) -> list[str]:
    if row["method"] not in ("GET", "POST"):
        raise KisGenerateError(f"{row['tr_id']}: 지원하지 않는 HTTP 메서드 {row['method']!r}")
    name = _method_name(row)
    arg_name = "params" if row["method"] == "GET" else "body"
    lines = [""]
    if is_order_method(row):
        lines.append("    @require_paper_sandbox")
    lines.extend(
        [
            f"    async def {name}(",
            "        self, params: dict[str, Any] | None = None",
            "    ) -> dict[str, Any]:",
        ]
    )
    lines.extend(_docstring_lines(row, "        "))
    lines.extend(
        [
            "        return await self._request(",
            f'            "{row["method"]}",',
            f'            "{row["path"]}",',
            f'            "{row["tr_id"]}",',
            f"            {arg_name}=params or {{}},",
            "        )",
        ]
    )
    return lines


def render_ws_method(row: dict[str, Any]) -> list[str]:
    if row["method"] != "WS":
        raise KisGenerateError(f"{row['tr_id']}: WS 렌더러에 비-WS 행 전달됨")
    name = _method_name(row)
    lines = [
        "",
        f"    async def {name}(",
        "        self,",
        "        tr_key: str,",
        "        callback: MessageHandler,",
        "        *,",
        "        on_reconnecting: ReconnectHook | None = None,",
        "        on_reconnected: ReconnectHook | None = None,",
        "        connect_fn: ConnectFn = _connect,",
        "    ) -> None:",
    ]
    lines.extend(_docstring_lines(row, "        "))
    lines.extend(
        [
            "        approval_key = await self.get_ws_approval_key()",
            "        url = WS_PAPER_URL if self._is_paper_trading else WS_REAL_URL",
            "        subscribe_msg = _build_subscribe_message(",
            f'            approval_key, "{row["tr_id"]}", tr_key',
            "        )",
            "",
            "        async def _on_frame(raw: str) -> None:",
            "            await callback(raw)",
            "",
            "        await _run_kis_ws_subscription(",
            "            url,",
            "            subscribe_msg,",
            "            _on_frame,",
            "            connect_fn=connect_fn,",
            "            on_reconnecting=on_reconnecting,",
            "            on_reconnected=on_reconnected,",
            "        )",
        ]
    )
    return lines


def is_order_method(row: dict[str, Any]) -> bool:
    """BR-12 리프(review:1971 REJECT 후속) -- 주문성 TR 판정.

    KIS TR 기준 목록 안 POST 메서드 48건은 예외 없이 매수/매도/정정/취소/예약주문
    (레이블에 "주문" 포함)이다. 이 저장소 안에서는 method == "POST"가 곧
    주문성이라는 뜻이라 이름 규칙에 기대지 않는다(fail-closed 원칙 -- 분류가
    모호하면 가드를 붙이는 쪽이 안전하다, decision 참조). GET/WS는 조회·구독뿐이라
    대상에서 뺀다."""
    return row["method"] == "POST"


def render_method(row: dict[str, Any]) -> list[str]:
    return render_ws_method(row) if row["method"] == "WS" else render_rest_method(row)


def _chunk_rows(rows: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """tr_id 오름차순을 유지한 채 라인 예산으로 그리디 분할(결정론)."""
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_lines = 0
    for row in rows:
        rendered = render_method(row)
        if current and current_lines + len(rendered) > _CHUNK_BODY_BUDGET:
            chunks.append(current)
            current = []
            current_lines = 0
        current.append(row)
        current_lines += len(rendered)
    if current:
        chunks.append(current)
    return chunks


def render_chunk_file(domain: str, idx: int, rows: list[dict[str, Any]]) -> str:
    has_rest = any(r["method"] != "WS" for r in rows)
    has_ws = any(r["method"] == "WS" for r in rows)
    has_order = any(is_order_method(r) for r in rows)
    protocol_bases = sorted(
        name for name, flag in (("_KISRestHost", has_rest), ("_KISWsHost", has_ws)) if flag
    )
    class_name = f"KISGenerated{_pascal(domain)}{idx:02d}Mixin"

    lines: list[str] = [
        f'"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- {domain} 미착수 TR 청크 {idx:02d}.',
        "",
        "`scripts/kis_generate_adapters.py`가 `docs/design/kis_tr_reference.json`(BR-11)",
        "에서 그대로 생성했다 -- 손으로 수정하지 말 것(재생성 시 덮어쓴다). 요청 조립만",
        "예제에서 기계 추출한 값 그대로 하고, 응답은 파싱 없이 원본을 돌려준다 -- 필드",
        "단위 타입 매핑은 실계좌 확보 후 검수 리프(BR-13~15)의 몫이다.",
        '"""',
        "from __future__ import annotations",
        "",
    ]
    if has_rest:
        lines += ["from typing import Any", ""]
    first_party: list[str] = []
    if has_order:
        first_party.append("from src.exchanges.common.live_guard import require_paper_sandbox")
    first_party.append(
        f"from src.exchanges.kis.generated._protocols import {', '.join(protocol_bases)}"
    )
    if has_ws:
        first_party.append(
            "from src.exchanges.kis.websocket_connection import (\n"
            "    ConnectFn,\n"
            "    MessageHandler,\n"
            "    ReconnectHook,\n"
            "    _connect,\n"
            "    _run_kis_ws_subscription,\n"
            ")"
        )
        first_party.append(
            "from src.exchanges.kis.websocket_mixin import (\n"
            "    WS_PAPER_URL,\n"
            "    WS_REAL_URL,\n"
            ")"
        )
        first_party.append(
            "from src.exchanges.kis.websocket_parsing import _build_subscribe_message"
        )
    lines += first_party
    lines += ["", "", f"class {class_name}({', '.join(protocol_bases)}):"]
    for row in rows:
        lines.extend(render_method(row))
    lines.append("")
    text = "\n".join(lines)
    if text.count("\n") + 1 > _FILE_LINE_CAP:
        raise KisGenerateError(
            f"{domain} 청크 {idx:02d}: {text.count(chr(10)) + 1}줄 > {_FILE_LINE_CAP}줄 상한"
        )
    return text


_PROTOCOLS_MODULE = '''"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- 생성 mixin의 어댑터 인터페이스.

`scripts/kis_generate_adapters.py`가 생성한다 -- 손으로 수정하지 말 것. Protocol을
베이스 클래스로 명시 상속하면(구조적 타이핑) `self._request`/`self._is_paper_trading`
같은 크로스-mixin 접근에 `# type: ignore[attr-defined]`가 필요 없다 -- 실제 구현은
`KISAdapter`의 다른 mixin(`_KISHTTPClient`/`KISWebSocketMixin`)이 MRO 앞쪽에서
채운다(PLT-40 type-ignore 예산 래칫을 건드리지 않기 위한 설계 선택).
"""
from __future__ import annotations

from typing import Any, Protocol


class _KISRestHost(Protocol):
    async def _request(
        self,
        method: str,
        path: str,
        tr_id: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


class _KISWsHost(Protocol):
    _is_paper_trading: bool

    async def get_ws_approval_key(self) -> str: ...
'''


def render_init_module(chunk_names: list[tuple[str, str]]) -> str:
    """`chunk_names`: (module_stem, class_name) 목록(파일 생성 순서와 동일)."""
    import_lines = [
        f"from src.exchanges.kis.generated.{stem} import (\n    {cls},\n)"
        for stem, cls in chunk_names
    ]
    # 미착수 0건(모든 TR이 수기 구현됨)이면 청크가 없다 -- 그때도 유효한 클래스가
    # 나오도록 빈 베이스는 object 하나로 채운다(구문 오류 방지).
    bases = ",\n    ".join(cls for _, cls in chunk_names) or "object"
    return "\n".join(
        [
            '"""BR-12 자동 생성(ADR-2026-09-06-I D7) -- 도메인별 생성 mixin을 하나로 묶는다.',
            "",
            "`scripts/kis_generate_adapters.py`가 생성한다 -- 손으로 수정하지 말 것. adapter.py",
            "는 `KISGeneratedMixin` 하나만 상속하면 된다(신규 청크가 생겨도 이 파일 하나만",
            "재생성되고 adapter.py는 바뀌지 않는다).",
            '"""',
            "from __future__ import annotations",
            "",
            *import_lines,
            "",
            "",
            "class KISGeneratedMixin(",
            f"    {bases},",
            "):",
            '    """BR-12 생성 mixin 전체(도메인별 청크)를 결합한 파사드."""',
            "",
        ]
    )


def generate_files(reference: dict[str, Any], handwritten_source: str) -> dict[str, str]:
    """상대경로(`generated/` 기준) -> 파일 내용. 순수 함수 -- I/O 없음(결정론 보증)."""
    todo = select_todo_rows(reference, handwritten_source)
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for row in todo:
        by_domain.setdefault(row["domain"], []).append(row)

    files: dict[str, str] = {"_protocols.py": _PROTOCOLS_MODULE}
    chunk_names: list[tuple[str, str]] = []
    for domain in sorted(by_domain):
        chunks = _chunk_rows(by_domain[domain])
        for idx, rows in enumerate(chunks, start=1):
            stem = f"{domain}_{idx:02d}_mixin"
            class_name = f"KISGenerated{_pascal(domain)}{idx:02d}Mixin"
            files[f"{stem}.py"] = render_chunk_file(domain, idx, rows)
            chunk_names.append((stem, class_name))
    files["__init__.py"] = render_init_module(chunk_names)
    return files


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지
    del argv
    try:
        reference = _coverage.load_reference(REFERENCE_PATH)
    except _coverage.KisTrCoverageError as exc:
        print(f"FAIL: {exc}")
        return 1
    handwritten_source = scan_handwritten_source(ADAPTER_DIR)

    try:
        files = generate_files(reference, handwritten_source)
    except KisGenerateError as exc:
        print(f"FAIL: {exc}")
        return 1

    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    for stale in GENERATED_DIR.glob("*.py"):
        stale.unlink()
    for relpath, content in files.items():
        (GENERATED_DIR / relpath).write_text(content, encoding="utf-8")

    print(f"OK: {len(files) - 2}개 청크 파일 생성 (총 {len(files)}개 파일) -> {GENERATED_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
