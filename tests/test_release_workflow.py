#!/usr/bin/env python3
"""Regression tests for the permanent v2 release publisher."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release-v2.yml"
Q1_WORKFLOW = ROOT / ".github" / "workflows" / "native-v2-quickstart1-parity.yml"
Q2_WORKFLOW = ROOT / ".github" / "workflows" / "native-v2-quickstart2-parity.yml"
ACCEPTANCE = ROOT / "scripts" / "verify_release_acceptance.sh"
REGISTRY_PUT = ROOT / "scripts" / "release_registry_put_if_absent.sh"


class ReleaseWorkflowTests(unittest.TestCase):
    def test_parity_artifacts_and_release_provenance_bind_run_attempts(self) -> None:
        q1 = Q1_WORKFLOW.read_text(encoding="utf-8")
        q2 = Q2_WORKFLOW.read_text(encoding="utf-8")
        text = WORKFLOW.read_text(encoding="utf-8")

        self.assertIn(
            "name: native-v2-quickstart1-parity-${{ github.run_id }}-"
            "${{ github.run_attempt }}",
            q1,
        )
        self.assertIn(
            "name: native-v2-quickstart2-parity-${{ github.run_id }}-"
            "${{ github.run_attempt }}",
            q2,
        )
        self.assertIn("{id, run_attempt, name, html_url", text)
        for gate in ("ci", "q1", "q2"):
            self.assertIn(f"echo \"{gate}_attempt=$(jq -r '.run_attempt'", text)
            self.assertIn(
                f"{gate.upper()}_ATTEMPT: "
                f"${{{{ steps.gate.outputs.{gate}_attempt }}}}",
                text,
            )
        self.assertIn(
            'Q1_NAME="native-v2-quickstart1-parity-' '$Q1_RUN_ID-$Q1_RUN_ATTEMPT"',
            text,
        )
        self.assertIn(
            'Q2_NAME="native-v2-quickstart2-parity-' '$Q2_RUN_ID-$Q2_RUN_ATTEMPT"',
            text,
        )
        self.assertIn('test "$count" = 1', text)
        self.assertEqual(text.count("run_attempt: ($"), 3)
        helper = ACCEPTANCE.read_text(encoding="utf-8")
        self.assertIn("release-provenance-and-attempt-binding", helper)
        self.assertIn("native-v2-quickstart1-parity-", helper)
        self.assertIn("native-v2-quickstart2-parity-", helper)

    def test_stable_tags_are_classified_as_an_atomic_pair(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertGreaterEqual(text.count("scripts/release_registry_pair.sh"), 4)
        self.assertIn('"$IMAGE_NAME:2.0.0" "$IMAGE_NAME:v2.0.0"', text)
        self.assertIn('case "$STABLE_STATE" in', text)
        self.assertIn("missing)", text)
        self.assertIn("existing)", text)
        self.assertIn("partial)", text)
        self.assertEqual(
            text.count("bash scripts/release_registry_put_if_absent.sh"), 2
        )
        self.assertEqual(text.count("docker buildx imagetools create"), 1)
        self.assertIn('--tag "$CANDIDATE_TAG" "$PUBLISHED_IMAGE"', text)
        publisher = REGISTRY_PUT.read_text(encoding="utf-8")
        # GHCR ignores If-None-Match, so the header is sent only on the real
        # write and safety comes from confirming absence immediately before it
        # and the exact digest immediately after.
        self.assertEqual(publisher.count("--header 'If-None-Match: *'"), 1)
        self.assertIn("appeared before its conditional write", publisher)
        self.assertIn('if [[ "$CREATE_STATUS" == 201 ]]', publisher)
        self.assertNotIn("PROBE_STATUS", publisher)
        self.assertIn('test "$AUTHENTICATED_REGISTRY_STATE" = "$REGISTRY_STATE"', text)
        self.assertIn(
            'test "$PREPUBLISH_REGISTRY_STATE" = "$EXPECTED_REGISTRY_STATE"',
            text,
        )
        self.assertIn(
            'test "$FINAL_REGISTRY_STATE" = "$EXPECTED_FINAL_REGISTRY_STATE"',
            text,
        )
        self.assertNotIn('if docker buildx imagetools inspect "$stable"', text)

    def test_conditional_registry_publisher_never_overwrites(self) -> None:
        manifest = b'{"schemaVersion":2}\n'
        digest = "sha256:" + hashlib.sha256(manifest).hexdigest()
        source = "ghcr.io/cfarkas/oncotracer:v2.0.0-candidate-123-1"
        target = "ghcr.io/cfarkas/oncotracer:2.0.0"
        second_target = "ghcr.io/cfarkas/oncotracer:v2.0.0"
        main_sha = "a" * 40

        fake_docker = r"""#!/usr/bin/env bash
