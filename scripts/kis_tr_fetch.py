"""KIS TR 기준 목록 기계 추출 — 공식 저장소 `koreainvestment/open-trading-api`에서 내려받는다.

BR-1(ADR-2026-09-06-I D2). 수기 목록은 기준이 될 수 없다는 원칙에 따라, TR ID·설명은
전부 공식 예제 코드(`examples_llm/**/*.py`, `chk_*.py` 제외)의 `tr_id = "..."` 대입문과
파일 상단 `# [분류] ... > 이름 [문서ID]` 주석에서 정규식으로만 추출한다.

BR-11(ADR-2026-09-06-I D7). 어댑터 메서드를 생성하려면 tr_id만으로는 부족해 path·HTTP
메서드·파라미터(필수/선택)·응답 필드까지 예제 원문에서 함께 뽑는다(순수 파싱 로직은
`kis_tr_parse.py`). 뽑지 못하면 조용히 비워두지 않고 각 TR 행의 `extraction_error`에
사유를 남긴다 — path·method는 375개 전부 채워지는 것이 DoD이므로, 실패 시 `main()`이
그 목록을 출력하고 종료코드 1을 반환한다(fail-closed).

`kis_tr_coverage.py`는 이 모듈이 만드는 `docs/design/kis_tr_reference.json`(커밋되는 스냅샷)만
읽는다 — CI는 네트워크 없이 오프라인으로 돈다. 스냅샷을 새로 뜨려면:

    python scripts/kis_tr_fetch.py

이 스크립트만 네트워크 접근을 한다(`urllib`, GitHub REST + raw.githubusercontent.com).
path·method·params·응답필드까지 스냅샷에 박혀 있으므로, 이후 어댑터 생성(BR-12)은
이 JSON만 읽으면 되고 다시 네트워크를 탈 필요가 없다(오프라인 재생성 가능).
"""
from __future__ import annotations

import json
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import kis_tr_parse  # noqa: E402 (sys.path 준비 후에만 임포트 가능 — 이 스크립트가 단독 실행/importlib 양쪽으로 로드됨)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "docs" / "design" / "kis_tr_reference.json"

REPO = "koreainvestment/open-trading-api"
REF = "main"
TREE_API = f"https://api.github.com/repos/{REPO}/git/trees/{REF}?recursive=1"
RAW_BASE = f"https://raw.githubusercontent.com/{REPO}/{REF}/"
INCLUDE_PREFIX = "examples_llm/"
USER_AGENT = "aios-kis-tr-coverage/1"
FETCH_TIMEOUT = 20
MAX_WORKERS = 16


class KisTrFetchError(RuntimeError):
    """GitHub API/raw 응답이 예상 형식이 아님."""


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310 (공식 저장소 고정 URL)
        return bytes(resp.read())


def list_example_files(ref: str = REF) -> tuple[str, list[str]]:
    """(커밋 SHA, 대상 파일 경로 목록)을 반환한다.

    `chk_*.py`(검증용 러너)는 TR을 정의하지 않으므로 제외한다.
    """
    data = json.loads(_http_get(TREE_API))
    if data.get("truncated"):
        raise KisTrFetchError("git tree 응답이 truncated — recursive 조회가 저장소 크기를 못 담음")
    commit_sha = _resolve_commit_sha(ref)
    paths = []
    for entry in data.get("tree", []):
        path = entry.get("path", "")
        if entry.get("type") != "blob" or not path.startswith(INCLUDE_PREFIX):
            continue
        if not path.endswith(".py"):
            continue
        name = path.rsplit("/", 1)[-1]
        if name.startswith("chk_") or name == "kis_auth.py":
            continue
        if path.count("/") < 2:
            continue  # examples_llm/<domain>/<endpoint>/<file>.py 형태만
        paths.append(path)
    return commit_sha, sorted(paths)


def _resolve_commit_sha(ref: str) -> str:
    data = json.loads(_http_get(f"https://api.github.com/repos/{REPO}/commits/{ref}"))
    sha = data.get("sha")
    if not sha:
        raise KisTrFetchError("커밋 SHA를 확인할 수 없음")
    return str(sha)


def extract_trs_from_file(path: str) -> list[dict[str, Any]]:
    """한 예제 파일에서 TR 행(들)을 추출한다. 없으면 빈 목록(순수 파싱은 `kis_tr_parse`)."""
    text = _http_get(RAW_BASE + path).decode("utf-8", errors="replace")
    return kis_tr_parse.extract_trs_from_text(text, path)


def build_reference(ref: str = REF) -> dict[str, Any]:
    commit_sha, paths = list_example_files(ref)
    seen: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for rows in pool.map(extract_trs_from_file, paths):
            for row in rows:
                # 먼저 나온 파일(정렬된 paths 순서)이 정본 — 동일 TR이 여러 파일에 있어도 결정적
                seen.setdefault(row["tr_id"], row)
    trs = sorted(seen.values(), key=lambda r: r["tr_id"])
    return {
        "source_repo": REPO,
        "source_ref": ref,
        "source_commit": commit_sha,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "file_count": len(paths),
        "trs": trs,
    }


def find_extraction_failures(
    trs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(path/method가 빠진 필수 실패, 그 외 추출 경고)로 나눈다.

    BR-11 DoD: 375개 전부에 path·method가 채워져야 한다 — 못 채운 TR은 여기서 걸러
    `main()`이 사유와 함께 출력하고 실패 종료하게 만든다(ADR D2.4, 조용히 누락 금지).
    """
    hard: list[dict[str, Any]] = []
    soft: list[dict[str, Any]] = []
    for row in trs:
        is_ws = row["method"] == "WS"
        missing_path = row["path"] is None and not is_ws
        missing_method = row["method"] is None
        if missing_path or missing_method:
            hard.append(row)
        elif row["extraction_error"]:
            soft.append(row)
    return hard, soft


def write_reference(output: Path = DEFAULT_OUTPUT, ref: str = REF) -> int:
    reference = build_reference(ref)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(reference, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    hard_failures, soft_failures = find_extraction_failures(reference["trs"])
    for row in hard_failures:
        print(
            f"FAIL[path/method]: {row['tr_id']} ({row['source_path']}): {row['extraction_error']}"
        )
    for row in soft_failures:
        print(
            f"WARN[params/response]: {row['tr_id']} ({row['source_path']}): "
            f"{row['extraction_error']}"
        )
    print(
        f"{'FAIL' if hard_failures else 'OK'}: TR {len(reference['trs'])}개, "
        f"파일 {reference['file_count']}개 -> {output} "
        f"(path/method 실패 {len(hard_failures)}건, 기타 추출 경고 {len(soft_failures)}건)"
    )
    return 1 if hard_failures else 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        return write_reference()
    except (KisTrFetchError, OSError) as exc:
        print(f"FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
