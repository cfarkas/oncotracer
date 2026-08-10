#!/usr/bin/env python3
"""Regression tests for the permanent v2 release publisher."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-v2.yml"


class ReleaseWorkflowTests(unittest.TestCase):
    def test_stable_tags_copy_the_candidate_manifest_without_rewrapping(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "docker buildx imagetools create \\\n"
            "            --prefer-index=false",
            text,
        )
        self.assertIn(
            'for stable in "$IMAGE_NAME:2.0.0" "$IMAGE_NAME:v2.0.0"; do',
            text,
        )
        self.assertIn(
            'docker buildx imagetools inspect "$stable"',
            text,
        )
        self.assertIn('= "$IMAGE_DIGEST"', text)


if __name__ == "__main__":
    unittest.main()
