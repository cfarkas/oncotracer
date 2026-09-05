from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli import reference_bundle as bundle
from oncotracer_cli.cli import _legacy_to_modern, build_parser
from oncotracer_cli.runtime import (
    CommandRunner,
    OncoTracerError,
    StageLedger,
    sha256_file,
)


class ReferenceBundleTests(unittest.TestCase):
    def fixture(self, root):
        files = bundle._files()
        base = {
            name: hashlib.sha256(name.encode()).hexdigest()
            for name, group in files.items()
            if group == "base"
        }
        records = []
        for i, (name, group) in enumerate(files.items()):
            data = name.encode()
            part = f"hg38-{i:02d}-0000.part"
            (root / part).write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()
            records.append(
                {
                    "path": name,
                    "group": group,
                    "bytes": len(data),
                    "sha256": digest,
                    "chunks": [{"name": part, "bytes": len(data), "sha256": digest}],
                }
            )
        manifest = {
            "schema": bundle.SCHEMA,
            "reference_sha256": base,
            "base_url": "",
            "files": records,
        }
        path = root / "hg38-reference.json"
        path.write_text(json.dumps(manifest))
        return path, manifest, base

    def test_stream_import_installs_only_requested_platform_without_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, value, base = self.fixture(root)
            destination = root / "project"
            with (
                patch.dict(bundle.engine.HG38_ASSETS, base, clear=True),
                patch.object(bundle, "_verify_imported_indexes") as verify,
                patch.object(
                    bundle.engine,
                    "_prepare_bwa_index",
                    side_effect=AssertionError("must not build"),
                ),
                patch.object(
                    bundle.engine,
                    "_prepare_minimap_index",
                    side_effect=AssertionError("must not build"),
                ),
            ):
                result = bundle.install_bundle(
                    str(manifest), destination, mode="illumina"
                )
            reference = Path(result["destination"])
            self.assertEqual((reference / "genome.fa").read_bytes(), b"genome.fa")
            self.assertTrue((reference / "bwa/genome.bwt").is_file())
            self.assertFalse((reference / "genome.fa.map-ont.mmi").exists())
            self.assertEqual(result["index_builds"], 0)
            verify.assert_called_once()
            self.assertEqual(verify.call_args.args[1], {"base", "bwa"})

    def test_preview_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, value, base = self.fixture(root)
            destination = root / "not-created"
            with patch.dict(bundle.engine.HG38_ASSETS, base, clear=True):
                result = bundle.install_bundle(
                    str(manifest), destination, mode="ont", dry_run=True
                )
            self.assertFalse(destination.exists())
            self.assertTrue(result["dry_run"])

    def test_bad_chunk_never_publishes_partial_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, value, base = self.fixture(root)
            (root / value["files"][0]["chunks"][0]["name"]).write_bytes(b"corrupt")
            destination = root / "project"
            with patch.dict(bundle.engine.HG38_ASSETS, base, clear=True):
                with self.assertRaisesRegex(OncoTracerError, "chunk"):
                    bundle.install_bundle(str(manifest), destination, mode="ont")
            self.assertFalse((destination / "references/samurai_hg38").exists())
            self.assertFalse(
                list((destination / "references").glob(".oncotracer-hg38-import-*"))
            )

    def test_existing_directory_and_symlinks_are_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, value, base = self.fixture(root)
            destination = root / "project"
            target = destination / "references/samurai_hg38"
            target.mkdir(parents=True)
            (target / "user-data").write_text("keep")
            with patch.dict(bundle.engine.HG38_ASSETS, base, clear=True):
                with self.assertRaisesRegex(OncoTracerError, "already exists"):
                    bundle.install_bundle(str(manifest), destination, mode="both")
            self.assertEqual((target / "user-data").read_text(), "keep")

    def test_manifest_hash_genome_mismatch_and_unsafe_paths_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, value, base = self.fixture(root)
            with patch.dict(bundle.engine.HG38_ASSETS, base, clear=True):
                with self.assertRaisesRegex(OncoTracerError, "SHA-256 mismatch"):
                    bundle.install_bundle(
                        str(manifest),
                        root / "project",
                        mode="both",
                        expected_sha256="0" * 64,
                    )
                for name in ("../escape", "/tmp/escape", "arbitrary.pkl"):
                    bad = copy.deepcopy(value)
                    bad["files"][0]["path"] = name
                    with self.subTest(name=name), self.assertRaises(OncoTracerError):
                        bundle.validate_manifest(bad)
                bad = copy.deepcopy(value)
                bad["files"][0]["chunks"][0]["name"] = "../escape.part"
                with self.assertRaises(OncoTracerError):
                    bundle.validate_manifest(bad)
                bad = copy.deepcopy(value)
                bad["reference_sha256"] = {"genome.fa": "0" * 64}
                with self.assertRaises(OncoTracerError):
                    bundle.validate_manifest(bad)

    def test_remote_manifest_requires_https_and_trusted_checksum(self):
        with patch.object(
            bundle, "urlopen", side_effect=AssertionError("must not connect")
        ):
            for source, digest in (
                ("https://example.org/reference.json", None),
                ("http://example.org/reference.json", "0" * 64),
            ):
                with self.assertRaises(OncoTracerError):
                    bundle._read_manifest(source, digest)

    def test_public_flags_explain_paths_and_platforms(self):
        values = [
            "reference",
            "install",
            "--manifest",
            "bundle.json",
            "--lpwgs-root",
            "/data/reference",
            "--mode",
            "ont",
            "--dry-run",
        ]
        self.assertEqual(_legacy_to_modern(values.copy()), values)
        args = build_parser().parse_args(values)
        self.assertEqual(args.mode, "ont")
        self.assertTrue(args.dry_run)