set -Eeuo pipefail
test "$#" -eq 4
test "$1" = buildx && test "$2" = imagetools && test "$3" = inspect
case "$4" in
  ghcr.io/cfarkas/oncotracer:v2.0.0-candidate-123-1)
    printf 'Name: source\nDigest: %s\n' "$FAKE_DIGEST"
    ;;
  ghcr.io/cfarkas/oncotracer:2.0.0)
    if [[ "$(cat "$FAKE_TARGET_STATE")" == present ]]; then
      printf 'Name: target\nDigest: %s\n' "$FAKE_DIGEST"
    else
      printf 'manifest unknown\n'
      exit 1
    fi
    ;;
  ghcr.io/cfarkas/oncotracer:v2.0.0)
    if [[ "$(cat "$FAKE_SECOND_TARGET_STATE")" == present ]]; then
      printf 'Name: second-target\nDigest: %s\n' "$FAKE_DIGEST"
    else
      printf 'manifest unknown\n'
      exit 1
    fi
    ;;
  *)
    exit 98
    ;;
esac
"""
        fake_curl = r"""#!/usr/bin/env bash
set -Eeuo pipefail
cat >/dev/null
url=''
headers=''
output=''
method=GET
while [[ "$#" -gt 0 ]]; do
  case "$1" in
    --dump-header|--output|--request|--header|--data-binary|--write-out|--data-urlencode|--config)
      option="$1"
      value="$2"
      case "$option" in
        --dump-header) headers="$value" ;;
        --output) output="$value" ;;
        --request) method="$value" ;;
      esac
      shift 2
      ;;
    --fail|--silent|--show-error|--location|--get)
      shift
      ;;
    http://*|https://*)
      url="$1"
      shift
      ;;
    *)
      shift
      ;;
  esac
