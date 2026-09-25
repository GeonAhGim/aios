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

언어 정책(ADR-2026-09-07-A, task-1990): `src/` 아래 주석·독스트링은 영문이어야
하므로 메서드 독스트링 템플릿에서 한글 TR 레이블(`row["label"]`)을 뺐다. 정보
손실을 막기 위해 레이블은 도메인별 `{domain}_tr_labels.py`에 일반 dict 리터럴
(독스트링도 주석도 아닌 값)로 옮겨 그대로 보존한다 — 이 값은 검사 대상이 아니다.

사용: `python scripts/kis_generate_adapters.py` (저장소 루트에서). 종료코드
0=생성 완료, 1=입력 오류(기준 목록 없음/형식 오류/미지원 메서드).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = ROOT / "docs" / "design" / "kis_tr_reference.json"
ADAPTER_DIR = ROOT / "src" / "exchanges" / "kis"
GENERATED_DIR = ADAPTER_DIR / "generated"

# 예외 오버라이드 표 — ADR 근거와 함께 tr_id별 필드(path/method/params/response)를
# 보정할 때만 채운다(`kis_tr_coverage._SCOPE_OVERRIDES`와 동일 원칙). 현재 BR-11
# 기계 추출 결과에 추출 오류가 0건이라 비어 있다.
_EXCEPTION_OVERRIDES: dict[str, dict[str, Any]] = {}


def _load_module(name: str, path: Path) -> ModuleType:
    """`sys.modules`에 이미 있으면 재사용한다 -- 분할된 스크립트들이 서로 같은
    이름으로 서로를 로드할 때(예: 이 모듈과 `kis_chunking.py`가 둘 다
    `kis_rendering`을 로드) 다시 실행하면 같은 파일이라도 클래스 정체성이
    갈라져(`KisGenerateError`가 두 개의 서로 다른 클래스가 됨) `isinstance`/
    `pytest.raises`가 깨진다."""
    existing = sys.modules.get(name)
    if existing is not None:
        return existing
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"모듈 로드 실패: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_HERE = Path(__file__).resolve().parent
_coverage = _load_module("kis_tr_coverage", _HERE / "kis_tr_coverage.py")
_rendering = _load_module("kis_rendering", _HERE / "kis_rendering.py")
_chunking = _load_module("kis_chunking", _HERE / "kis_chunking.py")
_labels = _load_module("kis_labels", _HERE / "kis_labels.py")

# 하위 호환 재노출 -- 테스트가 이 모듈에서 직접 참조한다(예: `gen.is_order_method`,
# `gen.render_rest_method`). 실제 정의는 각 분할 모듈에 있다.
KisGenerateError = _rendering.KisGenerateError
render_rest_method = _rendering.render_rest_method
render_ws_method = _rendering.render_ws_method
render_method = _rendering.render_method
is_order_method = _rendering.is_order_method
_pascal = _rendering._pascal
_FILE_LINE_CAP = _chunking._FILE_LINE_CAP
_chunk_rows = _chunking._chunk_rows
render_init_module = _chunking.render_init_module
_PROTOCOLS_MODULE = _chunking._PROTOCOLS_MODULE


def render_chunk_file(domain: str, idx: int, rows: list[dict[str, Any]]) -> str:
    """`_chunking.render_chunk_file`에 이 모듈의 `_FILE_LINE_CAP`을 그대로
    전달한다 -- 테스트가 `_FILE_LINE_CAP`을 이 모듈에서 monkeypatch하므로 값을
    복사해두면 안 되고 호출 시점에 다시 읽어야 한다."""
    return str(_chunking.render_chunk_file(domain, idx, rows, file_line_cap=_FILE_LINE_CAP))


def render_tr_labels_file(domain: str, rows: list[dict[str, Any]]) -> str:
    """`render_chunk_file`과 같은 이유로 `_FILE_LINE_CAP`을 호출 시점에 전달한다."""
    return str(_labels.render_tr_labels_file(domain, rows, file_line_cap=_FILE_LINE_CAP))


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


def generate_files(reference: dict[str, Any], handwritten_source: str) -> dict[str, str]:
    """상대경로(`generated/` 기준) -> 파일 내용. 순수 함수 -- I/O 없음(결정론 보증)."""
    todo = select_todo_rows(reference, handwritten_source)
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for row in todo:
        by_domain.setdefault(row["domain"], []).append(row)

    files: dict[str, str] = {"_protocols.py": _PROTOCOLS_MODULE}
    chunk_names: list[tuple[str, str]] = []
    for domain in sorted(by_domain):
        files[f"{domain}_tr_labels.py"] = render_tr_labels_file(domain, by_domain[domain])
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

    mixin_count = sum(1 for name in files if name.endswith("_mixin.py"))
    label_count = sum(1 for name in files if name.endswith("_tr_labels.py"))
    print(
        f"OK: mixin 청크 {mixin_count}개 + tr_labels {label_count}개 "
        f"(총 {len(files)}개 파일) -> {GENERATED_DIR}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
