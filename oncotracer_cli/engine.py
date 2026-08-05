"""Native LP-WGS execution engine for OncoTracer v2."""

from __future__ import annotations

import csv
import gzip
import json
import math
import os
import re
import shutil
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from . import __version__
from .classifier import run_native_classifier
from .runtime import (
    CommandRunner,
    OncoTracerError,
    StageLedger,
    atomic_write_json,
    atomic_write_text,
    download,
    load_flat_yaml,
    require_command,
    require_directory,
    require_file,
    runtime_root,
    sha256_file,
    utc_now,
)

HG38_BASE = "https://ngi-igenomes.s3.amazonaws.com/igenomes/Homo_sapiens/UCSC/hg38/Sequence/WholeGenomeFasta"
ICHOR_ASSET_BASE = "https://raw.githubusercontent.com/DIncalciLab/samurai/v1.4.0/assets/ichorcna"
ICHOR_ASSETS = {
    "gc": "gc_hg38_500kb.wig",
    "map": "map_hg38_500kb.wig",
    "centromere": "GRCh38.GCA_000001405.2_centromere_acen.txt",
    "reptime": "Koren_repTiming_hg38_500kb.wig",
    "pon": "HD_ULP_PoN_hg38_500kb_median_normAutosome_median.rds",
}


@dataclass(frozen=True)
class IlluminaSample:
    sample: str
    fastq_1: Path
    fastq_2: Path | None
    status: str


@dataclass(frozen=True)
class OntSample:
    sample: str
    barcode: str
    fastq_dir: Path
    status: str = "tumor"


@dataclass
class Toolchain:
    """Resolve stage-specific Conda prefixes without changing scientific code."""

    core_prefix: Path | None = None
    qdnaseq_prefix: Path | None = None
    ichorcna_prefix: Path | None = None
    classifier_prefix: Path | None = None
    gistic_prefix: Path | None = None

    @classmethod
    def from_environment(cls) -> "Toolchain":
        def resolved(name: str) -> Path | None:
            value = os.environ.get(name)
            return Path(value).expanduser().resolve() if value else None

        return cls(
            core_prefix=resolved("ONCOTRACER_CORE_PREFIX"),
            qdnaseq_prefix=resolved("ONCOTRACER_QDNASEQ_PREFIX"),
            ichorcna_prefix=resolved("ONCOTRACER_ICHORCNA_PREFIX"),
            classifier_prefix=resolved("ONCOTRACER_CLASSIFIER_PREFIX"),
            gistic_prefix=resolved("ONCOTRACER_GISTIC_PREFIX"),
        )

    def _prefix(self, group: str) -> Path | None:
        if group == "core":
            return self.core_prefix
        if group == "qdnaseq":
            return self.qdnaseq_prefix
        if group == "ichorcna":
            return self.ichorcna_prefix
        if group == "classifier":
            return self.classifier_prefix
        if group == "gistic":
            return self.gistic_prefix
        raise OncoTracerError(f"unknown toolchain group: {group}")

    def executable(self, group: str, name: str) -> str:
        """Return the exact executable for a native environment group.

        A configured prefix is authoritative. In particular, do not invoke a
        basename through conda run: an independently prepended environment can
        remain ahead of the requested prefix on PATH and select the wrong R or
        Python runtime.
        """
        if not name or Path(name).name != name:
            raise OncoTracerError(f"tool name must be a basename, found: {name!r}")
        prefix = self._prefix(group)
        if prefix is None:
            return require_command(name)
        prefix = prefix.expanduser().resolve()
        if not prefix.is_dir():
            raise OncoTracerError(f"configured {group} Conda prefix does not exist: {prefix}")
        executable = prefix / "bin" / name
        if not executable.is_file() or not os.access(executable, os.X_OK):
            raise OncoTracerError(
                f"configured {group} Conda prefix is missing executable {name!r}: {executable}"
            )
        return str(executable)

    def wrap(self, group: str, command: Sequence[str | Path]) -> list[str]:
        argv = [str(item) for item in command]
        if not argv:
            raise OncoTracerError(f"empty command for toolchain group: {group}")
        return [self.executable(group, argv[0]), *argv[1:]]

    def environment(self, group: str) -> dict[str, str]:
        """Return the minimal exact-prefix environment required by a tool.

        Most native executables are relocatable and need no activation state.
        The pinned GISTIC package is an exception: its launcher depends on the
        MATLAB Compiler Runtime paths normally installed by a Conda activation
        hook. Resolve those paths from the configured GISTIC prefix instead of
        sourcing a shell or inheriting an unrelated environment.
        """
        prefix = self._prefix(group)
        if prefix is None:
            return {}
        prefix = prefix.expanduser().resolve()
        if not prefix.is_dir():
            raise OncoTracerError(f"configured {group} Conda prefix does not exist: {prefix}")
        if group != "gistic":
            return {}

        roots = sorted(
            candidate
            for candidate in (prefix / "share").glob("mcr-*/v*")
            if candidate.is_dir()
        )
        valid: list[tuple[Path, list[Path]]] = []
        for root in roots:
            libraries = [
                root / "runtime" / "glnxa64",
                root / "bin" / "glnxa64",
                root / "sys" / "os" / "glnxa64",
            ]
            if all(path.is_dir() for path in libraries):
                valid.append((root, libraries))
        if len(valid) != 1:
            found = ", ".join(str(root) for root, _ in valid) or "none"
            raise OncoTracerError(
                "configured GISTIC prefix must contain exactly one usable MATLAB "
                f"Compiler Runtime under share/mcr-*/v*; found: {found}"
            )
        _, libraries = valid[0]
        return {
            "LD_LIBRARY_PATH": os.pathsep.join(str(path) for path in libraries),
            "LD_LIBRARY_PATH_MCR": "",
        }

    def rscript(self, group: str, command: Sequence[str | Path]) -> list[str]:
        """Run the group's exact Rscript with inherited R routing removed."""
        return [
            require_command("env"),
            "-u", "R_HOME",
            "-u", "R_LIBS",
            "-u", "R_LIBS_USER",
            "-u", "R_LIBS_SITE",
            self.executable(group, "Rscript"),
            "--vanilla",
            *[str(item) for item in command],
        ]


