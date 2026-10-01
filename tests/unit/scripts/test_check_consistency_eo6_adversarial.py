"""EO-6 D3 adversarial 증빙 -- scripts/consistency/spec_trace.py, wiring.py.

tests/unit/scripts/test_check_consistency.py가 1000줄 하드 캡에 닿아
(ADR-2026-09-10-C §7 file policy) EO-6 깊이 증빙을 위한 추가 negative test를
같은 파일에 넣지 않고 책임 단위(spec 추적성 + router 배선의 adversarial 변형)로
분리한 sibling 파일이다(task-10502, CLAUDE.md §6 #12).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS_DIR = ROOT / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cc = _load_module("check_consistency_eo6_adversarial", SCRIPTS_DIR / "check_consistency.py")


def _write(tmp_path: Path, relative: str, content: str) -> Path:
    path = tmp_path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_spec_leaf_embedded_in_larger_identifier_does_not_count_as_traced(
    tmp_path: Path,
) -> None:
    """EO-6 D3: 더 큰 식별자 안에 묻힌 leaf_id는 추적된 것으로 치지 않는다.

    `_LEAF_TOKEN_SCAN_RE`는 토큰 경계(앞뒤로 영숫자가 없을 때)를 요구한다.
    leaf_id 글자가 더 큰 식별자(`XEO-6Y`)에 포함돼 있을 뿐이면 그 전체가
    별개의 토큰으로 추출되어 leaf_ids 집합과 일치하지 않으므로, spec에 있는
    leaf는 여전히 미추적(flag)으로 남아야 한다 -- 부분 문자열 오판(task-3680
    계열, Z-1/Z-10 사례)과 같은 축의 adversarial 변형.
    """
    _write(
        tmp_path,
        "docs/specs/L4_x_v1.0.md",
        "## 9. 리프 목록\n| 리프 ID | 파일 |\n|---|---|\n| EO-6 | src/eo.py |\n",
    )
    _write(tmp_path, "src/eo.py", "# XEO-6Y라는 더 큰 식별자만 있다\nx = 1\n")
    hits = cc.check_spec_leaf_traceability(tmp_path)
    assert hits == [("docs/specs#EO-6", 0)]


def test_leaf_unreachable_adversarial(tmp_path: Path) -> None:
    """EO-6 D3: check_spec_leaf_traceability — spec의 모든 리프가 코드에 없을 때 적색.

    EO-6 게이트가 spec에 명시된 리프 중 코드에서 leaf 토큰이 하나도 등장하지
    않으면 전부 미추적으로 차단하는지를 adversarial하게 검증한다(리프 1개가
    아니라 여러 개가 동시에 미추적인 경우).
    """
    _write(
        tmp_path,
        "docs/specs/L4_execution_ownership_v1.0.md",
        "## 9. 리프 목록\n| 리프 ID | 파일 |\n|---|---|\n"
        "| EO-1 | src/eo.py |\n| EO-99 | src/unreachable.py |\n",
    )
    # src/eo.py, src/unreachable.py 둘 다 생성하지 않음 -- EO-1, EO-99 모두 미추적
    hits = cc.check_spec_leaf_traceability(tmp_path)
    assert len(hits) == 2
    leaf_ids = {h[0].split("#")[-1] for h in hits}
    assert leaf_ids == {"EO-1", "EO-99"}


def test_router_wiring_adversarial(tmp_path: Path) -> None:
    """EO-6 D3: check_router_wiring — router_registry에 등록 안 한 라우터.

    EO-6 게이트가 FastAPI 라우터 모듈 중 router_registry.py에 등록되지 않은
    것을 적색으로 차단하는지를, 등록 파일에 무관한 다른 라우터 등록이 섞여
    있는 상황에서도 adversarial하게 검증한다.
    """
    _write(
        tmp_path,
        "src/api/routers/my_feature.py",
        "from fastapi import APIRouter\n"
        'router = APIRouter(prefix="/my-feature", tags=["my-feature"])\n',
    )
    _write(
        tmp_path,
        "src/api/router_registry.py",
        "def register_routers(app):\n"
        "    from src.api.routers import other\n"
        "    app.include_router(other.router)\n",
    )
    hits = cc.check_router_wiring(tmp_path)
    assert hits == [("src/api/routers/my_feature.py", 1)]
