"""Frontend spec issuer: skeletons and leaf planning (structure, sizing, completion checks)."""

from __future__ import annotations

import pytest

from scripts.frontend_spec import extract_facts as ef
from scripts.frontend_spec import gen_facts, plan_leaves, skeleton, verify_spec

SCREEN = "J3-ExecutionControlPage"


def _item() -> dict[str, str]:
    return next(i for i in gen_facts.screens() if skeleton.screen_id(i) == SCREEN)


def test_every_manifest_screen_has_a_skeleton_that_passes_the_verifier() -> None:
    for item in gen_facts.screens():
        path = skeleton.SCREENS_DIR / f"{skeleton.screen_id(item)}.md"
        assert path.exists(), f"missing skeleton for {item['page']}"
        report = verify_spec.verify_file(path, require_sections=True)
        assert report.findings == [], [f.detail for f in report.findings]


def test_skeleton_blocks_cover_every_template_block_key() -> None:
    text = (skeleton.SCREENS_DIR / f"{SCREEN}.md").read_text(encoding="utf-8")
    for block in skeleton.BLOCKS:
        assert skeleton.todo_count(text, SCREEN, block.key) is not None, block.key


def test_todo_count_is_per_block_and_none_for_missing() -> None:
    text = (
        "<!-- BLOCK:S:a -->\n| x | TODO | TODO |\n<!-- /BLOCK -->\n"
        "<!-- BLOCK:S:b -->\n| y | done |\n<!-- /BLOCK -->\n"
    )
    assert skeleton.todo_count(text, "S", "a") == 2
    assert skeleton.todo_count(text, "S", "b") == 0
    assert skeleton.todo_count(text, "S", "zzz") is None
    assert skeleton.todo_count(text, "OTHER", "a") is None


def test_fit_never_cuts_the_fixed_part_and_respects_the_limit() -> None:
    fixed = "RULES " * 50
    facts = "\n".join(f"- fact number {i} " + "x" * 40 for i in range(100))
    out = plan_leaves.fit(fixed, facts, limit=600)
    assert out.startswith(fixed) and len(out) <= 600
    assert out.count("- fact number") >= 1
    assert plan_leaves.fit(fixed, "", limit=600) == fixed


def test_plan_for_a_block_is_small_complete_and_machine_checkable() -> None:
    catalogs = ef.load_catalogs()
    if not ef.catalogs_available(catalogs):
        pytest.skip("Node >= 22.6 is required to read the TS i18n catalogs")
    plan = plan_leaves.build_plan(_item(), "st-b", catalogs)
    assert len(plan.spec) <= plan_leaves.MAX_SPEC_CHARS
    assert f"<!-- BLOCK:{SCREEN}:st-b -->" in plan.spec
    assert "verify_spec" in plan.spec and "TODO" in plan.spec
    assert plan.tier == "local" and plan.doc.endswith(f"{SCREEN}.md")
    assert plan_leaves.build_plan(_item(), "gap", catalogs).tier == "claude"


def test_instruction_file_defines_every_local_block() -> None:
    _, blocks = plan_leaves.load_instructions()
    assert set(plan_leaves.LOCAL_BLOCKS) <= set(blocks)


def test_open_blocks_lists_only_blocks_with_todo() -> None:
    opened = plan_leaves.open_blocks(SCREEN)
    assert set(opened) <= {b.key for b in skeleton.BLOCKS}
    assert plan_leaves.open_blocks("no-such-screen") == []