def _safe_sample(value: str) -> str:
    sample = value.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", sample):
        raise OncoTracerError(
            f"invalid sample ID {value!r}; use letters, digits, dot, underscore, or dash"
        )
    return sample


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _as_int(value: object, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError) as error:
        raise OncoTracerError(f"expected integer, found {value!r}") from error


def parse_illumina_samplesheet(path: Path) -> list[IlluminaSample]:
    path = require_file(path, "Illumina samplesheet")
    rows: list[IlluminaSample] = []
    seen: set[str] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"sample", "fastq_1", "fastq_2", "status"}
        missing = required.difference(reader.fieldnames or [])
        if missing:
            raise OncoTracerError(f"Illumina samplesheet is missing: {', '.join(sorted(missing))}")
        for raw in reader:
            sample = _safe_sample(raw.get("sample", ""))
            if sample in seen:
                raise OncoTracerError(f"duplicate sample ID: {sample}")
            seen.add(sample)
            fq1 = require_file(Path(raw.get("fastq_1", "")), f"FASTQ 1 for {sample}")
            fq2_text = (raw.get("fastq_2") or "").strip()
            fq2 = require_file(Path(fq2_text), f"FASTQ 2 for {sample}") if fq2_text else None
            status = (raw.get("status") or "tumor").strip().lower()
            if status not in {"tumor", "normal"}:
                raise OncoTracerError(f"status for {sample} must be tumor or normal")
            rows.append(IlluminaSample(sample, fq1, fq2, status))
    if not rows:
        raise OncoTracerError("Illumina samplesheet has no data rows")
    layouts = {sample.fastq_2 is not None for sample in rows}
    if len(layouts) != 1:
        raise OncoTracerError("a native Illumina run cannot mix single-end and paired-end libraries")
    normals = [row for row in rows if row.status == "normal"]
    tumors = [row for row in rows if row.status == "tumor"]
    if not tumors:
        raise OncoTracerError("Illumina analysis requires at least one tumor")
    if len(normals) == 1:
        raise OncoTracerError("exactly one normal is not valid; use zero or at least two normals")
    return rows


def _resolve_fastq_pass(folder: Path) -> Path:
    folder = require_directory(folder, "ONT folder")
    if folder.name == "fastq_pass":
        return folder
    if (folder / "fastq_pass").is_dir():
        return (folder / "fastq_pass").resolve()
    if any(path.is_dir() for path in folder.glob("barcode*")):
        return folder
    candidates = sorted(path for path in folder.rglob("fastq_pass") if path.is_dir())
    if len(candidates) != 1:
        raise OncoTracerError(
            f"expected exactly one fastq_pass under {folder}; found {len(candidates)}"
        )
    return candidates[0].resolve()


def parse_ont_samples(config: Mapping[str, object]) -> list[OntSample]:
    folder_value = config.get("ont_folder")
    barcodes_value = config.get("ont_barcodes")
    if not folder_value or not barcodes_value:
        raise OncoTracerError("ONT config requires ont_folder and ont_barcodes")
    root = _resolve_fastq_pass(Path(str(folder_value)))
    barcodes = [token.strip() for token in str(barcodes_value).replace(";", ",").split(",") if token.strip()]
    names_value = config.get("ont_sample_names")
    names = (
        [token.strip() for token in str(names_value).replace(";", ",").split(",") if token.strip()]
        if names_value
        else barcodes
    )
    if len(names) != len(barcodes):
        raise OncoTracerError("ont_sample_names must contain one name per barcode")
    samples: list[OntSample] = []
    for barcode, name in zip(barcodes, names, strict=True):
        candidate = root / barcode
        if not candidate.is_dir() and barcode.isdigit():
            candidate = root / f"barcode{int(barcode):02d}"
        if not candidate.is_dir():
            match = re.fullmatch(r"barcode0*(\d+)", barcode, flags=re.I)
            if match:
                candidate = root / f"barcode{int(match.group(1)):02d}"
        if not candidate.is_dir():
            raise OncoTracerError(f"ONT barcode directory not found: {root / barcode}")
        samples.append(OntSample(_safe_sample(name), candidate.name, candidate.resolve()))
    if len({sample.sample for sample in samples}) != len(samples):
        raise OncoTracerError("ONT sample names must be unique")
    return samples


