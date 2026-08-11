#!/usr/bin/env python3
"""Regression tests for the permanent v2 release publisher."""

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-v2.yml"


class ReleaseWorkflowTests(unittest.TestCase):
    def test_stable_tags_are_classified_as_an_atomic_pair(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("scripts/release_registry_pair.sh"), 3)
        self.assertIn('"$IMAGE_NAME:2.0.0" "$IMAGE_NAME:v2.0.0"', text)
        self.assertIn('case "$STABLE_STATE" in', text)
        self.assertIn("missing)", text)
        self.assertIn("existing)", text)
        self.assertIn("partial)", text)
        self.assertIn(
            "docker buildx imagetools create \\\n"
            "                --prefer-index=false",
            text,
        )
        self.assertIn('--tag "$MISSING_STABLE_TAG"', text)
        self.assertIn(
            'test "$PREPUBLISH_REGISTRY_STATE" = "$REGISTRY_STATE"', text
        )
        self.assertNotIn('if docker buildx imagetools inspect "$stable"', text)

    def test_existing_stable_digest_requires_exact_provenance(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('PUBLISHED_IMAGE="$IMAGE_NAME@$IMAGE_DIGEST"', text)
        self.assertIn('.source_commit == $commit', text)
        self.assertIn('.source_sha256 == $source', text)
        self.assertIn('.binary_sha256 == $binary', text)
        self.assertIn('/usr/local/bin/oncotracer', text)
        self.assertIn('test "$PUBLISHED_BINARY_SHA256" = "$RELEASE_BINARY_SHA256"', text)
        self.assertIn('doctor --backend host', text)
        self.assertIn('published-container-doctor.json', text)
        self.assertIn(
            'cmp "$RUNNER_TEMP/container-provenance.json" \\\n'
            '            "$RUNNER_TEMP/published-container-provenance.json"',
            text,
        )
        self.assertIn("-c '! command -v nextflow'", text)

    def test_main_is_rechecked_immediately_before_stable_publication(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        guard_index = text.index("PREPUBLISH_REGISTRY_STATE=")
        recheck_index = text.index(
            'test "$(gh api "/repos/$GITHUB_REPOSITORY/commits/main"',
            guard_index,
        )
        publish_index = text.index("docker buildx imagetools create", recheck_index)
        self.assertLess(recheck_index, publish_index)
        self.assertNotIn("commits/main", text[publish_index:])

    def test_existing_release_assets_are_hash_checked_without_clobber(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("--clobber", text)
        self.assertIn('missing_assets+=("$asset")', text)
        self.assertIn(
            'gh release upload "$RELEASE_TAG" "${missing_assets[@]}"', text
        )
        self.assertIn('existing_sha256="$(sha256sum "$downloaded"', text)
        self.assertIn('test "$existing_sha256" = "$local_sha256"', text)
        self.assertIn("([.assets[].name] - $expected)", text)
        self.assertIn('published_sha256="$(sha256sum "$downloaded"', text)
        self.assertIn('test "$published_sha256" = "$local_sha256"', text)

    def test_existing_release_lookup_is_fail_closed(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('2> "$RUNNER_TEMP/existing-release.error"', text)
        self.assertIn("'\\(HTTP 404\\)[[:space:]]*$'", text)
        self.assertIn(
            'echo "unable to establish whether $RELEASE_TAG already exists"', text
        )


if __name__ == "__main__":
    unittest.main()