done
if [[ "$url" == https://ghcr.io/token ]]; then
  printf '{"token":"fake.bearer"}\n'
  exit 0
fi
if [[ "$method" == GET && "$url" == *"/manifests/$FAKE_DIGEST" ]]; then
  printf 'HTTP/1.1 200 OK\r\nContent-Type: application/vnd.oci.image.manifest.v1+json\r\nDocker-Content-Digest: %s\r\n\r\n' "$FAKE_DIGEST" > "$headers"
  cp "$FAKE_MANIFEST" "$output"
  exit 0
fi
if [[ "$method" == PUT && "$url" == *'/manifests/v2.0.0-candidate-123-1' ]]; then
  status="${FAKE_PROBE_STATUS:-412}"
  : > "$headers"
  : > "$output"
  printf '%s' "$status"
  exit 0
fi
if [[ "$method" == PUT && "$url" == *'/manifests/2.0.0' ]]; then
  status="${FAKE_CREATE_STATUS:-201}"
  if [[ "$status" == 201 ]]; then
    printf 'HTTP/1.1 201 Created\r\nDocker-Content-Digest: %s\r\n\r\n' "$FAKE_DIGEST" > "$headers"
    printf 'present\n' > "$FAKE_TARGET_STATE"
  elif [[ "$status" == 412 && "${FAKE_CONFLICT_EXACT:-0}" == 1 ]]; then
    : > "$headers"
    printf 'present\n' > "$FAKE_TARGET_STATE"
  else
    : > "$headers"
  fi
  : > "$output"
  printf '%s' "$status"
  exit 0
fi
if [[ "$method" == PUT && "$url" == *'/manifests/v2.0.0' ]]; then
  status="${FAKE_SECOND_CREATE_STATUS:-201}"
  if [[ "$status" == 201 ]]; then
    printf 'HTTP/1.1 201 Created\r\nDocker-Content-Digest: %s\r\n\r\n' "$FAKE_DIGEST" > "$headers"
    printf 'present\n' > "$FAKE_SECOND_TARGET_STATE"
  else
    : > "$headers"
  fi
  : > "$output"
  printf '%s' "$status"
  exit 0
fi
echo "unexpected fake curl request: $method $url" >&2
exit 97
"""
        fake_gh = r"""#!/usr/bin/env bash
set -Eeuo pipefail
test "$*" = 'api /repos/cfarkas/oncotracer/commits/main --jq .sha'
count="$(cat "$FAKE_GH_COUNT")"
printf '%s\n' "$((count + 1))" > "$FAKE_GH_COUNT"
printf '%s\n' "$FAKE_MAIN"
"""

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            fake_bin = root / "bin"
            runner_temp = root / "runner-temp"
            fake_bin.mkdir()
            runner_temp.mkdir(mode=0o700)
            (root / "manifest.json").write_bytes(manifest)
            state = root / "target-state"
            state.write_text("absent\n", encoding="utf-8")
            second_state = root / "second-target-state"
            second_state.write_text("absent\n", encoding="utf-8")
            gh_count = root / "gh-count"
            gh_count.write_text("0\n", encoding="utf-8")
            (fake_bin / "docker").write_text(fake_docker, encoding="utf-8")
            (fake_bin / "curl").write_text(fake_curl, encoding="utf-8")
            (fake_bin / "gh").write_text(fake_gh, encoding="utf-8")
            (fake_bin / "docker").chmod(0o755)
            (fake_bin / "curl").chmod(0o755)
            (fake_bin / "gh").chmod(0o755)

            environment = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ['PATH']}",
                "RUNNER_TEMP": str(runner_temp),
                "GHCR_USERNAME": "runner",
                "GHCR_TOKEN": "secret-token",
                "GH_TOKEN": "secret-token",
                "FAKE_DIGEST": digest,
                "FAKE_MAIN": main_sha,
                "FAKE_MANIFEST": str(root / "manifest.json"),
                "FAKE_TARGET_STATE": str(state),
                "FAKE_SECOND_TARGET_STATE": str(second_state),
                "FAKE_GH_COUNT": str(gh_count),
            }

            def publish(
                *targets: str, **overrides: str
            ) -> subprocess.CompletedProcess[str]:
                selected_targets = targets or (target,)
                return subprocess.run(
                    [
                        "bash",
                        str(REGISTRY_PUT),
                        source,
                        digest,
                        main_sha,
                        *selected_targets,
                    ],
                    cwd=ROOT,
                    env={**environment, **overrides},
                    check=False,
                    capture_output=True,
                    text=True,
                )

            success = publish()
            self.assertEqual(success.returncode, 0, success.stderr)
            self.assertEqual(state.read_text(encoding="utf-8"), "present\n")

            # A target that already exists must never be overwritten.
            already_present = publish()
            self.assertNotEqual(already_present.returncode, 0)
            self.assertIn("was not absent", already_present.stderr)

            state.write_text("absent\n", encoding="utf-8")

            conflict = publish(FAKE_CREATE_STATUS="412")
            self.assertNotEqual(conflict.returncode, 0)
            self.assertEqual(state.read_text(encoding="utf-8"), "absent\n")

            main_drift = publish(FAKE_MAIN="b" * 40)
            self.assertNotEqual(main_drift.returncode, 0)
            self.assertEqual(state.read_text(encoding="utf-8"), "absent\n")

            exact_concurrent_create = publish(
                FAKE_CREATE_STATUS="412", FAKE_CONFLICT_EXACT="1"
            )
            self.assertEqual(
                exact_concurrent_create.returncode, 0, exact_concurrent_create.stderr
            )
            self.assertEqual(state.read_text(encoding="utf-8"), "present\n")

            state.write_text("absent\n", encoding="utf-8")
            second_state.write_text("absent\n", encoding="utf-8")
            gh_count.write_text("0\n", encoding="utf-8")
            pair = publish(target, second_target)
            self.assertEqual(pair.returncode, 0, pair.stderr)
            self.assertEqual(state.read_text(encoding="utf-8"), "present\n")
            self.assertEqual(second_state.read_text(encoding="utf-8"), "present\n")
            self.assertEqual(gh_count.read_text(encoding="utf-8"), "1\n")

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

    def test_partial_repair_rebinds_attempt_owned_probe_to_selected_digest(
        self,
    ) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        authenticate_index = text.index('PUBLISHED_IMAGE="$IMAGE_NAME@$IMAGE_DIGEST"')
        rebind_index = text.index(
            'if [[ "$CANDIDATE_DIGEST" != "$IMAGE_DIGEST" ]]', authenticate_index
        )
        rebind_write_index = text.index(
            '--tag "$CANDIDATE_TAG" "$PUBLISHED_IMAGE"', rebind_index
        )
        exact_probe_index = text.index(
            'scripts/release_registry_digest.sh "$CANDIDATE_TAG"',
            rebind_write_index,
        )
        output_index = text.index(
            'echo "candidate_tag=$CANDIDATE_TAG"', exact_probe_index
        )
        partial_index = text.index("partial)", text.index("Recheck and publish"))
        conditional_index = text.index(
            '"$CANDIDATE_TAG" "$IMAGE_DIGEST" "$MAIN_SHA" \\\n'
            '                "$MISSING_STABLE_TAG"',
            partial_index,
        )
        self.assertLess(rebind_index, rebind_write_index)
        self.assertLess(rebind_write_index, exact_probe_index)
        self.assertLess(exact_probe_index, output_index)
        self.assertLess(output_index, conditional_index)

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
        preaccept_index = text.index(
            "Clean-room accept local release assets before publication",
            reauthentication_index,
        )
        stable_mutation_index = text.index(
            "bash scripts/release_registry_put_if_absent.sh", preaccept_index
        )
        release_mutation_index = text.index(
            'gh release upload "$RELEASE_TAG"', stable_mutation_index
        )
        self.assertLess(push_index, anonymous_config_index)
        self.assertLess(anonymous_config_index, anonymous_pull_index)
        self.assertLess(anonymous_pull_index, anonymous_provenance_index)
        self.assertLess(anonymous_provenance_index, anonymous_nextflow_index)
        self.assertLess(anonymous_nextflow_index, reauthentication_index)
        self.assertLess(reauthentication_index, preaccept_index)
        self.assertLess(preaccept_index, stable_mutation_index)
        self.assertLess(stable_mutation_index, release_mutation_index)
        self.assertIn(
            '"ghcr.io/cfarkas/oncotracer:v2.0.0-candidate-'
            '${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"',
            text,
        )
        self.assertNotIn('docker image rm "$CANDIDATE_TAG"', text)
        self.assertNotIn('docker image rm "$CANDIDATE_IMAGE"', text)
        self.assertIn("-u DOCKER_AUTH_CONFIG -u REGISTRY_AUTH_FILE", text)
        self.assertIn('DOCKER_CONFIG="$ANONYMOUS_DOCKER_CONFIG"', text)
        self.assertIn('test ! -e "$ANONYMOUS_DOCKER_CONFIG/config.json"', text)
        self.assertIn(
            'test "$ANONYMOUS_BINARY_SHA256" = "$RELEASE_BINARY_SHA256"', text
        )
        self.assertIn(
            'cmp "$RUNNER_TEMP/container-provenance.json" \\\n'
            '            "$RUNNER_TEMP/anonymous-container-provenance.json"',
            text,
        )

    def test_local_and_downloaded_assets_use_same_acceptance_before_publish(
        self,
    ) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        asset_index = text.index("Build final release provenance and assets")
        calls = [
            index
            for index in range(len(text))
            if text.startswith("bash scripts/verify_release_acceptance.sh", index)
        ]
        self.assertEqual(len(calls), 2)
        stable_index = text.index("bash scripts/release_registry_put_if_absent.sh")
        release_index = min(
            text.index('gh release upload "$RELEASE_TAG"'),
            text.index('gh release create "$RELEASE_TAG"'),
        )
        download_index = text.index(
            'PUBLISHED_RELEASE_DOWNLOAD="$RUNNER_TEMP/', release_index
        )
        self.assertLess(asset_index, calls[0])
        self.assertLess(calls[0], stable_index)
        self.assertLess(stable_index, release_index)
        self.assertLess(release_index, download_index)
        self.assertLess(download_index, calls[1])
        prepublication_prefix = text[: calls[0]]
        for mutation in (
            "bash scripts/release_registry_put_if_absent.sh",
            'gh release create "$RELEASE_TAG"',
            'gh release upload "$RELEASE_TAG"',
            'gh release edit "$RELEASE_TAG"',
        ):
            self.assertNotIn(mutation, prepublication_prefix)
        self.assertIn('cmp "$PREPUBLICATION_ACCEPTANCE/acceptance-evidence.json"', text)
        self.assertNotIn("beginner() {", text)
        self.assertIn('cat > "$RUNNER_TEMP/RELEASE_NOTES.md"', text)
        self.assertNotIn("release/RELEASE_NOTES.md", text)
        self.assertIn('PARITY_DIR="$RUNNER_TEMP/release-parity-', text)

    def test_main_and_registry_are_rechecked_adjacent_to_stable_publish(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        step_index = text.index("Recheck and publish stable container aliases")
        guard_index = text.index("PREPUBLISH_REGISTRY_STATE=", step_index)
        equality_index = text.index(
            'test "$PREPUBLISH_REGISTRY_STATE" = "$EXPECTED_REGISTRY_STATE"',
            guard_index,
        )
        recheck_index = text.index(
            'test "$(gh api "/repos/$GITHUB_REPOSITORY/commits/main"',
            equality_index,
        )
        publish_index = text.index(
            "bash scripts/release_registry_put_if_absent.sh", recheck_index
        )
        self.assertLess(guard_index, equality_index)
        self.assertLess(equality_index, recheck_index)
        self.assertLess(recheck_index, publish_index)
        between = text[recheck_index:publish_index]
        self.assertNotIn("gh release", between)
        self.assertNotIn("docker pull", between)
        publisher = REGISTRY_PUT.read_text(encoding="utf-8")
        absent_index = publisher.index('test -z "$target_before"')
        helper_main_index = publisher.index(
            "gh api /repos/cfarkas/oncotracer", absent_index
        )
        conditional_put_index = publisher.index(
            'CREATE_STATUS="$(registry_curl', helper_main_index
        )
        self.assertLess(absent_index, helper_main_index)
        self.assertLess(helper_main_index, conditional_put_index)

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

    def test_acceptance_helper_isolated_and_fail_closed(self) -> None:
        helper = ACCEPTANCE.read_text(encoding="utf-8")
        self.assertIn("expected_assets=(", helper)
        self.assertIn('test "${#release_entries[@]}" -eq', helper)
        self.assertIn("sha256sum --strict -c SHA256SUMS", helper)
        self.assertIn('XDG_CONFIG_HOME="$BEGINNER_CONFIG"', helper)
        self.assertIn('XDG_DATA_HOME="$BEGINNER_DATA"', helper)
        self.assertIn('XDG_CACHE_HOME="$BEGINNER_CACHE"', helper)
        self.assertIn("-u ONCOTRACER_PAYLOAD_CACHE", helper)
        self.assertIn(".container.nextflow_present == false", helper)
        self.assertIn(".parity_audit_bundle_sha256 == $parity", helper)
        self.assertIn('! -L "$ACCEPTANCE_ROOT"', helper)
        self.assertIn("def positive_integer:", helper)
        self.assertIn(".workflows.quickstart1.artifact.id | positive_integer", helper)
        self.assertIn('path_is_absent "$BEGINNER_ENVS"', helper)
        self.assertIn('find "$ACCEPTANCE_HOME"', helper)
        self.assertIn('test -z "$(find "$ACCEPTANCE_TMP"', helper)

    def test_acceptance_helper_has_identical_pre_post_evidence_and_rejects_tamper(
        self,
    ) -> None:
        main_sha = "a" * 40
        source_sha = "b" * 64
        image_digest = "sha256:" + "c" * 64
        executable_template = """#!/usr/bin/env bash
set -Eeuo pipefail
case "${1:-}" in
  --version)
    printf 'OncoTracer 2.0.0\\n'
    ;;
  --help)
    printf 'Native LP-WGS CNA analysis.\\n'
    ;;
  provenance)
    test "${2:-}" = --json
    binary="$(sha256sum "$0" | awk '{print $1}')"
    printf '{"source_commit":"MAIN","source_sha256":"SOURCE","source_tree_dirty":false,"binary_sha256":"%s"}\\n' "$binary"
    ;;
  install)
    [[ " $* " == *" --conda "* && " $* " == *" --dry-run "* ]]
    printf 'install dry-run\\n'
    ;;
  quickstart)
    [[ "${2:-}" == 1 || "${2:-}" == 2 ]]
    [[ " $* " == *" --backend conda "* && " $* " == *" --dry-run "* ]]
    printf 'dry-run completed without writing files\\n'
    ;;
  *)
    exit 64
    ;;