@unittest.skipUnless(
    os.environ.get("ONCOTRACER_TEST_REFERENCE_ROOT")
    and os.environ.get("ONCOTRACER_TEST_REFERENCE_CORE"),
    "opt-in real hg38 bundle and matching BWA/minimap2 installation required",
)
class RealReferenceBundleTests(unittest.TestCase):
    """Read an installed public bundle; never download or build indexes here."""

    def test_engine_reuses_both_indexes_and_maps_synthetic_reads(self):
        parent = Path(os.environ["ONCOTRACER_TEST_REFERENCE_ROOT"]).resolve(strict=True)
        root = parent / "references/samurai_hg38"
        self.assertTrue(
            root.is_dir(), "install the bundle before opting into this test"
        )
        core = Path(os.environ["ONCOTRACER_TEST_REFERENCE_CORE"]).resolve(strict=True)

        class ReadOnlyRunner(CommandRunner):
            def run(self, stage, command, **kwargs):
                if "build" in stage or "index" in [str(value) for value in command]:
                    raise AssertionError("the imported reference must never be rebuilt")
                return super().run(stage, command, **kwargs)

        with tempfile.TemporaryDirectory(
            prefix="oncotracer-real-reference-test-"
        ) as temporary:
            work = Path(temporary)
            runner = ReadOnlyRunner(work / "trace.tsv", echo=False)
            toolchain = bundle.engine.Toolchain(core_prefix=core)
            reference = bundle.engine.prepare_reference(
                parent,
                runner,
                StageLedger(work / "stages.json"),
                toolchain,
                need_bwa=True,
                need_minimap2=True,
                threads=2,
            )
            self.assertFalse(reference["reference_owned"])
            fai = {}
            for line in (root / "genome.fa.fai").read_text().splitlines():
                fields = line.split("\t")
                fai[fields[0]] = [int(value) for value in fields[1:5]]

            # Perfect synthetic reads from three public-genome loci, not samples.
            loci = [("chr1", 1_000_001), ("chr2", 2_000_001), ("chr3", 3_000_001)]
            sequences = []
            with (root / "genome.fa").open("rb") as fasta:
                for chrom, position in loci:
                    _length, offset, bases, width = fai[chrom]
                    start = position - 1
                    fasta.seek(offset + (start // bases) * width + start % bases)
                    sequence = b""
                    while len(sequence) < 1000:
                        sequence += fasta.readline().strip()
                    sequence = sequence[:1000].decode().upper()
                    self.assertLessEqual(set(sequence), set("ACGT"))
                    sequences.append(sequence)

            for kind, length, reader, arguments in (
                (
                    "bwa",
                    150,
                    bundle.engine._validated_bwa_reader,
                    [
                        toolchain.executable("core", "bwa"),
                        "mem",
                        "-t",
                        "2",
                        reference["bwa_prefix"],
                    ],
                ),
                (
                    "minimap2",
                    1000,
                    bundle.engine._validated_minimap_reader,
                    [
                        toolchain.executable("core", "minimap2"),
                        "-ax",
                        "map-ont",
                        "-t",
                        "2",
                        reference["minimap2_index"],
                    ],
                ),
            ):
                with self.subTest(index=kind):
                    reads = work / f"synthetic-{kind}.fastq"
                    reads.write_text(
                        "".join(
                            f"@synthetic-{i}\n{sequence[:length]}\n+\n{'I' * length}\n"
                            for i, sequence in enumerate(sequences)
                        )
                    )
                    sam = work / f"{kind}.sam"
                    with reader(reference, runner, toolchain):
                        with (
                            sam.open("wb") as stdout,
                            (work / f"{kind}.stderr").open("wb") as stderr,
                        ):
                            runner.run(
                                f"synthetic-{kind}-alignment",
                                [*arguments, reads],
                                stdout=stdout,
                                stderr=stderr,
                            )
                    primary = [
                        line.split("\t")
                        for line in sam.read_text().splitlines()
                        if not line.startswith("@")
                        and not (int(line.split("\t")[1]) & 0x900)
                    ]
                    self.assertEqual(len(primary), len(loci))
                    for record, (chrom, position) in zip(primary, loci):
                        self.assertFalse(int(record[1]) & 4, record[:6])
                        self.assertEqual((record[2], int(record[3])), (chrom, position))
                        self.assertGreater(int(record[4]), 0)


if __name__ == "__main__":
    unittest.main()
