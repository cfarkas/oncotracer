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
        self.assertIn('test "$PREPUBLISH_REGISTRY_STATE" = "$REGISTRY_STATE"', text)
        self.assertNotIn('if docker buildx imagetools inspect "$stable"', text)

    def test_existing_stable_digest_requires_exact_provenance(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('PUBLISHED_IMAGE="$IMAGE_NAME@$IMAGE_DIGEST"', text)
        self.assertIn(".source_commit == $commit", text)
        self.assertIn(".source_sha256 == $source", text)
        self.assertIn(".binary_sha256 == $binary", text)
        self.assertIn("/usr/local/bin/oncotracer", text)
        self.assertIn(
            'test "$PUBLISHED_BINARY_SHA256" = "$RELEASE_BINARY_SHA256"', text
        )
        self.assertIn("doctor --backend host", text)
        self.assertIn("published-container-doctor.json", text)
        self.assertIn(
            'cmp "$RUNNER_TEMP/container-provenance.json" \\\n'
            '            "$RUNNER_TEMP/published-container-provenance.json"',
            text,
        )
        self.assertIn("-c '! command -v nextflow'", text)

    def test_candidate_is_publicly_pullable_before_any_stable_mutation(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        push_index = text.index('docker push "$CANDIDATE_TAG"')
        anonymous_config_index = text.index(
            'ANONYMOUS_DOCKER_CONFIG="$RUNNER_TEMP/oncotracer-anonymous-docker-'
            '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"',
            push_index,
        )
        anonymous_pull_index = text.index(
            'docker pull "$CANDIDATE_IMAGE"', anonymous_config_index
        )
        anonymous_provenance_index = text.index(
            "anonymous-container-provenance.json", anonymous_pull_index
        )
        anonymous_nextflow_index = text.index(
            "-c '! command -v nextflow'", anonymous_provenance_index
        )
        reauthentication_index = text.index(
            "docker login ghcr.io", anonymous_nextflow_index
        )
        stable_mutation_index = text.index(
            "docker buildx imagetools create", reauthentication_index
        )
        release_mutation_index = text.index(
            'gh release upload "$RELEASE_TAG"', stable_mutation_index
        )
        self.assertLess(push_index, anonymous_config_index)
        self.assertLess(anonymous_config_index, anonymous_pull_index)
        self.assertLess(anonymous_pull_index, anonymous_provenance_index)
        self.assertLess(anonymous_provenance_index, anonymous_nextflow_index)
        self.assertLess(anonymous_nextflow_index, reauthentication_index)
        self.assertLess(reauthentication_index, stable_mutation_index)
        self.assertLess(stable_mutation_index, release_mutation_index)
        self.assertIn(
            '"ghcr.io/cfarkas/oncotracer:' 'v2.0.0-candidate-${GITHUB_RUN_ID}"',
            text,
        )
        self.assertNotIn('docker image rm "$CANDIDATE_TAG"', text)
        self.assertNotIn('docker image rm "$CANDIDATE_IMAGE"', text)
        self.assertIn("-u DOCKER_AUTH_CONFIG -u REGISTRY_AUTH_FILE", text)
        self.assertIn('DOCKER_CONFIG="$ANONYMOUS_DOCKER_CONFIG"', text)
        self.assertIn('test ! -e "$ANONYMOUS_DOCKER_CONFIG/config.json"', text)
        self.assertIn(
            'test "$ANONYMOUS_BINARY_SHA256" = "$RELEASE_BINARY_SHA256"',
            text,
        )
        self.assertIn(
            'cmp "$RUNNER_TEMP/container-provenance.json" \\\n'
            '            "$RUNNER_TEMP/anonymous-container-provenance.json"',
            text,
        )

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
        self.assertIn('gh release upload "$RELEASE_TAG" "${missing_assets[@]}"', text)
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

    def test_downloaded_release_gets_clean_room_beginner_acceptance(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        published_assets_index = text.index("published-release-assets")
        acceptance_index = text.index(
            'ACCEPTANCE_ROOT="$RUNNER_TEMP/oncotracer-released-beginner-acceptance-'
            '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"',
            published_assets_index,
        )
        checksum_index = text.index(
            "sha256sum --strict -c SHA256SUMS", acceptance_index
        )
        version_index = text.index(
            'beginner "$ACCEPTANCE_RELEASE/oncotracer" --version', checksum_index
        )
        isolated_cwd_index = text.index('cd "$ACCEPTANCE_RELEASE"', checksum_index)
        provenance_index = text.index(
            'beginner "$ACCEPTANCE_RELEASE/oncotracer" provenance --json',
            version_index,
        )
        install_index = text.index(
            'beginner "$ACCEPTANCE_RELEASE/oncotracer" install --conda',
            provenance_index,
        )
        quickstart_index = text.index("for quickstart in 1 2", install_index)
        self.assertLess(acceptance_index, checksum_index)
        self.assertLess(checksum_index, isolated_cwd_index)
        self.assertLess(isolated_cwd_index, version_index)
        self.assertLess(version_index, provenance_index)
        self.assertLess(provenance_index, install_index)
        self.assertLess(install_index, quickstart_index)
        self.assertIn('XDG_CONFIG_HOME="$BEGINNER_CONFIG"', text)
        self.assertIn('XDG_DATA_HOME="$BEGINNER_DATA"', text)
        self.assertIn('XDG_CACHE_HOME="$BEGINNER_CACHE"', text)
        self.assertIn("-u ONCOTRACER_PAYLOAD_CACHE", text)
        self.assertIn(".container.nextflow_present == false", text)
        self.assertIn('test ! -e "$BEGINNER_CONFIG"', text)
        self.assertIn('test ! -e "$BEGINNER_DATA"', text)
        self.assertIn('test ! -e "$BEGINNER_CACHE"', text)
        self.assertIn('test ! -e "$BEGINNER_ENVS"', text)


if __name__ == "__main__":
    unittest.main()