def prepare_reference(
    lpwgs_root: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    need_bwa: bool,
    need_minimap2: bool,
    threads: int,
) -> dict[str, Path]:
    ref_dir = lpwgs_root / "references" / "samurai_hg38"
    ref_dir.mkdir(parents=True, exist_ok=True)
    fasta = ref_dir / "genome.fa"
    fai = ref_dir / "genome.fa.fai"
    sequence_dict = ref_dir / "genome.dict"
    if not fasta.is_file() or fasta.stat().st_size == 0:
        download(f"{HG38_BASE}/genome.fa", fasta)
    if not fai.is_file() or fai.stat().st_size == 0:
        try:
            download(f"{HG38_BASE}/genome.fa.fai", fai)
        except OncoTracerError:
            runner.run("reference-faidx", [toolchain.executable("core", "samtools"), "faidx", fasta])
    if not sequence_dict.is_file() or sequence_dict.stat().st_size == 0:
        try:
            download(f"{HG38_BASE}/genome.dict", sequence_dict)
        except OncoTracerError:
            with sequence_dict.open("w", encoding="utf-8") as output:
                runner.run(
                    "reference-dict",
                    [toolchain.executable("core", "samtools"), "dict", fasta],
                    stdout=output,
                )
    first = ""
    with fasta.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith(">"):
                first = line[1:].split()[0]
                break
    if not first.startswith("chr"):
        raise OncoTracerError(f"hg38 reference must use UCSC chr names; first contig is {first!r}")

    bwa_prefix = ref_dir / "bwa" / "genome"
    if need_bwa:
        required = [Path(str(bwa_prefix) + suffix) for suffix in (".amb", ".ann", ".bwt", ".pac", ".sa")]
        command = [toolchain.executable("core", "bwa"), "index", "-p", str(bwa_prefix), str(fasta)]
        signature = ledger.signature("reference-bwa", command, [fasta])
        if not ledger.reusable("reference-bwa", signature, required):
            bwa_prefix.parent.mkdir(parents=True, exist_ok=True)
            runner.run("reference-bwa", command)
            if not all(path.is_file() and path.stat().st_size > 0 for path in required):
                raise OncoTracerError("BWA index did not produce all required files")
            ledger.complete("reference-bwa", signature, required)

    minimap_index = Path(str(fasta) + ".map-ont.mmi")
    if need_minimap2:
        command = [toolchain.executable("core", "minimap2"), "-x", "map-ont", "-d", minimap_index, fasta]
        signature = ledger.signature("reference-minimap2", [str(x) for x in command], [fasta])
        if not ledger.reusable("reference-minimap2", signature, [minimap_index]):
            runner.run("reference-minimap2", command)
            ledger.complete("reference-minimap2", signature, [minimap_index])

    return {
        "fasta": fasta,
        "fai": fai,
        "dict": sequence_dict,
        "bwa_prefix": bwa_prefix,
        "minimap2_index": minimap_index,
    }


def prepare_qdnaseq_annotation(
    root: Path,
    lpwgs_root: Path,
    binsize: int,
    runner: CommandRunner,
    toolchain: Toolchain,
) -> Path:
    helper = require_file(root / "bin" / "scripts" / "prepare_qdnaseq_bin_data.sh", "qDNAseq annotation helper")
    cache = lpwgs_root / ".oncotracer" / "qdnaseq-bin-data"
    command = [
        toolchain.executable("qdnaseq", "bash"),
        str(helper),
        "--rscript",
        toolchain.executable("qdnaseq", "Rscript"),
        "--binsize",
        str(binsize),
        "--cache-dir",
        str(cache),
    ]
    # The helper prints the absolute RDS path as its final line. Capture without shell.
    if runner.dry_run:
        return cache / f"QDNAseq.hg38.{binsize}kbp.SR50.rds"
    import subprocess

    started = utc_now()
    print(f"[qdnaseq-annotation] {' '.join(map(str, command))}", file=sys.stderr)
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    runner._record(
        "qdnaseq-annotation", started, utc_now(), completed.returncode, root, command
    )
    if completed.returncode != 0:
        sys.stderr.write(completed.stdout)
        sys.stderr.write(completed.stderr)
        raise OncoTracerError("qDNAseq annotation preparation failed")
    output_lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if not output_lines:
        raise OncoTracerError("qDNAseq annotation helper returned no path")
    return require_file(Path(output_lines[-1]), "qDNAseq hg38 annotation")


def _write_bam_sheet(samples: Iterable[IlluminaSample], bams: Mapping[str, Path], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["sample", "bam", "status"])
        writer.writeheader()
        for sample in samples:
            writer.writerow(
                {"sample": sample.sample, "bam": str(bams[sample.sample]), "status": sample.status}
            )


