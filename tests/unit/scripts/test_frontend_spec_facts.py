"""scripts/frontend_spec extract_facts + gen_facts: extraction, rendering, freshness gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts.frontend_spec import extract_facts as ef
from scripts.frontend_spec import gen_facts, verify_spec

PAGE = """import { useExecutions } from "@aios/shared-hooks";
import { Button, Field, Input } from "@aios/ui-web";

// t("comment.should.not.count")
export function DemoPage() {
  const { t } = useTranslation();
  const executions = useExecutions();
  return (
    <section role="alert" data-testid="demo-root">
      <Field label={t("demo.name")}>
        <Input value="x" />
      </Field>
      <Button onClick={handleSave}>{t("demo.save")}</Button>
      <FeatureFlagGate flag="FF_DEMO">{classifyForbidden(error)}</FeatureFlagGate>
    </section>
  );
}
"""


@pytest.fixture
def page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "frontend" / "apps" / "web" / "src" / "routes" / "DemoPage.tsx"
    path.parent.mkdir(parents=True)
    path.write_text(PAGE, encoding="utf-8")
    monkeypatch.setattr(ef, "ROOT", tmp_path)
    monkeypatch.setattr(verify_spec, "ROOT", tmp_path)
    monkeypatch.setattr(verify_spec, "_index", None)
    monkeypatch.setattr(verify_spec, "_file_cache", {})
    return path


def test_flatten_builds_dotted_keys() -> None:
    assert ef.flatten({"a": {"b": "x", "c": {"d": "y"}}, "e": "z"}) == {
        "a.b": "x",
        "a.c.d": "y",
        "e": "z",
    }


def test_extract_finds_real_facts_with_line_numbers(page: Path) -> None:
    facts = ef.extract(page)
    texts = {(f.name, f.line) for f in facts.of("text")}
    assert ("demo.name", 10) in texts and ("demo.save", 13) in texts
    assert [f.name for f in facts.of("hook")] == ["useTranslation", "useExecutions"]
    assert [f.name for f in facts.of("testid")] == ["demo-root"]
    assert [f.name for f in facts.of("flag")] == ["FF_DEMO"]
    assert any(f.name == "classifyForbidden" for f in facts.of("error"))
    button = facts.of("button")[0]
    assert (button.name, button.extra) == ("Button", "handleSave")


def test_comment_lines_do_not_count_as_facts(page: Path) -> None:
    names = {f.name for f in ef.extract(page).of("text")}
    assert "comment.should.not.count" not in names


def test_render_resolves_catalog_text_and_marks_missing_keys(page: Path) -> None:
    catalogs = {"ko": {"demo.name": "이름"}, "en": {"demo.name": "Name"}}
    text = ef.render(ef.extract(page), catalogs, {"useExecutions": ("pkg/hooks.ts", 5)})
    assert "| `demo.name` | 이름 | Name |" in text
    assert "(missing in catalog)" in text  # demo.save is not in the catalog
    assert "`pkg/hooks.ts:5`" in text


def test_rendered_sheet_always_passes_the_verifier(page: Path, tmp_path: Path) -> None:
    """Property: a sheet derived from code cites only what the code contains."""
    text = ef.render(ef.extract(page), {"ko": {}, "en": {}}, {})
    sheet = tmp_path / "docs" / "frontend" / "facts" / "DemoPage.facts.md"
    sheet.parent.mkdir(parents=True)
    sheet.write_text(text, encoding="utf-8")
    report = verify_spec.verify_file(sheet)
    assert report.findings == [] and report.citations > 5


def test_catalogs_available_requires_both_locales() -> None:
    assert not ef.catalogs_available({"ko": {"a": "b"}, "en": {}})
    assert ef.catalogs_available({"ko": {"a": "b"}, "en": {"a": "c"}})


def test_manifest_pages_exist_and_are_unique() -> None:
    items = gen_facts.screens()
    pages = [item["page"] for item in items]
    assert len(pages) == len(set(pages)) >= 15
    missing = [p for p in pages if not (ef.ROOT / p).is_file()]
    assert missing == []


def test_committed_fact_sheets_are_fresh() -> None:
    """Sheets under docs/frontend/facts must equal a fresh extraction (like gen_adr_index)."""
    if not ef.catalogs_available(ef.load_catalogs()):
        # Old Node (no type stripping): the generator must degrade visibly, never silently succeed.
        rendered = gen_facts.render_all()
        assert rendered and all("(missing in catalog)" in text for text in rendered.values())
        return
    assert gen_facts.main(["--check"]) == 0
