"""OpenAPI 기준선(contracts/openapi/v1.json)에 새 경로·스키마만 '추가'로 합친다.

`export_openapi.py`를 기본 출력으로 돌리면 기준선 전체가 덮어써지고, 환경(pydantic 버전)에 따라
무관한 스키마 70여 개(Decimal 표현 등)까지 바뀐다. 호환성 계약(107)상 기준선은 의도한 변경만
들어가야 하므로, 새 export 결과에서 지정한 경로·스키마만 복사해 넣는다.

사용 (저장소 루트):
  1) 임시 위치로 export — 기준선을 덮어쓰지 않게 반드시 --out을 준다:
       python -m scripts.export_openapi --out %TEMP%/openapi_current.json
     (앱 조립에 환경 변수가 필요하다: JWT_SECRET_KEY, CREDENTIAL_ENCRYPTION_KEY(64자 hex),
      BITGET_API_KEY/SECRET, KIS_APP_KEY/SECRET, DATABASE_URL — 테스트용 더미 값을 쓴다)
  2) 합치기:
       python scripts/merge_openapi_baseline.py --current %TEMP%/openapi_current.json \\
           --path "/executions/{execution_id}/decision-trail" --schema DecisionTrailResponse
  3) `git diff --stat contracts/openapi/v1.json`이 순수 추가인지 보고
       python scripts/check_openapi_compat.py
     가 통과하는지 확인한다.

이미 기준선에 있는 경로·스키마는 거부한다(기존 계약을 조용히 바꾸지 않는다). 파일 형식은 export와
같다: indent=2, sort_keys=True, ensure_ascii=False, 끝 개행 1개, 원본 줄바꿈(CRLF/LF) 유지.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BASELINE = ROOT / "contracts" / "openapi" / "v1.json"


class MergeError(ValueError):
    """합칠 수 없는 요청(없는 항목, 이미 있는 항목)."""


def merge_additions(
    baseline: dict[str, Any],
    current: dict[str, Any],
    paths: list[str],
    schemas: list[str],
) -> dict[str, Any]:
    """baseline 사본에 current의 paths/schemas만 추가해 돌려준다. 입력은 바꾸지 않는다."""
    if not paths and not schemas:
        raise MergeError("합칠 경로나 스키마를 하나 이상 지정해야 한다")
    merged: dict[str, Any] = json.loads(json.dumps(baseline))
    base_paths = merged.setdefault("paths", {})
    base_schemas = merged.setdefault("components", {}).setdefault("schemas", {})
    cur_paths = current.get("paths", {})
    cur_schemas = current.get("components", {}).get("schemas", {})
    for path in paths:
        if path not in cur_paths:
            raise MergeError(f"새 export에 경로가 없다: {path}")
        if path in base_paths:
            raise MergeError(f"기준선에 이미 있는 경로라 덮어쓰지 않는다: {path}")
        base_paths[path] = cur_paths[path]
    for name in schemas:
        if name not in cur_schemas:
            raise MergeError(f"새 export에 스키마가 없다: {name}")
        if name in base_schemas:
            raise MergeError(f"기준선에 이미 있는 스키마라 덮어쓰지 않는다: {name}")
        base_schemas[name] = cur_schemas[name]
    return merged


def render(obj: dict[str, Any], *, crlf: bool) -> bytes:
    text = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if crlf:
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAPI 기준선에 새 경로·스키마만 추가")
    parser.add_argument("--current", type=Path, required=True, help="새로 export한 JSON")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--path", action="append", default=[], dest="paths")
    parser.add_argument("--schema", action="append", default=[], dest="schemas")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    raw = args.baseline.read_bytes()
    baseline = json.loads(raw.decode("utf-8"))
    current = json.loads(args.current.read_text(encoding="utf-8"))
    try:
        merged = merge_additions(baseline, current, args.paths, args.schemas)
    except MergeError as exc:
        print(f"FAIL: {exc}")
        return 1
    mixed = raw.count(b"\n") - raw.count(b"\r\n")
    args.baseline.write_bytes(render(merged, crlf=b"\r\n" in raw))
    if raw.count(b"\r\n") and mixed:
        # 줄바꿈이 섞인 기준선은 한 형식으로 정규화된다 -- git diff에 의미 없는 줄 변경이 보인다.
        print(f"NOTE: 기준선에 LF 단독 줄 {mixed}개가 섞여 있어 CRLF로 정규화했다(내용 변경 아님)")
    print(f"OK: 경로 {len(args.paths)}개·스키마 {len(args.schemas)}개 추가 — {args.baseline}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
