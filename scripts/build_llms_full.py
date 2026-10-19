"""Regenerate llms-full.txt from the core documentation set (verbatim aggregation).

Usage: python scripts/build_llms_full.py   (run from the repository root)
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    "README.md", "docs/getting-started.md", "docs/architecture.md", "docs/api.md", "docs/faq.md",
    "docs/troubleshooting.md", "docs/data-model.md", "docs/cognition.md", "docs/remembering.md",
    "docs/clients.md", "docs/importers.md", "CONTRIBUTING.md", "SECURITY.md", "CHANGELOG.md",
]
HEADER = """# AbstractNote

> One endpoint for everything you want to remember: notes, files, photos, recordings and logs are filed with their time,
> place and provenance, linked to what you already know, and kept as a temporal semantic knowledge graph you can search by
> effort (quick, standard, deep) and revisit through time.

This file aggregates the core AbstractNote documentation in one place. Each section reproduces one repository file verbatim.

## Document Index

"""


def main() -> None:
    parts = [HEADER + "".join(f"- [{d}]({d})\n" for d in DOCS) + "\n---\n"]
    for d in DOCS:
        parts.append(f"\n## {d}\n\n{(ROOT / d).read_text().rstrip()}\n\n---\n")
    (ROOT / "llms-full.txt").write_text("".join(parts))
    print("llms-full.txt:", sum(1 for _ in (ROOT / "llms-full.txt").open()), "lines")


if __name__ == "__main__":
    main()
