"""KIS TR 기준 목록 기계 추출 — 공식 저장소 `koreainvestment/open-trading-api`에서 내려받는다.

BR-1(ADR-2026-09-06-I D2). 수기 목록은 기준이 될 수 없다는 원칙에 따라, TR ID·설명은
전부 공식 예제 코드(`examples_llm/**/*.py`, `chk_*.py` 제외)의 `tr_id = "..."` 대입문과
파일 상단 `# [분류] ... > 이름 [문서ID]` 주석에서 정규식으로만 추출한다.

`kis_tr_coverage.py`는 이 모듈이 만드는 `docs/design/kis_tr_reference.json`(커밋되는 스냅샷)만
읽는다 — CI는 네트워크 없이 오프라인으로 돈다. 스냅샷을 새로 뜨려면:

    python scripts/kis_tr_fetch.py

이 스크립트만 네트워크 접근을 한다(`urllib`, GitHub REST + raw.githubusercontent.com).
"""
from __future__ import annotations

import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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

_TR_ID_RE = re.compile(r"""tr_id\s*=\s*["']([A-Z][A-Z0-9]{5,13})["']""")
_HEADER_RE = re.compile(r"^#\s*(\[.+?\].+)$", re.MULTILINE)


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


def extract_trs_from_file(path: str) -> list[dict[str, str]]:
    """한 예제 파일에서 (tr_id, label) 쌍을 추출한다. 없으면 빈 목록."""
    text = _http_get(RAW_BASE + path).decode("utf-8", errors="replace")
    tr_ids = dict.fromkeys(_TR_ID_RE.findall(text))  # 파일 내 등장 순서 보존, 중복 제거
    if not tr_ids:
        return []
    header_match = _HEADER_RE.search(text)
    label = header_match.group(1).strip() if header_match else ""
    domain = path.split("/")[1]
    return [
        {"tr_id": tr_id, "domain": domain, "label": label, "source_path": path}
        for tr_id in tr_ids
    ]


def build_reference(ref: str = REF) -> dict[str, Any]:
    commit_sha, paths = list_example_files(ref)
    seen: dict[str, dict[str, str]] = {}
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


def write_reference(output: Path = DEFAULT_OUTPUT, ref: str = REF) -> int:
    reference = build_reference(ref)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(reference, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"OK: TR {len(reference['trs'])}개, 파일 {reference['file_count']}개 -> {output}")
    return 0


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
