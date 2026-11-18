"""compose.uat.yml 정합성 -- 빌드 경로와 이미지 태그를 정적으로 확인한다.

회귀(2026-11-18 첫 기동): web 서비스 context가 ops/uat라 루트 기준 COPY를 쓰는
Dockerfile.web이 파일을 못 찾았고, postgres:16 로컬 태그가 PG18로 별칭돼 있었다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "compose.uat.yml"


def _services() -> dict[str, Any]:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    services: dict[str, Any] = data["services"]
    return services


def _built() -> list[tuple[str, dict[str, Any]]]:
    return [(n, s["build"]) for n, s in _services().items() if isinstance(s.get("build"), dict)]


@pytest.mark.parametrize("name,build", _built())
def test_build_context_and_dockerfile_exist(name: str, build: dict[str, Any]) -> None:
    context = ROOT / build["context"]
    assert context.is_dir(), f"{name}: context 없음 {context}"
    dockerfile = context / build.get("dockerfile", "Dockerfile")
    assert dockerfile.is_file(), f"{name}: Dockerfile 없음 {dockerfile}"


def test_web_dockerfile_copies_resolve_against_its_context() -> None:
    build = dict(_built())["web"]
    context = ROOT / build["context"]
    text = (context / build["dockerfile"]).read_text(encoding="utf-8")
    for match in re.finditer(r"^COPY\s+(?!--from)(\S+)\s+\S+", text, re.MULTILINE):
        source = match.group(1).rstrip("/")
        assert (context / source).exists(), f"COPY 원본이 컨텍스트에 없다: {source}"


def test_postgres_image_is_explicit_non_aliased_tag() -> None:
    image = str(_services()["postgres"]["image"])
    assert re.fullmatch(r"postgres:\d+-[a-z]+", image), f"명시 태그가 아니다: {image}"


def test_all_ports_bind_loopback_only() -> None:
    for name, service in _services().items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name}: 루프백 외 바인딩 {port}"