def align_illumina(
    samples: list[IlluminaSample],
    reference: Mapping[str, Path],
    outdir: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    threads: int,
    force: bool,
) -> dict[str, Path]:
    alignment = outdir / "alignment"
    markduplicates = outdir / "markduplicates"
    alignment.mkdir(parents=True, exist_ok=True)
    markduplicates.mkdir(parents=True, exist_ok=True)
    results: dict[str, Path] = {}
    for sample in samples:
        bam = alignment / f"{sample.sample}.bam"
        bai = Path(str(bam) + ".bai")
        markdup = markduplicates / f"{sample.sample}_markdup.bam"
        markdup_bai = Path(str(markdup) + ".bai")
        metrics = markduplicates / f"{sample.sample}_markdup.metrics.txt"
        reads = [sample.fastq_1] + ([sample.fastq_2] if sample.fastq_2 else [])
        rg = (
            f"@RG\\tID:{sample.sample}\\tPU:1\\tSM:{sample.sample}"
            f"\\tLB:{sample.sample}\\tPL:Illumina"
        )
        bwa = [
            toolchain.executable("core", "bwa"),
            "mem",
            "-t",
            str(threads),
            "-R",
            rg,
            str(reference["bwa_prefix"]),
            *[str(path) for path in reads],
        ]
        sort = [
            toolchain.executable("core", "samtools"),
            "sort",
            "-@",
            str(max(1, threads // 2)),
            "-o",
            str(bam),
            "-",
        ]
        signature = ledger.signature(f"illumina-align-{sample.sample}", bwa + ["|"] + sort, reads)
        if force or not ledger.reusable(
            f"illumina-align-{sample.sample}", signature, [bam, bai]
        ):
            runner.pipeline(f"illumina-align-{sample.sample}", bwa, sort)
            runner.run(
                f"illumina-index-{sample.sample}",
                [toolchain.executable("core", "samtools"), "index", "-@", str(max(1, threads // 2)), bam],
            )
            ledger.complete(f"illumina-align-{sample.sample}", signature, [bam, bai])

        picard = toolchain.executable("core", "picard")
        if picard:
            mark_command = [
                picard,
                "MarkDuplicates",
                f"I={bam}",
                f"O={markdup}",
                f"M={metrics}",
                "CREATE_INDEX=true",
                "VALIDATION_STRINGENCY=SILENT",
            ]
            mark_signature = ledger.signature(
                f"illumina-markdup-{sample.sample}", mark_command, [bam, bai]
            )
            if force or not ledger.reusable(
                f"illumina-markdup-{sample.sample}", mark_signature, [markdup, markdup_bai]
            ):
                runner.run(f"illumina-markdup-{sample.sample}", mark_command)
                if not markdup_bai.is_file():
                    runner.run(
                        f"illumina-markdup-index-{sample.sample}",
                        [toolchain.executable("core", "samtools"), "index", markdup],
                    )
                ledger.complete(
                    f"illumina-markdup-{sample.sample}", mark_signature, [markdup, markdup_bai]
                )
        else:
            raise OncoTracerError(
                "Picard MarkDuplicates is required for native Illumina analysis; "
                "run 'oncotracer install --conda' or use the maintained container backend"
            )
        results[sample.sample] = markdup
    return results


def run_qdnaseq(
    root: Path,
    lpwgs_root: Path,
    samples: list[IlluminaSample],
    markdup_bams: Mapping[str, Path],
    samurai_outdir: Path,
    binsize: int,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    force: bool,
) -> tuple[Path, Path]:
    normals = [sample for sample in samples if sample.status == "normal"]
    bam_sheet = samurai_outdir / "input" / "native.bam.samplesheet.csv"
    _write_bam_sheet(samples, markdup_bams, bam_sheet)
    annotation = prepare_qdnaseq_annotation(root, lpwgs_root, binsize, runner, toolchain)
    paired = all(sample.fastq_2 is not None for sample in samples)

    if normals:
        qdna_out = samurai_outdir / "qdnaseq_local_pon"
        script = require_file(
            root / "bin" / "scripts" / "native_qdnaseq_pon.R",
            "native qDNAseq local-PoN R script",
        )
        command = toolchain.rscript(
            "qdnaseq",
            [
                script,
                "--samplesheet",
                bam_sheet,
                "--outdir",
                qdna_out,
                "--binsize",
                str(binsize),
                "--min-mapq",
                "37",
                "--min-normals",
                str(len(normals)),
                "--paired-ends",
                str(paired).lower(),
                "--pon-name",
                "illumina_local_PoN",
                "--bin-data",
                annotation,
            ],
        )
        output = qdna_out / "all_segments.seg"
        signature = ledger.signature(
            "qdnaseq-local-pon", command, [bam_sheet, annotation, *markdup_bams.values()]
        )
        if force or not ledger.reusable("qdnaseq-local-pon", signature, [output]):
            runner.run("qdnaseq-local-pon", command, cwd=root)
            ledger.complete("qdnaseq-local-pon", signature, [output])
        # Keep the legacy refinement input path coherent.
        pon_alignment = samurai_outdir / "pon_alignment"
        pon_alignment.mkdir(parents=True, exist_ok=True)
        for sample, bam in markdup_bams.items():
            for source in (bam, Path(str(bam) + ".bai")):
                target = pon_alignment / source.name
                if target.exists() or target.is_symlink():
                    target.unlink()
                target.symlink_to(source.resolve())
        return qdna_out, pon_alignment

    qdna_out = samurai_outdir / "qdnaseq"
    script = require_file(root / "bin" / "scripts" / "native_qdnaseq.R", "native qDNAseq R script")
    command = toolchain.rscript(
        "qdnaseq",
        [
            script,
            "--samplesheet",
            bam_sheet,
            "--outdir",
            qdna_out,
            "--binsize",
            str(binsize),
            "--min-mapq",
            "37",
            "--paired-ends",
            str(paired).lower(),
            "--bin-data",
            annotation,
        ],
    )
    output = qdna_out / "all_segments.seg"
    signature = ledger.signature("qdnaseq", command, [bam_sheet, annotation, *markdup_bams.values()])
    if force or not ledger.reusable("qdnaseq", signature, [output]):
        runner.run("qdnaseq", command, cwd=root)
        ledger.complete("qdnaseq", signature, [output])
    return qdna_out, samurai_outdir / "alignment"


def _fastq_files(directory: Path, min_age_minutes: int) -> list[Path]:
    import time

    now = time.time()
    files: list[Path] = []
    for path in sorted(directory.rglob("*")):
        if not path.is_file() or path.name.endswith(".tmp"):
            continue
        lowered = path.name.lower()
        if not lowered.endswith((".fastq", ".fq", ".fastq.gz", ".fq.gz")):
            continue
        if path.stat().st_size == 0:
            continue
        if now - path.stat().st_mtime < min_age_minutes * 60:
            continue
        files.append(path)
    if not files:
        raise OncoTracerError(f"no stable FASTQ files found under {directory}")
    return files


def merge_fastqs(files: list[Path], destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.tmp.{os.getpid()}")
    with gzip.open(temporary, "wb", compresslevel=6) as output:
        for path in files:
            opener = gzip.open if path.name.lower().endswith(".gz") else open
            with opener(path, "rb") as source:  # type: ignore[arg-type]
                shutil.copyfileobj(source, output, length=8 * 1024 * 1024)
    os.replace(temporary, destination)


def align_ont(
    samples: list[OntSample],
    reference: Mapping[str, Path],
    samurai_outdir: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    threads: int,
    min_age_minutes: int,
    force: bool,
) -> dict[str, Path]:
    merged_dir = samurai_outdir / "merged_fastq"
    bam_dir = samurai_outdir / "bam"
    merged_dir.mkdir(parents=True, exist_ok=True)
    bam_dir.mkdir(parents=True, exist_ok=True)
    bams: dict[str, Path] = {}
    for sample in samples:
        files = _fastq_files(sample.fastq_dir, min_age_minutes)
        merged = merged_dir / f"{sample.sample}.fastq.gz"
        merge_signature = ledger.signature(
            f"ont-merge-{sample.sample}", ["native-gzip-merge", str(merged)], files
        )
        if force or not ledger.reusable(f"ont-merge-{sample.sample}", merge_signature, [merged]):
            if not runner.dry_run:
                merge_fastqs(files, merged)
            ledger.complete(f"ont-merge-{sample.sample}", merge_signature, [merged])
        bam = bam_dir / f"{sample.sample}.sorted.bam"
        bai = Path(str(bam) + ".bai")
        left = [
            toolchain.executable("core", "minimap2"),
            "-ax",
            "map-ont",
            "-t",
            str(threads),
            "-R",
            f"@RG\\tID:{sample.sample}\\tSM:{sample.sample}\\tPL:ONT",
            reference["minimap2_index"],
            merged,
        ]
        right = [
            toolchain.executable("core", "samtools"),
            "sort",
            "-@",
            str(max(1, threads // 2)),
            "-o",
            bam,
            "-",
        ]
        signature = ledger.signature(f"ont-align-{sample.sample}", [*map(str, left), "|", *map(str, right)], [merged])
        if force or not ledger.reusable(f"ont-align-{sample.sample}", signature, [bam, bai]):
            runner.pipeline(f"ont-align-{sample.sample}", left, right)
            runner.run(
                f"ont-index-{sample.sample}",
                [toolchain.executable("core", "samtools"), "index", "-@", str(max(1, threads // 2)), bam],
            )
            ledger.complete(f"ont-align-{sample.sample}", signature, [bam, bai])
        bams[sample.sample] = bam
    return bams


def prepare_ichor_assets(lpwgs_root: Path, binsize: int) -> dict[str, Path]:
    if binsize != 500:
        raise OncoTracerError(
            "native automatic ichorCNA assets are pinned to hg38/500 kb; use ont_binsize_kb: 500"
        )
    directory = lpwgs_root / "references" / "samurai_ichorcna_hg38_500kb"
    directory.mkdir(parents=True, exist_ok=True)
    resolved: dict[str, Path] = {}
    for key, filename in ICHOR_ASSETS.items():
        resolved[key] = download(f"{ICHOR_ASSET_BASE}/{filename}", directory / filename)
    return resolved


def _parse_ichor_params(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            values[key.strip()] = value.strip()
    return values


def _combine_headered(paths: list[Path], destination: Path) -> None:
    if not paths:
        raise OncoTracerError(f"no source files for {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as output:
        expected_header: str | None = None
        for index, path in enumerate(paths):
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            if not lines:
                raise OncoTracerError(f"empty ichorCNA segment file: {path}")
            if expected_header is None:
                expected_header = lines[0]
                output.write(expected_header + "\n")
            elif lines[0] != expected_header:
                raise OncoTracerError(f"ichorCNA segment headers differ: {path}")
            for line in lines[1:]:
                if line.strip():
                    output.write(line + "\n")


def _correct_ichor_segments(seg_path: Path, summary_path: Path, destination: Path) -> None:
    with summary_path.open(newline="", encoding="utf-8") as handle:
        summary_rows = list(csv.DictReader(handle, delimiter="\t"))
    ploidy: dict[str, float] = {}
    for row in summary_rows:
        sample = row.get("samplename") or row.get("sample") or row.get("index")
        value = row.get("Ploidy") or row.get("ploidy")
        if sample and value:
            ploidy[sample] = float(value)
    with seg_path.open(newline="", encoding="utf-8") as source:
        reader = csv.DictReader(source, delimiter="\t")
        fieldnames = reader.fieldnames or []
        required = {"ID", "chrom", "start", "end", "num.mark", "logR_Copy_Number"}
        missing = required.difference(fieldnames)
        if missing:
            raise OncoTracerError(f"ichorCNA SEG is missing columns: {', '.join(sorted(missing))}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", newline="", encoding="utf-8") as sink:
            writer = csv.writer(sink, delimiter="\t")
            writer.writerow(["sample", "chromosome", "start", "end", "num.mark", "adj.seg"])
            for row in reader:
                sample = row["ID"]
                if sample not in ploidy:
                    raise OncoTracerError(f"ploidy is missing for ichorCNA sample: {sample}")
                ratio = max(float(row["logR_Copy_Number"]) / ploidy[sample], 2e-8)
                adjusted = max(math.log2(ratio), -0.5)
                writer.writerow(
                    [sample, row["chrom"], row["start"], row["end"], row["num.mark"], adjusted]
                )


def run_ichorcna(
    root: Path,
    lpwgs_root: Path,
    samples: list[OntSample],
    bams: Mapping[str, Path],
    samurai_outdir: Path,
    binsize: int,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: Toolchain,
    *,
    threads: int,
    force: bool,
) -> Path:
    assets = prepare_ichor_assets(lpwgs_root, binsize)
    ichor_out = samurai_outdir / "results" / "ichorcna"
    wig_dir = ichor_out / "wigfiles_samples"
    ichor_out.mkdir(parents=True, exist_ok=True)
    wig_dir.mkdir(parents=True, exist_ok=True)
    params_files: list[Path] = []
    seg_files: list[Path] = []
    script = require_file(root / "bin" / "scripts" / "native_ichorcna.R", "native ichorCNA R script")
    chromosomes = ",".join(f"chr{index}" for index in range(1, 23))
    for sample in samples:
        bam = bams[sample.sample]
        wig = wig_dir / f"{sample.sample}.wig"
        readcounter = toolchain.wrap(
            "ichorcna",
            [
                "readCounter",
                "--chromosome",
                chromosomes,
                "--quality",
                "20",
                "--window",
                str(binsize * 1000),
                bam,
            ],
        )
        signature = ledger.signature(f"ichor-readcounter-{sample.sample}", readcounter, [bam])
        if force or not ledger.reusable(f"ichor-readcounter-{sample.sample}", signature, [wig]):
            with wig.open("w", encoding="utf-8") as output:
                runner.run(f"ichor-readcounter-{sample.sample}", readcounter, stdout=output)
            ledger.complete(f"ichor-readcounter-{sample.sample}", signature, [wig])

        sample_out = ichor_out / sample.sample
        sample_out.mkdir(parents=True, exist_ok=True)
        command = toolchain.rscript(
            "ichorcna",
            [
                script,
                "--wig",
                wig,
                "--sample",
                sample.sample,
                "--outdir",
                sample_out,
                "--gc-wig",
                assets["gc"],
                "--map-wig",
                assets["map"],
                "--centromere",
                assets["centromere"],
                "--reptime",
                assets["reptime"],
                "--normal-panel",
                assets["pon"],
                "--cores",
                str(max(1, min(threads, 8))),
            ],
        )
        expected_params = sample_out / f"{sample.sample}.params.txt"
        expected_seg = sample_out / f"{sample.sample}.seg.txt"
        signature = ledger.signature(f"ichorcna-{sample.sample}", command, [wig, *assets.values()])
        if force or not ledger.reusable(
            f"ichorcna-{sample.sample}", signature, [expected_params, expected_seg]
        ):
            runner.run(f"ichorcna-{sample.sample}", command, cwd=sample_out)
            if not expected_params.is_file():
                matches = sorted(sample_out.rglob(f"{sample.sample}.params.txt"))
                if matches:
                    expected_params = matches[0]
            if not expected_seg.is_file():
                matches = sorted(sample_out.rglob(f"{sample.sample}.seg.txt"))
                if matches:
                    expected_seg = matches[0]
            ledger.complete(f"ichorcna-{sample.sample}", signature, [expected_params, expected_seg])
        params_files.append(require_file(expected_params, f"ichorCNA params for {sample.sample}"))
        seg_files.append(require_file(expected_seg, f"ichorCNA SEG for {sample.sample}"))

        # Publish the files where the legacy refinement script expects them.
        for path in sample_out.rglob("*"):
            if path.is_file() and path.name.startswith(sample.sample):
                target = ichor_out / path.name
                if path.resolve() != target.resolve():
                    shutil.copy2(path, target)

    combined_seg = ichor_out / "all_segments_ichorcna_gistic.seg"
    _combine_headered(seg_files, combined_seg)
    summary = ichor_out / "ichorcna_summary_mqc.txt"
    with summary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t")
        writer.writerow(["samplename", "Tumor Fraction", "Ploidy", "GC-Map correction MAD"])
        for sample, params in zip(samples, params_files, strict=True):
            values = _parse_ichor_params(params)
            writer.writerow(
                [
                    sample.sample,
                    values.get("Tumor Fraction", "NA"),
                    values.get("Ploidy", "NA"),
                    values.get("GC-Map correction MAD", "NA"),
                ]
            )
    corrected = ichor_out / "segments_logR_corrected_gistic.seg"
    _correct_ichor_segments(combined_seg, summary, corrected)
    return ichor_out


def run_refinement_and_outputs(
    root: Path,
    config: Mapping[str, object],
    mode: str,
    samurai_outdir: Path,
    qdna_or_ichor_dir: Path,
    bam_dir: Path,
    outdir: Path,
    lpwgs_root: Path,
    runner: CommandRunner,
    toolchain: Toolchain,
    *,
    force: bool,
) -> None:
    refine_script = require_file(
        root / "bin" / "scripts" / "bam_cnv_boundary_refine.sh",
        "BAM boundary-refinement script",
    )
    refine_out = outdir / "02_bam_refinement"
    refine_out.mkdir(parents=True, exist_ok=True)
    if mode == "illumina":
        binsize = _as_int(config.get("illumina_binsize_kb"), 100)
        dataset = f"illumina_qdnaseq_{binsize}kb"
        prior = qdna_or_ichor_dir / "all_segments.seg"
        mode_args = [
            "--mode",
            "illumina",
            "--illumina-qdnaseq-dir",
            qdna_or_ichor_dir,
            "--illumina-bam-dir",
            bam_dir,
            "--illumina-prior-seg",
            prior,
            "--illumina-binsize-kb",
            str(binsize),
            "--fine-bin-kb-illumina",
            str(_as_int(config.get("fine_bin_kb_illumina"), 10)),
            "--coverage-mode-illumina",
            "starts",
            "--normal-samples",
            "auto" if "qdnaseq_local_pon" in str(qdna_or_ichor_dir) else "none",
            "--pon-mode",
            "on" if "qdnaseq_local_pon" in str(qdna_or_ichor_dir) else "off",
            "--min-local-log2-diff",
            str(config.get("min_local_log2_diff_illumina", 0.10)),
        ]
    else:
        binsize = _as_int(config.get("ont_binsize_kb"), 500)
        dataset = f"ONT_ichorcna_{binsize}kb"
        prior = qdna_or_ichor_dir / "segments_logR_corrected_gistic.seg"
        mode_args = [
            "--mode",
            "ont",
            "--ont-ichor-dir",
            qdna_or_ichor_dir,
            "--ont-bam-dir",
            bam_dir,
            "--ont-prior-seg",
            prior,
            "--ont-binsize-kb",
            str(binsize),
            "--fine-bin-kb-ont",
            str(_as_int(config.get("fine_bin_kb_ont"), 25)),
            "--coverage-mode-ont",
            "bases",
            "--normal-samples",
            "auto",
            "--pon-mode",
            "auto",
            "--min-local-log2-diff",
            str(config.get("min_local_log2_diff_ont", 0.12)),
        ]
    command: list[str | Path] = [
        require_command("bash"),
        refine_script,
        *mode_args,
        "--lpwgs-root",
        lpwgs_root,
        "--outdir",
        refine_out,
        "--search-radius-bins",
        str(_as_int(config.get("search_radius_bins"), 2)),
        "--min-mapq",
        str(_as_int(config.get("min_mapq"), 20)),
        "--min-adjacent-seg-delta",
        str(config.get("min_adjacent_seg_delta", 0.10)),
        "--min-bic-gain",
        str(config.get("min_bic_gain", 6)),
        "--permutations",
        str(_as_int(config.get("permutations"), 300)),
        "--permutation-p",
        str(config.get("permutation_p", 0.05)),
        "--accept-rule",
        str(config.get("accept_rule", "p_and_bic")),
        "--max-ci-fraction-of-coarse",
        str(config.get("max_ci_fraction_of_coarse", 1.0)),
        "--zipcnv-mode",
        str(config.get("zipcnv_mode", "adapted")),
        "--zipcnv-window-bins",
        str(_as_int(config.get("zipcnv_window_bins"), 5)),
        "--zipcnv-k",
        str(config.get("zipcnv_k", 0.05)),
        "--zipcnv-min-segment-bins",
        str(_as_int(config.get("zipcnv_min_segment_bins"), 3)),
        "--zipcnv-min-abs-log2",
        str(config.get("zipcnv_min_abs_log2", 0.25)),
        "--zipcnv-compare-min-overlap",
        str(config.get("zipcnv_compare_min_overlap", 0.50)),
        "--native-current-environment",
        "--python-executable",
        toolchain.executable("core", "python"),
        "--samtools-executable",
        toolchain.executable("core", "samtools"),
        "--skip-install",
    ]
    if force:
        command.append("--force")
    runner.run("bam-refinement", command, cwd=root)
    final_root = refine_out / dataset / "04_final_results"
    require_file(final_root / "final_segments.tsv", "refined final segments")
    bins_input = require_directory(
        final_root / "cna_cytogenomic_input" / "qdnaseq_bins",
        "CNA codification bins",
    )

    codify_out = outdir / "03_cna_codification"
    codify_out.mkdir(parents=True, exist_ok=True)
    codify = require_file(
        root / "bin" / "cna_codification" / "scripts" / "cna_to_cytogenomic_notation.py",
        "CNA codification script",
    )
    cytoband = require_file(
        root / "bin" / "cna_codification" / "resources" / "hg38.cytoBand.txt.gz",
        "hg38 cytoband resource",
    )
    runner.run(
        "cna-codification",
        toolchain.wrap(
            "core",
            [
                "python",
                codify,
            "--qdnaseq",
            "--input-dir",
            bins_input,
            "--cytoband",
            cytoband,
            "--outdir",
            codify_out,
            "--prefix",
                "cna",
            ],
        ),
        cwd=root,
    )
    events = require_file(codify_out / "cna_events.tsv", "CNA events")
    require_file(codify_out / "cna_cytogenomic_notation.tsv", "cytogenomic notation")

    plots_out = outdir / "04_cna_custom_plots"
    plots_out.mkdir(parents=True, exist_ok=True)
    plotter = require_file(
        root / "bin" / "cna_codification" / "scripts" / "plot_cna_events.py",
        "CNA plotting script",
    )
    bins_table = require_file(
        refine_out / dataset / "01_tables" / "refined_bins.tsv.gz", "refined bins"
    )
    runner.run(
        "cna-plots",
        toolchain.wrap(
            "core",
            [
                "python",
                plotter,
            "--events",
            events,
            "--cytoband",
            cytoband,
            "--outdir",
            plots_out,
            "--bins",
            bins_table,
            "--profile-sample",
                "all",
            ],
        ),
        cwd=root,
    )
    require_file(plots_out / "cna_per_sample_pages.pdf", "per-sample CNA PDF")
    require_file(
        plots_out / "cna_log2_ratio_profiles_all_samples.pdf", "cohort CNA profile PDF"
    )

    summary_dir = outdir / "06_workflow_summary"
    summary_dir.mkdir(parents=True, exist_ok=True)
    summary = {
        "oncotracer_version": __version__,
        "engine": "native",
        "nextflow_used": False,
        "mode": mode,
        "dataset": dataset,
        "outdir": str(outdir),
        "bam_refinement": str(refine_out / dataset),
        "cna_codification": str(codify_out),
        "cna_events": str(events),
        "cna_custom_plots": str(plots_out),
        "cna_notation": str(codify_out / "cna_cytogenomic_notation.tsv"),
        "completed_at": utc_now(),
    }
    atomic_write_json(summary_dir / "workflow_summary.json", summary)
    atomic_write_text(
        summary_dir / "workflow_summary.txt",
        "\n".join(f"{key}={value}" for key, value in summary.items()) + "\n",
    )


def write_run_manifest(outdir: Path, config_path: Path, trace_path: Path) -> None:
    files: list[dict[str, object]] = []
    for relative in [
        "06_workflow_summary/workflow_summary.txt",
        "06_workflow_summary/workflow_summary.json",
        "03_cna_codification/cna_events.tsv",
        "03_cna_codification/cna_cytogenomic_notation.tsv",
        "04_cna_custom_plots/cna_per_sample_pages.pdf",
        "04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf",
        "05_cna_classifier/native_classifier_summary.json",
        "05_cna_classifier/02_classification/cna_patient_classification.tsv",
        "05_cna_classifier/03_report/cna_classifier_report.html",
    ]:
        path = outdir / relative
        if path.is_file():
            files.append(
                {
                    "path": relative,
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            )
    manifest = {
        "schema": "oncotracer-native-run-manifest-v1",
        "oncotracer_version": __version__,
        "engine": "native",
        "nextflow_used": False,
        "config": str(config_path),
        "config_sha256": sha256_file(config_path),
        "trace": str(trace_path),
        "created_at": utc_now(),
        "files": files,
    }
    atomic_write_json(outdir / "06_workflow_summary" / "native_run_manifest.json", manifest)


def run_native(
    config_path: Path,
    *,
    root: Path | None = None,
    threads: int | None = None,
    force: bool | None = None,
    dry_run: bool = False,
) -> Path:
    root = runtime_root(root)
    config_path = require_file(config_path, "OncoTracer YAML config")
    config = load_flat_yaml(config_path)
    mode = str(config.get("mode") or "").strip().lower()
    if mode not in {"illumina", "ont"}:
        raise OncoTracerError("config mode must be illumina or ont")
    lpwgs_root = Path(str(config.get("lpwgs_root") or root / "project")).expanduser().resolve()
    outdir_value = config.get("outdir")
    if not outdir_value:
        raise OncoTracerError("config requires outdir")
    outdir = Path(str(outdir_value)).expanduser().resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    native_dir = outdir / ".oncotracer-native"
    native_dir.mkdir(parents=True, exist_ok=True)
    trace = native_dir / "trace.tsv"
    runner = CommandRunner(trace, dry_run=dry_run)
    ledger = StageLedger(native_dir / "state.json")
    toolchain = Toolchain.from_environment()
    cpu = threads or max(1, min(os.cpu_count() or 1, 16))
    force_run = _as_bool(config.get("force"), False) if force is None else force

    # The native trace is an explicit release invariant.
    atomic_write_text(native_dir / "engine.txt", f"engine=native\nnextflow_used=false\nversion={__version__}\n")

    if mode == "illumina":
        samplesheet_value = config.get("illumina_samplesheet")
        if not samplesheet_value:
            raise OncoTracerError("Illumina config requires illumina_samplesheet")
        samples = parse_illumina_samplesheet(Path(str(samplesheet_value)))
        samurai_out = outdir / "01_samurai_illumina"
        samurai_out.mkdir(parents=True, exist_ok=True)
        reference = prepare_reference(
            lpwgs_root, runner, ledger, toolchain, need_bwa=True, need_minimap2=False, threads=cpu
        )
        bams = align_illumina(
            samples,
            reference,
            samurai_out,
            runner,
            ledger,
            toolchain,
            threads=cpu,
            force=force_run,
        )
        qdna_dir, refine_bam_dir = run_qdnaseq(
            root,
            lpwgs_root,
            samples,
            bams,
            samurai_out,
            _as_int(config.get("illumina_binsize_kb"), 100),
            runner,
            ledger,
            toolchain,
            force=force_run,
        )
        run_refinement_and_outputs(
            root,
            config,
            mode,
            samurai_out,
            qdna_dir,
            refine_bam_dir,
            outdir,
            lpwgs_root,
            runner,
            toolchain,
            force=force_run,
        )
    else:
        samples = parse_ont_samples(config)
        samurai_out = outdir / "01_samurai_ont"
        samurai_out.mkdir(parents=True, exist_ok=True)
        reference = prepare_reference(
            lpwgs_root, runner, ledger, toolchain, need_bwa=False, need_minimap2=True, threads=cpu
        )
        bams = align_ont(
            samples,
            reference,
            samurai_out,
            runner,
            ledger,
            toolchain,
            threads=cpu,
            min_age_minutes=_as_int(config.get("ont_min_age_minutes"), 0),
            force=force_run,
        )
        ichor_dir = run_ichorcna(
            root,
            lpwgs_root,
            samples,
            bams,
            samurai_out,
            _as_int(config.get("ont_binsize_kb"), 500),
            runner,
            ledger,
            toolchain,
            threads=cpu,
            force=force_run,
        )
        run_refinement_and_outputs(
            root,
            config,
            mode,
            samurai_out,
            ichor_dir,
            samurai_out / "bam",
            outdir,
            lpwgs_root,
            runner,
            toolchain,
            force=force_run,
        )

    if _as_bool(config.get("run_cna_classifier"), False):
        run_native_classifier(
            root,
            config,
            outdir,
            lpwgs_root,
            runner,
            ledger,
            toolchain,
            force=force_run,
        )

    trace_text = trace.read_text(encoding="utf-8", errors="replace")
    if "nextflow" in trace_text.lower():
        raise OncoTracerError("native execution trace contains a forbidden Nextflow command")
    write_run_manifest(outdir, config_path, trace)
    return outdir
