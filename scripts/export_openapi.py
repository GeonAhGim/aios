"""OpenAPI 스냅샷 생성 — PLT-16.

`src.main.app`을 실제로 임포트해 `app.openapi()`가 만든 스키마를 그대로 JSON
파일로 저장한다(수기 작성 금지 — decision). 앱을 임포트하는 스크립트는 이
파일뿐이다: `check_openapi_compat.py`는 이 스크립트를 서브프로세스로 호출해
"현재" 스냅샷을 얻고, 자기 자신은 순수 JSON 비교만 한다.

사용: `python scripts/export_openapi.py [--out PATH]`. 기본 출력은
`contracts/openapi/v1.json`(커밋 대상). DB·네트워크 접속 없이 앱 조립(라우트
등록)만으로 스키마가 나온다 — `app.openapi()`는 lifespan을 거치지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "contracts" / "openapi" / "v1.json"


def export_schema() -> dict[str, Any]:
    from src.main import app  # 앱 임포트를 이 함수 호출 시점으로 지연

    schema: dict[str, Any] = app.openapi()
    return schema


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="OpenAPI 스냅샷 생성(PLT-16)")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔(cp949)에서 한글 깨짐 방지

    schema = export_schema()
    out_path: Path = args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"OK: OpenAPI 스냅샷 작성 — {out_path} ({len(schema.get('paths', {}))}개 경로)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
