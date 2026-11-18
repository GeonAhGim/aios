"""Generate (or check) the auto-extracted fact sheets listed in docs/frontend/spec_manifest.json.

python -m scripts.frontend_spec.gen_facts           # write docs/frontend/facts/<Page>.facts.md
python -m scripts.frontend_spec.gen_facts --check   # exit 1 if a committed sheet is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from scripts.frontend_spec import extract_facts as ef

ROOT = ef.ROOT
MANIFEST = ROOT / "docs" / "frontend" / "spec_manifest.json"
OUT_DIR = ROOT / "docs" / "frontend" / "facts"


def screens() -> list[dict[str, str]]:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    items: list[dict[str, str]] = data["screens"]
    return items


def render_all() -> dict[Path, str]:
    catalogs = ef.load_catalogs()
    defs = ef.hook_definitions()
    out: dict[Path, str] = {}
    for item in screens():
        page = ROOT / item["page"]
        out[OUT_DIR / f"{page.stem}.facts.md"] = ef.render(ef.extract(page), catalogs, defs)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    rendered = render_all()
    if args.check:
        stale = [
            p.name
            for p, text in rendered.items()
            if not p.exists() or p.read_text(encoding="utf-8") != text
        ]
        if stale:
            print(f"STALE fact sheets (run gen_facts): {stale}", file=sys.stderr)
            return 1
        print(f"OK: {len(rendered)} fact sheets up to date")
        return 0
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for path, text in rendered.items():
        path.write_text(text, encoding="utf-8")
    print(f"wrote {len(rendered)} fact sheets to {OUT_DIR.relative_to(ROOT).as_posix()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
