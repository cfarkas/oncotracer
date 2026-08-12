#!/usr/bin/env python3
"""Prevent host-wide cleanup from entering active CI and release surfaces."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTIVE_WORKFLOWS = (
    *sorted((ROOT / ".github" / "workflows").glob("*.yml")),
    *sorted((ROOT / ".github" / "workflows").glob("*.yaml")),
)
ACTIVE_CI_SURFACES = (
    *ACTIVE_WORKFLOWS,
    ROOT / "scripts" / "ci_native_parity.sh",
    ROOT / "scripts" / "release_registry_digest.sh",
    ROOT / "scripts" / "release_registry_pair.sh",
    ROOT / "scripts" / "validate_v2_release.sh",
)


FORBIDDEN_CLEANUP = (
    (
        "third-party whole-runner disk cleanup action",
        re.compile(
            r"\b(?:jlumbroso/free-disk-space|easimon/maximize-build-space|"
            r"AdityaGarg8/remove-unwanted-software)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "global Docker prune",
        re.compile(
            r"\bdocker\s+(?:system|image|container|volume|network|builder)\s+prune\b",
            re.IGNORECASE,
        ),
    ),
    (
        "Docker deletion driven by a global object listing",
        re.compile(
            r"\bdocker\s+(?:rm|rmi|image\s+rm|container\s+rm|volume\s+rm)\b"
            r"[^\n]*(?:\$\(|`)[^\n]*\bdocker\s+"
            r"(?:ps|images|image\s+ls|container\s+ls|volume\s+ls)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "piped Docker deletion driven by a global object listing",
        re.compile(
            r"\bdocker\s+"
            r"(?:ps|images|image\s+ls|container\s+ls|volume\s+ls)\b"
            r"[^\n]*\|\s*xargs\b[^\n]*\bdocker\s+"
            r"(?:rm|rmi|image\s+rm|container\s+rm|volume\s+rm)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "global Conda or Mamba cache cleanup",
        re.compile(r"\b(?:conda|mamba|micromamba)\s+clean\b", re.IGNORECASE),
    ),
    (
        "global Nextflow cleanup",
        re.compile(r"\bnextflow\s+clean\b", re.IGNORECASE),
    ),
    (
        "recursive filesystem deletion",
        re.compile(
            r"(?<![-\w])(?:sudo\s+)?rm\s+"
            r"(?=[^\n]*(?:(?<!\S)--recursive(?=\s|$)|"
            r"(?<!\S)-[A-Za-z]*r[A-Za-z]*(?=\s|$)))",
            re.IGNORECASE,
        ),
    ),
    (
        "recursive filesystem deletion through find",
        re.compile(r"\bfind\b[^\n]*(?:-delete\b|-exec\s+rm\b)", re.IGNORECASE),
    ),
)


def cleanup_violations(text: str) -> list[str]:
    """Return line-specific descriptions of host-wide cleanup commands."""
    violations: list[str] = []
    for description, pattern in FORBIDDEN_CLEANUP:
        for match in pattern.finditer(text):
            line_number = text.count("\n", 0, match.start()) + 1
            violations.append(f"line {line_number}: {description}")
    return sorted(violations)


class HostedCiStorageSafetyTests(unittest.TestCase):
    def test_active_ci_and_release_surfaces_have_no_host_wide_cleanup(self) -> None:
        self.assertTrue(ACTIVE_WORKFLOWS)
        for path in ACTIVE_CI_SURFACES:
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertTrue(path.is_file(), path)
                violations = cleanup_violations(path.read_text(encoding="utf-8"))
                self.assertEqual(
                    violations,
                    [],
                    f"unsafe cleanup in {path.relative_to(ROOT)}: {violations}",
                )

    def test_scanner_rejects_global_cleanup_variants(self) -> None:
        fixtures = (
            "uses: jlumbroso/free-disk-space@v1",
            "docker system prune --all --force",
            "docker image prune -af",
            "docker volume prune --force",
            "docker rmi $(docker images -aq)",
            "docker ps -aq | xargs -r docker rm -f",
            "sudo rm -rf /opt/hostedtoolcache/CodeQL",
            "rm --recursive --force \"$HOME/.nextflow\"",
            "find /opt/hostedtoolcache -mindepth 1 -delete",
            "conda clean --all -y",
            "micromamba clean --all --yes",
            "nextflow clean -f",
        )
        for fixture in fixtures:
            with self.subTest(command=fixture):
                self.assertTrue(cleanup_violations(fixture), fixture)

    def test_scanner_allows_non_destructive_and_exact_object_commands(self) -> None:
        fixtures = (
            "df -h",
            "docker image inspect oncotracer:v2-ci",
            "docker image rm oncotracer:v2-ci",
            "docker container rm oncotracer-v2-ci-${GITHUB_RUN_ID}",
            "conda list --explicit --prefix \"$RUNNER_TEMP/native-core\"",
            "rm -f -- \"$RUNNER_TEMP/oncotracer-v2-ci.marker\"",
            "rm -f -- \"$RUNNER_TEMP/exact-marker-r\"",
        )
        for fixture in fixtures:
            with self.subTest(command=fixture):
                self.assertEqual(cleanup_violations(fixture), [], fixture)


if __name__ == "__main__":
    unittest.main()
