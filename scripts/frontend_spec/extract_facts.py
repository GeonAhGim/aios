"""Extract verifiable facts about a frontend page from its source (no model involved).

The spec leaves that local workers used to write failed because the worker had to *read and
interpret* code. This extractor does the reading: it lists, with line numbers, what a page really
contains -- visible texts (i18n keys resolved to ko/en), form fields, buttons and handlers, data
hooks
and where they are defined, error-handling calls, test ids, feature flags and roles. The rendered
tables cite `path:line` and quote identifiers that occur in that file, so `verify_spec` passes by
construction. Judgement (state matrices, gaps) is written separately on top of these facts.

Usage (repo root, Node >= 23 for catalog resolution):
    python -m scripts.frontend_spec.extract_facts <page.tsx> [--out file.md]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
WEB_SRC = FRONTEND / "apps" / "web" / "src"
HOOK_DIRS = (FRONTEND / "packages" / "shared-hooks" / "src", WEB_SRC / "hooks")

T_CALL = re.compile(r"""\bt\(\s*(["'`])([^"'`$]+)\1""")
TESTID = re.compile(r"""data-testid=(?:"([^"]+)"|\{["'`]([^"'`]+)["'`]\})""")
HOOK_USE = re.compile(r"\b(use[A-Z][A-Za-z0-9]+)\s*\(")
FLAG = re.compile(
    r"""(?:flag=|flagEnabled\(|isFeatureEnabled\(|useFeatureFlag\()\s*\{?["'`]([A-Za-z0-9_]+)["'`]"""
)
ROLE = re.compile(r"""\b(role|aria-live|aria-label|aria-labelledby|aria-describedby)=["{]""")
ERROR_CALL = re.compile(
    r"\b(classify[A-Z][A-Za-z]+|route[A-Z][A-Za-z]*Error|is[A-Z][A-Za-z]*(?:NotFound|Forbidden|Conflict)"
    r"|[A-Z][A-Za-z]*Notice|ErrorMessage|NotFoundState|Alert)\b"
)
FIELD_TAG = re.compile(r"<(Field|Input|Select|Textarea|Checkbox|Radio)\b")
BUTTON_TAG = re.compile(r"<(Button|button)\b")
HANDLER = re.compile(r"on(Click|Submit|Change)=\{([A-Za-z_][A-Za-z0-9_.]*)")
LABEL_PROP = re.compile(r"""label=(?:\{t\(["'`]([^"'`]+)["'`]\)\}|"([^"]+)")""")


@dataclass
class Fact:
    line: int
    kind: str
    name: str
    extra: str = ""


@dataclass
class PageFacts:
    path: str
    line_count: int
    facts: list[Fact] = field(default_factory=list)

    def of(self, kind: str) -> list[Fact]:
        return [f for f in self.facts if f.kind == kind]


def flatten(node: object, prefix: str = "") -> dict[str, str]:
    """Flatten a nested catalog to dotted keys (i18next keySeparator '.')."""
    out: dict[str, str] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            out.update(flatten(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(node, str):
        out[prefix] = node
    return out


def catalogs_available(catalogs: dict[str, dict[str, str]]) -> bool:
    """False when Node could not read the TS catalogs (e.g. Node older than 22.6)."""
    return bool(catalogs.get("ko")) and bool(catalogs.get("en"))


def load_catalogs() -> dict[str, dict[str, str]]:
    """Read the TS i18n catalogs with Node's native type stripping and a small resolve hook.

    Returns empty dicts when Node is missing or too old to strip types (Node >= 22.6 is needed)."""
    node = shutil.which("node")
    helper = Path(__file__).resolve().parent
    result: dict[str, dict[str, str]] = {"ko": {}, "en": {}}
    if node is None:
        return result
    for locale, export in (("ko", "catalogKo"), ("en", "catalogEn")):
        src = WEB_SRC / "i18n" / f"catalog.{locale}.ts"
        command = [
            node,
            "--experimental-strip-types",
            "--no-warnings",
            "--import",
            (helper / "register_hook.mjs").as_uri(),
            str(helper / "dump_catalog.mjs"),
            str(src),
            export,
        ]
        dump = subprocess.run(  # noqa: S603
            command, capture_output=True, text=True, encoding="utf-8", check=False, cwd=helper
        )
        try:
            result[locale] = flatten(json.loads(dump.stdout))
        except ValueError:
            result[locale] = {}
    return result


def hook_definitions() -> dict[str, tuple[str, int]]:
    """Where each `useXxx` hook is defined: name -> (repo-relative path, line)."""
    defs: dict[str, tuple[str, int]] = {}
    pattern = re.compile(r"export\s+(?:async\s+)?(?:function|const)\s+(use[A-Z][A-Za-z0-9]+)")
    for base in HOOK_DIRS:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.ts*")):
            if ".test." in path.name:
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
                match = pattern.search(line)
                if match and match.group(1) not in defs:
                    defs[match.group(1)] = (path.relative_to(ROOT).as_posix(), number)
    return defs


# (kind, pattern, which group names the fact, ignore on import lines)
SIMPLE_RULES: tuple[tuple[str, re.Pattern[str], int, bool], ...] = (
    ("text", T_CALL, 2, False),
    ("hook", HOOK_USE, 1, True),
    ("flag", FLAG, 1, False),
    ("error", ERROR_CALL, 1, True),
    ("a11y", ROLE, 1, False),
)


def _testids(number: int, line: str) -> list[Fact]:
    return [Fact(number, "testid", m.group(1) or m.group(2)) for m in TESTID.finditer(line)]


def _controls(number: int, line: str) -> list[Fact]:
    """Form fields (with their label key) and buttons (with their handler) on one line."""
    found: list[Fact] = []
    tag = FIELD_TAG.search(line)
    if tag:
        label = LABEL_PROP.search(line)
        found.append(
            Fact(number, "field", tag.group(1), (label.group(1) or label.group(2)) if label else "")
        )
    button = BUTTON_TAG.search(line)
    if button:
        handler = HANDLER.search(line)
        found.append(Fact(number, "button", button.group(1), handler.group(2) if handler else ""))
    return found


def line_facts(number: int, line: str) -> list[Fact]:
    stripped = line.strip()
    if stripped.startswith(("//", "*", "/*")):
        return []
    is_import = stripped.startswith("import")
    found = _testids(number, line) + _controls(number, line)
    for kind, pattern, group, skip_imports in SIMPLE_RULES:
        if skip_imports and is_import:
            continue
        found += [Fact(number, kind, m.group(group)) for m in pattern.finditer(line)]
    return found


def extract(path: Path) -> PageFacts:
    rel = path.relative_to(ROOT).as_posix()
    lines = path.read_text(encoding="utf-8").splitlines()
    page = PageFacts(rel, len(lines))
    for number, line in enumerate(lines, start=1):
        page.facts.extend(line_facts(number, line))
    return page


def _code(text: str, fallback: str) -> str:
    return f"`{text}`" if text else fallback


def _unique(facts: list[Fact]) -> list[Fact]:
    seen: set[tuple[str, str]] = set()
    out = []
    for fact in facts:
        key = (fact.name, fact.extra)
        if key not in seen:
            seen.add(key)
            out.append(fact)
    return out


def render(
    page: PageFacts, catalogs: dict[str, dict[str, str]], defs: dict[str, tuple[str, int]]
) -> str:
    def ref(n: int) -> str:
        return f"`{page.path}:{n}`"

    out = [
        f"# Facts: `{Path(page.path).name}` (auto-generated, do not edit)",
        "",
        "> Generated by `scripts/frontend_spec/extract_facts.py` from the source; every row cites",
        "> the line it came from. Judgement (states, gaps, acceptance) lives in the screen spec.",
        "",
        f"- source: `{page.path}` ({page.line_count} lines)",
        "",
    ]
    texts = _unique(page.of("text"))
    out += ["## Visible texts (i18n keys)", "", "| Key | ko | en | Where |", "|---|---|---|---|"]
    for fact in texts:
        ko = catalogs.get("ko", {}).get(fact.name, "(missing in catalog)")
        en = catalogs.get("en", {}).get(fact.name, "(missing in catalog)")
        out.append(f"| `{fact.name}` | {ko} | {en} | {ref(fact.line)} |")
    if not texts:
        out.append("| (none) | | | |")
    out += ["", "## Form fields", "", "| Component | Label key | Where |", "|---|---|---|"]
    for fact in page.of("field"):
        out.append(
            f"| `<{fact.name}>` | {_code(fact.extra, '(no label on this line)')} | "
            f"{ref(fact.line)} |"
        )
    out += ["", "## Buttons and handlers", "", "| Element | Handler | Where |", "|---|---|---|"]
    for fact in page.of("button"):
        out.append(f"| `<{fact.name}>` | {_code(fact.extra, '-')} | {ref(fact.line)} |")
    out += ["", "## Data hooks", "", "| Hook | Defined in | Used at |", "|---|---|---|"]
    for fact in _unique(page.of("hook")):
        where = defs.get(fact.name)
        defined = f"`{where[0]}:{where[1]}`" if where else "(not found in shared-hooks)"
        out.append(f"| `{fact.name}` | {defined} | {ref(fact.line)} |")
    out += ["", "## Error handling calls", "", "| Name | Where |", "|---|---|"]
    for fact in _unique(page.of("error")):
        out.append(f"| `{fact.name}` | {ref(fact.line)} |")
    out += [
        "",
        "## Test ids, flags, accessibility attributes",
        "",
        "| Kind | Name | Where |",
        "|---|---|---|",
    ]
    for kind in ("testid", "flag", "a11y"):
        for fact in _unique(page.of(kind)):
            out.append(f"| {kind} | `{fact.name}` | {ref(fact.line)} |")
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page")
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)
    path = (ROOT / args.page).resolve()
    text = render(extract(path), load_catalogs(), hook_definitions())
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