esac
"""
        executable = executable_template.replace("MAIN", main_sha).replace(
            "SOURCE", source_sha
        )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            release = root / "release-source"
            release.mkdir()
            (release / "oncotracer").write_text(executable, encoding="utf-8")
            (release / "oncotracer-v2.0.0-parity-audit.tar.gz").write_bytes(
                b"parity-audit\n"
            )
            binary_sha = hashlib.sha256(
                (release / "oncotracer").read_bytes()
            ).hexdigest()
            parity_sha = hashlib.sha256(
                (release / "oncotracer-v2.0.0-parity-audit.tar.gz").read_bytes()
            ).hexdigest()
            provenance = {
                "schema": "oncotracer-v2-release-provenance-v3",
                "version": "2.0.0",
                "release_tag": "v2.0.0",
                "source_commit": main_sha,
                "source_sha256": source_sha,
                "source_tree_dirty": False,
                "binary_sha256": binary_sha,
                "parity_audit_bundle_sha256": parity_sha,
                "native_nextflow_required": False,
                "container": {
                    "reference": f"ghcr.io/cfarkas/oncotracer@{image_digest}",
                    "digest": image_digest,
                    "nextflow_present": False,
                },
                "frozen_comparator": {
                    "oncotracer_commit": "032c1268fa7fdcadc48087055066d7a9fc59bd89",
                    "oncotracer_image": (
                        "carlosfarkas/oncotracer@sha256:"
                        "4856aed020e1102f891b91de54d6acf365d6b8a57e2283a4f7b670b0bd5b07ed"
                    ),
                    "samurai_commit": "6a901940288b008237703c6b181d447e7dee4fcf",
                    "nextflow_version": "26.04.6",
                    "nextflow_sha256": (
                        "182a63c74074e2dc7956ffa3c8cd59de952ed2c44394e21faf5e1736b945444c"
                    ),
                    "qdnaseq_source_commit": (
                        "cf7c07e39de0ac64a9c38cb030cba4626e2aae83"
                    ),
                },
                "workflows": {
                    "native_v2_ci": {
                        "run_id": 100,
                        "run_attempt": 2,
                        "url": "https://github.com/cfarkas/oncotracer/actions/runs/100",
                        "sha": main_sha,
                    },
                    "quickstart1": {
                        "run_id": 101,
                        "run_attempt": 3,
                        "url": "https://github.com/cfarkas/oncotracer/actions/runs/101",
                        "sha": main_sha,
                        "artifact": {
                            "id": 201,
                            "name": "native-v2-quickstart1-parity-101-3",
                            "digest": "sha256:" + "d" * 64,
                        },
                    },
                    "quickstart2": {
                        "run_id": 102,
                        "run_attempt": 4,
                        "url": "https://github.com/cfarkas/oncotracer/actions/runs/102",
                        "sha": main_sha,
                        "artifact": {
                            "id": 202,
                            "name": "native-v2-quickstart2-parity-102-4",
                            "digest": "sha256:" + "e" * 64,
                        },
                    },
                },
            }
            provenance_path = release / "release-provenance.json"
            provenance_path.write_text(
                json.dumps(provenance, sort_keys=True) + "\n", encoding="utf-8"
            )

            def write_sums() -> None:
                names = (
                    "oncotracer",
                    "oncotracer-v2.0.0-parity-audit.tar.gz",
                    "release-provenance.json",
                )
                lines = [
                    f"{hashlib.sha256((release / name).read_bytes()).hexdigest()}  {name}"
                    for name in names
                ]
                (release / "SHA256SUMS").write_text(
                    "\n".join(lines) + "\n", encoding="utf-8"
                )

            write_sums()

            def run_helper(target: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        "bash",
                        str(ACCEPTANCE),
                        str(release),
                        str(root / target),
                        main_sha,
                        source_sha,
                        binary_sha,
                        image_digest,
                    ],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )

            pre = run_helper("pre")
            self.assertEqual(pre.returncode, 0, pre.stderr)
            post = run_helper("post")
            self.assertEqual(post.returncode, 0, post.stderr)
            self.assertEqual(
                (root / "pre" / "acceptance-evidence.json").read_bytes(),
                (root / "post" / "acceptance-evidence.json").read_bytes(),
            )

            (release / "unexpected").write_text("unexpected\n", encoding="utf-8")
            unexpected = run_helper("unexpected-run")
            self.assertNotEqual(unexpected.returncode, 0)
            (release / "unexpected").unlink()

            dangling_root = root / "dangling-acceptance-root"
            dangling_root.symlink_to(root / "absent-target", target_is_directory=True)
            dangling = run_helper(dangling_root.name)
            self.assertNotEqual(dangling.returncode, 0)
            dangling_root.unlink()

            hardlink = root / "parity-hardlink"
            os.link(release / "oncotracer-v2.0.0-parity-audit.tar.gz", hardlink)
            linked = run_helper("hardlinked-source-run")
            self.assertNotEqual(linked.returncode, 0)
            hardlink.unlink()

            provenance["workflows"]["quickstart1"]["artifact"][
                "name"
            ] = "native-v2-quickstart1-parity-101"
            provenance_path.write_text(
                json.dumps(provenance, sort_keys=True) + "\n", encoding="utf-8"
            )
            write_sums()
            tampered = run_helper("tampered-run")
            self.assertNotEqual(tampered.returncode, 0)

            provenance["workflows"]["quickstart1"]["artifact"][
                "name"
            ] = "native-v2-quickstart1-parity-101-3"
            for target, value in (("string-attempt", "3"), ("fraction-attempt", 3.5)):
                provenance["workflows"]["quickstart1"]["run_attempt"] = value
                provenance_path.write_text(
                    json.dumps(provenance, sort_keys=True) + "\n", encoding="utf-8"
                )
                write_sums()
                bad_type = run_helper(target)
                self.assertNotEqual(bad_type.returncode, 0)

            provenance["workflows"]["quickstart1"]["run_attempt"] = 3
            provenance["workflows"]["quickstart1"]["artifact"]["id"] = "201"
            provenance_path.write_text(
                json.dumps(provenance, sort_keys=True) + "\n", encoding="utf-8"
            )
            write_sums()
            string_artifact_id = run_helper("string-artifact-id")
            self.assertNotEqual(string_artifact_id.returncode, 0)

            provenance["workflows"]["quickstart1"]["artifact"]["id"] = 201
            provenance["workflows"]["quickstart1"]["artifact"][
                "digest"
            ] = "sha256:not-a-digest"
            provenance_path.write_text(
                json.dumps(provenance, sort_keys=True) + "\n", encoding="utf-8"
            )
            write_sums()
            malformed_digest = run_helper("malformed-artifact-digest")
            self.assertNotEqual(malformed_digest.returncode, 0)

            provenance["workflows"]["quickstart1"]["artifact"]["digest"] = (
                "sha256:" + "d" * 64
            )
            provenance["workflows"]["quickstart2"]["sha"] = "f" * 40
            provenance_path.write_text(
                json.dumps(provenance, sort_keys=True) + "\n", encoding="utf-8"
            )
            write_sums()
            mismatched_workflow_sha = run_helper("mismatched-workflow-sha")
            self.assertNotEqual(mismatched_workflow_sha.returncode, 0)

            provenance["workflows"]["quickstart2"]["sha"] = main_sha
            provenance["parity_audit_bundle_sha256"] = "0" * 64
            provenance_path.write_text(
                json.dumps(provenance, sort_keys=True) + "\n", encoding="utf-8"
            )
            write_sums()
            mismatched_parity_hash = run_helper("mismatched-parity-hash")
            self.assertNotEqual(mismatched_parity_hash.returncode, 0)

            provenance["parity_audit_bundle_sha256"] = parity_sha
            side_effect_executable = executable.replace(
                "  install)\n",
                "  install)\n"
                '    acceptance_root="${HOME%/home}"\n'
                '    chmod 0777 "$acceptance_root"\n'
                '    ln -s "$acceptance_root/absent-cache" "$XDG_CACHE_HOME"\n'
                '    rmdir "$HOME"\n'
                '    ln -s "$acceptance_root/absent-home" "$HOME"\n',
            )
            (release / "oncotracer").write_text(
                side_effect_executable, encoding="utf-8"
            )
            binary_sha = hashlib.sha256(
                (release / "oncotracer").read_bytes()
            ).hexdigest()
            provenance["binary_sha256"] = binary_sha
            provenance_path.write_text(
                json.dumps(provenance, sort_keys=True) + "\n", encoding="utf-8"
            )
            write_sums()
            side_effect = run_helper("side-effect-run")
            self.assertNotEqual(side_effect.returncode, 0)
            self.assertTrue((root / "side-effect-run" / "cache").is_symlink())
            self.assertTrue((root / "side-effect-run" / "home").is_symlink())


if __name__ == "__main__":
    unittest.main()
