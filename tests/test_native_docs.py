#!/usr/bin/env python3
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = [
    ROOT / "README.md",
    ROOT / "docs/index.md",
    ROOT / "docs/installation.md",
    ROOT / "docs/quick_start.md",
    ROOT / "docs/public_cohort.md",
    ROOT / "docs/auto_params.md",
    ROOT / "docs/running.md",
    ROOT / "docs/containers.md",
    ROOT / "docs/configuration_v2.md",
]


class NativeDocumentationTests(unittest.TestCase):
    def test_primary_docs_use_global_binary(self) -> None:
        for path in ACTIVE:
            text = path.read_text(encoding="utf-8")
            self.assertIn("oncotracer", text.lower(), path)
            self.assertNotIn("nextflow run", text.lower(), path)

    def test_readme_is_a_landing_page(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertLess(len(text.splitlines()), 100)
        self.assertIn("sudo install -m 0755 oncotracer", text)
        self.assertIn("complete documentation", text.lower())

    def test_all_markdown_fences_are_balanced(self) -> None:
        for path in [ROOT / "README.md", *ROOT.glob("docs/*.md")]:
            text = path.read_text(encoding="utf-8")
            self.assertEqual(text.count("```" ) % 2, 0, path)

    def test_mkdocs_contains_assurance_pages(self) -> None:
        text = (ROOT / "mkdocs.yml").read_text(encoding="utf-8")
        for page in ("native_architecture.md", "parity_release.md", "migration_v1_to_v2.md"):
            self.assertIn(page, text)


if __name__ == "__main__":
    unittest.main()
