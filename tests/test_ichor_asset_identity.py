#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import importlib.util
import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from oncotracer_cli import engine
from oncotracer_cli.engine import (
    ICHOR_ASSET_BASE,
    ICHOR_ASSET_SIZES,
    ICHOR_ASSETS,
    SAMURAI_ICHOR_COMMIT,
    _reference_identity,
    _validated_ichor_asset_reader,
    prepare_ichor_assets,
)
from oncotracer_cli.runtime import OncoTracerError, download


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "parity_audit_assets", ROOT / "tests/parity_audit.py"
)
assert SPEC and SPEC.loader
AUDIT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUDIT)


class IchorAssetIdentityTests(unittest.TestCase):
    def test_native_assets_bind_immutable_commit_size_and_sha256(self) -> None:
        self.assertEqual(SAMURAI_ICHOR_COMMIT, AUDIT.SAMURAI_COMMIT)
        self.assertIn(f"/{SAMURAI_ICHOR_COMMIT}/assets/ichorcna", ICHOR_ASSET_BASE)
        self.assertNotIn("/v1.4.0/", ICHOR_ASSET_BASE)
        expected = {
            filename: (ICHOR_ASSET_SIZES[key], digest)
            for key, (filename, digest) in ICHOR_ASSETS.items()
        }
        self.assertEqual(expected, AUDIT.ICHOR_ASSET_IDENTITIES)

    def test_owned_cache_downloads_with_exact_contract_and_holds_reader_lease(
        self,
    ) -> None:
        payloads = {
            "gc": b"gc-fixture",
            "map": b"map-fixture",
            "centromere": b"centromere-fixture",
            "reptime": b"reptime-fixture",
            "pon": b"static-HD-ULP-PoN-fixture",
        }
        fixture_assets = {
            key: (f"{key}.asset", hashlib.sha256(payload).hexdigest())
            for key, payload in payloads.items()
        }
        fixture_sizes = {key: len(payload) for key, payload in payloads.items()}
        calls: list[tuple[str, Path, dict[str, object]]] = []

        def download_asset(url: str, destination: Path, **kwargs: object) -> Path:
            filename = url.rsplit("/", 1)[-1]
            key = next(
                key
                for key, (name, _digest) in fixture_assets.items()
                if name == filename
            )
            self.assertEqual(
                kwargs,
                {
                    "expected_bytes": fixture_sizes[key],
                    "expected_sha256": fixture_assets[key][1],
                },
            )
            destination.write_bytes(payloads[key])
            calls.append((url, destination, kwargs))
            return destination

        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "native-v2-project"
            with (
                patch.object(engine, "ICHOR_ASSETS", fixture_assets),
                patch.object(engine, "ICHOR_ASSET_SIZES", fixture_sizes),
                patch.object(engine, "download", side_effect=download_asset),
            ):
                expected_identity = _reference_identity("ichorcna-hg38-500kb")
                assets = prepare_ichor_assets(project, 500)
                expected_root = (
                    project
                    / ".oncotracer"
                    / "reference-cache"
                    / f"ichorcna-hg38-500kb-{expected_identity[:16]}"
                )
                self.assertEqual(
                    {path.parent for path in assets.values()}, {expected_root}
                )
                self.assertFalse((project / "references").exists())
                marker = json.loads(
                    (expected_root / ".oncotracer-reference-owner.json").read_text(
                        encoding="utf-8"
                    )
                )
                self.assertEqual(
                    marker,
                    {
                        "schema": "oncotracer-reference-cache-owner-v1",
                        "kind": "ichorcna-hg38-500kb",
                        "identity": expected_identity,
                        "canonical_path": str(expected_root),
                    },
                )
                self.assertEqual(len(calls), len(fixture_assets))

                runner = SimpleNamespace(dry_run=False)
                with self.assertRaisesRegex(OncoTracerError, "size mismatch"):
                    with _validated_ichor_asset_reader(assets, runner):  # type: ignore[arg-type]
                        assets["pon"].write_bytes(b"changed")

                repaired = prepare_ichor_assets(project, 500)
                self.assertEqual(repaired["pon"].read_bytes(), payloads["pon"])
                self.assertEqual(len(calls), len(fixture_assets) + 1)

    def test_frozen_v1_reference_tree_isolated_from_native_owned_cache(self) -> None:
        payloads = {key: key.encode("utf-8") for key in ICHOR_ASSETS}
        fixture_assets = {
            key: (f"{key}.asset", hashlib.sha256(payload).hexdigest())
            for key, payload in payloads.items()
        }
        fixture_sizes = {key: len(payload) for key, payload in payloads.items()}

        def download_asset(url: str, destination: Path, **_kwargs: object) -> Path:
            filename = url.rsplit("/", 1)[-1]
            key = filename.removesuffix(".asset")
            destination.write_bytes(payloads[key])
            return destination

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            frozen_reference = (
                root
                / "frozen-v1-project"
                / "references"
                / "samurai_ichorcna_hg38_500kb"
            )
            frozen_reference.mkdir(parents=True)
            sentinel = frozen_reference / "v1-created-unpinned.asset"
            sentinel.write_bytes(b"frozen-v1")
            native_project = root / "native-v2-project"
            with (
                patch.object(engine, "ICHOR_ASSETS", fixture_assets),
                patch.object(engine, "ICHOR_ASSET_SIZES", fixture_sizes),
                patch.object(engine, "download", side_effect=download_asset),
            ):
                assets = prepare_ichor_assets(native_project, 500)
            self.assertTrue(
                all(native_project in path.parents for path in assets.values())
            )
            self.assertEqual(sentinel.read_bytes(), b"frozen-v1")
            self.assertFalse((native_project / "references").exists())

        driver = (ROOT / "scripts/ci_native_parity.sh").read_text(encoding="utf-8")
        self.assertIn('readonly V1_PROJECT_ROOT="$TEST_ROOT/frozen-v1-project"', driver)
        self.assertIn('readonly V2_PROJECT_ROOT="$TEST_ROOT/native-v2-project"', driver)
        self.assertIn(
            "resolve_owned_native_reference_root ichorcna-hg38-500kb",
            driver,
        )
        self.assertIn("resolve_owned_native_reference_root samurai-hg38", driver)
        self.assertNotIn(
            "$V2_PROJECT_ROOT/references/samurai_ichorcna_hg38_500kb", driver
        )

    def test_ci_resolves_only_exact_marker_owned_native_reference_cache(self) -> None:
        driver = (ROOT / "scripts/ci_native_parity.sh").read_text(encoding="utf-8")
        start = driver.index("resolve_owned_native_reference_root() {")
        end = driver.index('\n\n[[ ! -e "$TEST_ROOT"', start)
        function = driver[start:end]

        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "native-v2-project"
            project.mkdir()
            roots: dict[str, Path] = {}
            for kind in ("samurai-hg38", "ichorcna-hg38-500kb"):
                identity = _reference_identity(kind)
                root = (
                    project
                    / ".oncotracer"
                    / "reference-cache"
                    / f"{kind}-{identity[:16]}"
                )
                root.mkdir(parents=True)
                (root / ".oncotracer-reference-owner.json").write_text(
                    json.dumps(
                        {
                            "schema": "oncotracer-reference-cache-owner-v1",
                            "kind": kind,
                            "identity": identity,
                            "canonical_path": str(root),
                        }
                    ),
                    encoding="utf-8",
                )
                roots[kind] = root

            command = "\n".join(
                (
                    "set -Eeuo pipefail",
                    f"REPO={shlex.quote(str(ROOT))}",
                    f"V2_PROJECT_ROOT={shlex.quote(str(project))}",
                    'require_file() { [[ -f "$1" && ! -L "$1" ]]; }',
                    function,
                    "resolve_owned_native_reference_root samurai-hg38",
                    "resolve_owned_native_reference_root ichorcna-hg38-500kb",
                )
            )
            completed = subprocess.run(
                ["bash", "-c", command],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(
                completed.stdout.splitlines(),
                [str(roots["samurai-hg38"]), str(roots["ichorcna-hg38-500kb"])],
            )

            marker = roots["ichorcna-hg38-500kb"] / ".oncotracer-reference-owner.json"
            payload = json.loads(marker.read_text(encoding="utf-8"))
            payload["identity"] = "0" * 64
            marker.write_text(json.dumps(payload), encoding="utf-8")
            failed = subprocess.run(
                ["bash", "-c", command],
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("ownership mismatch", failed.stderr)

    def test_download_rejects_wrong_sha256_and_repairs_corrupt_destination(
        self,
    ) -> None:
        payload = b"immutable ichor asset\n"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            destination = root / "destination"
            source.write_bytes(payload)
            destination.write_bytes(b"corrupt")
            observed = download(
                source.as_uri(),
                destination,
                expected_bytes=len(payload),
                expected_sha256=digest,
                retries=1,
            )
            self.assertEqual(observed.read_bytes(), payload)
            destination.write_bytes(payload)
            with self.assertRaisesRegex(OncoTracerError, "validation failed"):
                download(
                    source.as_uri(),
                    destination,
                    expected_bytes=len(payload),
                    expected_sha256="0" * 64,
                    retries=1,
                )

    def test_audit_requires_exact_equal_frozen_and_native_manifests(self) -> None:
        lines = ["filename\tbytes\tsha256"]
        for filename, (size, digest) in sorted(AUDIT.ICHOR_ASSET_IDENTITIES.items()):
            lines.append(f"{filename}\t{size}\t{digest}")
        text = "\n".join(lines) + "\n"
        with tempfile.TemporaryDirectory() as raw:
            context = Path(raw)
            manifests = context / "manifests"
            manifests.mkdir()
            frozen = manifests / "frozen-ichorcna-assets-manifest.tsv"
            native = manifests / "native-ichorcna-assets-manifest.tsv"
            frozen.write_text(text, encoding="utf-8")
            native.write_text(text, encoding="utf-8")
            self.assertEqual(
                AUDIT.verify_ichor_asset_manifests(context, "quickstart1"),
                hashlib.sha256(text.encode()).hexdigest(),
            )
            native.write_text(text.replace("18efe127", "08efe127"), encoding="utf-8")
            with self.assertRaisesRegex(AUDIT.AuditError, "identity mismatch"):
                AUDIT.verify_ichor_asset_manifests(context, "quickstart1")


if __name__ == "__main__":
    unittest.main()
