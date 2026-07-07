#!/usr/bin/env python3
"""
run_ifcnv_ont_lpwgs.py

Dedicated ifCNV wrapper for ONT low-pass WGS BAM/CRAM files.

It does not modify SAMURAI/QDNAseq/ichorCNA outputs. It creates an independent
workspace, links or copies BAMs there, creates missing indexes inside the
workspace, generates a genome-wide tiling BED if needed, creates/fixes a
dedicated conda environment, and runs ifCNV.

Main fix in this version:
  - ifCNV currently calls numpy.in1d(). NumPy 2.4 removed np.in1d.
    This wrapper pins NumPy to 1.26.4 inside the dedicated environment and
    checks that np.in1d exists before running ifCNV.
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional


###############################################################################
# Small utilities
###############################################################################


def ts() -> str:
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def eprint(*args: object) -> None:
    print(*args, file=sys.stderr)


def die(msg: str, code: int = 1) -> None:
    eprint(f"ERROR: {msg}")
    raise SystemExit(code)


def mkdir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def q(x: object) -> str:
    return shlex.quote(str(x))


def sanitize_sample_name(name: str) -> str:
    name = re.sub(r"\.sorted\.bam$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\.bam$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\.cram$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip())
    name = name.strip("._-")
    return name or "sample"


class Runner:
    def __init__(self, log_file: Path, dry_run: bool = False):
        self.log_file = log_file
        self.dry_run = dry_run
        mkdir(log_file.parent)
        with log_file.open("a") as fh:
            fh.write(f"\n# run started {ts()}\n")

    def log(self, cmd: list[str], cwd: Optional[Path] = None) -> None:
        line = " ".join(q(x) for x in cmd)
        with self.log_file.open("a") as fh:
            if cwd is not None:
                fh.write(f"(cd {q(cwd)} && {line})\n")
            else:
                fh.write(line + "\n")

    def run(
        self,
        cmd: list[str],
        cwd: Optional[Path] = None,
        check: bool = True,
        capture: bool = False,
        text: bool = True,
    ) -> subprocess.CompletedProcess:
        self.log(cmd, cwd=cwd)
        print(f"[{ts()}] $ {' '.join(q(x) for x in cmd)}")
        if self.dry_run:
            return subprocess.CompletedProcess(
                cmd,
                0,
                stdout="" if capture else None,
                stderr="" if capture else None,
            )
        return subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            check=check,
            capture_output=capture,
            text=text,
        )


###############################################################################
# Conda/environment handling
###############################################################################


def find_conda() -> str:
    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe and Path(conda_exe).exists():
        return conda_exe
    conda = shutil.which("conda")
    if conda:
        return conda
    die("conda not found. Load conda or install Miniconda/Anaconda first.")
    raise AssertionError


def conda_run(conda: str, env_name: str, cmd: list[str]) -> list[str]:
    return [conda, "run", "--no-capture-output", "-n", env_name] + cmd


def conda_env_exists(conda: str, env_name: str, runner: Runner) -> bool:
    cp = runner.run([conda, "env", "list", "--json"], capture=True, check=False)
    if cp.returncode == 0:
        try:
            data = json.loads(cp.stdout or "{}")
            return any(Path(p).name == env_name for p in data.get("envs", []))
        except json.JSONDecodeError:
            pass
    cp = runner.run(
        [conda, "run", "-n", env_name, "python", "--version"],
        capture=True,
        check=False,
    )
    return cp.returncode == 0


def remove_env(conda: str, env_name: str, runner: Runner) -> None:
    if conda_env_exists(conda, env_name, runner):
        print(f"Removing dedicated conda environment: {env_name}")
        runner.run([conda, "env", "remove", "-y", "-n", env_name])


def ensure_numpy_has_in1d(
    conda: str,
    env_name: str,
    runner: Runner,
    numpy_version: str,
    fix_numpy: bool,
) -> None:
    check_code = (
        "import numpy as np; "
        "print('numpy=' + np.__version__); "
        "assert hasattr(np, 'in1d'), 'numpy.in1d missing'"
    )
    cp = runner.run(
        conda_run(conda, env_name, ["python", "-c", check_code]),
        check=False,
        capture=True,
    )
    if cp.returncode == 0:
        print((cp.stdout or "").strip())
        return

    if not fix_numpy:
        die(
            "The dedicated environment has a NumPy version without np.in1d, "
            "and --no-fix-numpy was used. Re-run without --no-fix-numpy."
        )

    print(
        f"Fixing dedicated env NumPy: installing numpy={numpy_version} "
        "because ifCNV uses np.in1d."
    )

    cp = runner.run(
        [conda, "install", "-y", "-n", env_name, "-c", "conda-forge", f"numpy={numpy_version}"],
        check=False,
    )
    if cp.returncode != 0:
        print("WARNING: conda NumPy pin failed; trying pip pin inside the env.")
        runner.run(
            conda_run(
                conda,
                env_name,
                ["python", "-m", "pip", "install", "--force-reinstall", f"numpy=={numpy_version}"],
            )
        )

    cp2 = runner.run(
        conda_run(conda, env_name, ["python", "-c", check_code]),
        check=False,
        capture=True,
    )
    if cp2.returncode != 0:
        eprint(cp2.stdout or "")
        eprint(cp2.stderr or "")
        die("NumPy compatibility check still failed after pinning NumPy.")
    print((cp2.stdout or "").strip())


def create_or_fix_env(args: argparse.Namespace, runner: Runner) -> str:
    conda = find_conda()

    if args.recreate_env and not args.no_create_env:
        remove_env(conda, args.env_name, runner)

    if not args.no_create_env:
        if not conda_env_exists(conda, args.env_name, runner):
            print(f"Creating dedicated conda environment: {args.env_name}")
            runner.run(
                [
                    conda,
                    "create",
                    "-y",
                    "-n",
                    args.env_name,
                    f"python={args.python_version}",
                    "pip",
                    "-c",
                    "conda-forge",
                ]
            )
        else:
            print(f"Using existing dedicated conda environment: {args.env_name}")

        if args.install_dependencies:
            runner.run(
                [
                    conda,
                    "install",
                    "-y",
                    "-n",
                    args.env_name,
                    "-c",
                    "conda-forge",
                    "-c",
                    "bioconda",
                    f"numpy={args.numpy_version}",
                    "samtools",
                    "bedtools",
                    "pip",
                ],
                check=False,
            )

            if args.ifcnv_install_method in {"auto", "conda"}:
                cp = runner.run(
                    [
                        conda,
                        "install",
                        "-y",
                        "-n",
                        args.env_name,
                        "-c",
                        "conda-forge",
                        "-c",
                        "bioconda",
                        "ifcnv",
                    ],
                    check=False,
                )
                if cp.returncode != 0 and args.ifcnv_install_method == "conda":
                    die("conda installation of ifCNV failed. Retry with --ifcnv-install-method pip or auto.")
                if cp.returncode != 0:
                    print("WARNING: conda install ifCNV failed; trying pip fallback.")
                    runner.run(
                        conda_run(
                            conda,
                            args.env_name,
                            ["python", "-m", "pip", "install", "--upgrade", "pip"],
                        )
                    )
                    runner.run(
                        conda_run(
                            conda,
                            args.env_name,
                            ["python", "-m", "pip", "install", "ifCNV"],
                        )
                    )
            else:
                runner.run(
                    conda_run(
                        conda,
                        args.env_name,
                        ["python", "-m", "pip", "install", "--upgrade", "pip"],
                    )
                )
                runner.run(
                    conda_run(
                        conda,
                        args.env_name,
                        ["python", "-m", "pip", "install", "ifCNV"],
                    )
                )

    ensure_numpy_has_in1d(
        conda=conda,
        env_name=args.env_name,
        runner=runner,
        numpy_version=args.numpy_version,
        fix_numpy=args.fix_numpy,
    )

    runner.run(conda_run(conda, args.env_name, ["samtools", "--version"]), check=False)
    runner.run(conda_run(conda, args.env_name, ["ifCNV", "-h"]), check=False)

    return conda


###############################################################################
# BAM/CRAM discovery and workspace preparation
###############################################################################


@dataclass
class AlignmentRecord:
    sample: str
    original: Path
    workspace: Path
    kind: str
    index_original: Optional[Path] = None
    index_workspace: Optional[Path] = None
    mapped_reads: Optional[int] = None


def is_inside_excluded_dir(path: Path, excluded: set[str]) -> bool:
    return any(part in excluded for part in path.parts)


def discover_alignments(args: argparse.Namespace) -> list[Path]:
    inp = Path(args.input).resolve()
    if not inp.exists():
        die(f"--input does not exist: {inp}")

    if inp.is_file():
        if inp.name.endswith((".bam", ".cram")):
            return [inp]
        die("--input is a file, but it is not .bam or .cram")

    patterns = [x.strip() for x in args.bam_glob.split(",") if x.strip()]
    excluded = {x.strip() for x in args.exclude_dirs.split(",") if x.strip()}
    include_re = re.compile(args.include_sample_regex) if args.include_sample_regex else None
    exclude_re = re.compile(args.exclude_sample_regex) if args.exclude_sample_regex else None

    out: list[Path] = []
    seen: set[Path] = set()

    for pattern in patterns:
        for p in sorted(inp.glob(pattern)):
            if not p.is_file() or not p.name.endswith((".bam", ".cram")):
                continue
            rp = p.resolve()
            if rp in seen:
                continue
            if is_inside_excluded_dir(rp, excluded):
                continue
            sample = sanitize_sample_name(rp.name)
            if include_re and not include_re.search(sample):
                continue
            if exclude_re and exclude_re.search(sample):
                continue
            seen.add(rp)
            out.append(rp)

    return out


def find_alignment_index(path: Path) -> Optional[Path]:
    if path.name.endswith(".bam"):
        candidates = [Path(str(path) + ".bai"), path.with_suffix(".bai"), Path(str(path) + ".csi")]
    elif path.name.endswith(".cram"):
        candidates = [Path(str(path) + ".crai"), path.with_suffix(".crai")]
    else:
        candidates = []

    for c in candidates:
        if c.exists() and c.stat().st_size > 0:
            return c.resolve()
    return None


def link_or_copy(src: Path, dst: Path, mode: str, force: bool) -> None:
    if dst.exists() or dst.is_symlink():
        if force:
            dst.unlink()
        else:
            return
    if mode == "copy":
        shutil.copy2(src, dst)
    elif mode == "symlink":
        os.symlink(src, dst)
    else:
        die(f"Unsupported --link-mode: {mode}")


def prepare_alignment_workspace(
    args: argparse.Namespace,
    conda: str,
    runner: Runner,
    outdir: Path,
) -> list[AlignmentRecord]:
    align_dir = outdir / "input_bams_clean"
    mkdir(align_dir)

    alignments = discover_alignments(args)
    if not alignments:
        die(f"No .bam/.cram files found in --input {args.input}")

    records: list[AlignmentRecord] = []
    seen_samples: dict[str, int] = {}

    for p in alignments:
        base = sanitize_sample_name(p.name)
        seen_samples[base] = seen_samples.get(base, 0) + 1
        sample = base if seen_samples[base] == 1 else f"{base}_{seen_samples[base]}"
        ext = ".cram" if p.name.endswith(".cram") else ".bam"
        dst = align_dir / f"{sample}{ext}"

        link_or_copy(p, dst, args.link_mode, force=args.force)

        idx_orig = find_alignment_index(p)
        idx_dst = None
        if idx_orig:
            idx_dst = Path(str(dst) + (".bai" if ext == ".bam" else ".crai"))
            link_or_copy(idx_orig, idx_dst, args.link_mode, force=args.force)

        records.append(
            AlignmentRecord(
                sample=sample,
                original=p,
                workspace=dst,
                kind=ext.lstrip("."),
                index_original=idx_orig,
                index_workspace=idx_dst,
            )
        )

    if len(records) < args.min_samples:
        die(
            f"Found {len(records)} sample(s), but --min-samples is {args.min_samples}. "
            "ifCNV needs at least three samples for a useful intrarun reference."
        )

    if args.index_bams:
        for rec in records:
            expected = Path(str(rec.workspace) + (".bai" if rec.kind == "bam" else ".crai"))
            if not expected.exists() or expected.stat().st_size == 0:
                print(f"Indexing in workspace only: {rec.workspace}")
                runner.run(
                    conda_run(
                        conda,
                        args.env_name,
                        ["samtools", "index", "-@", str(args.threads), str(rec.workspace)],
                    )
                )
            if expected.exists() and expected.stat().st_size > 0:
                rec.index_workspace = expected

    if args.samtools_quickcheck:
        for rec in records:
            cp = runner.run(
                conda_run(conda, args.env_name, ["samtools", "quickcheck", "-v", str(rec.workspace)]),
                check=False,
            )
            if cp.returncode != 0:
                die(f"samtools quickcheck failed for {rec.workspace}")

    if args.count_reads:
        for rec in records:
            cp = runner.run(
                conda_run(conda, args.env_name, ["samtools", "idxstats", str(rec.workspace)]),
                capture=True,
                check=False,
            )
            if cp.returncode == 0 and cp.stdout:
                total = 0
                for line in cp.stdout.splitlines():
                    fields = line.split("\t")
                    if len(fields) >= 3:
                        try:
                            total += int(fields[2])
                        except ValueError:
                            pass
                rec.mapped_reads = total

    manifest = outdir / "manifest_ifcnv_input_bams.tsv"
    with manifest.open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(
            [
                "sample",
                "workspace_alignment",
                "original_alignment",
                "workspace_index",
                "original_index",
                "mapped_reads",
            ]
        )
        for rec in records:
            w.writerow(
                [
                    rec.sample,
                    str(rec.workspace),
                    str(rec.original),
                    str(rec.index_workspace or ""),
                    str(rec.index_original or ""),
                    rec.mapped_reads if rec.mapped_reads is not None else "",
                ]
            )

    print(f"Manifest written: {manifest}")
    return records


###############################################################################
# BED generation
###############################################################################


def parse_fai(fai: Path) -> list[tuple[str, int]]:
    rows: list[tuple[str, int]] = []
    with fai.open() as fh:
        for line in fh:
            if not line.strip():
                continue
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 2:
                continue
            try:
                rows.append((fields[0], int(fields[1])))
            except ValueError:
                continue
    if not rows:
        die(f"No contigs parsed from FAI: {fai}")
    return rows


def select_chromosomes(spec: str, available: Iterable[str]) -> list[str]:
    avail = list(available)
    avail_set = set(avail)
    has_chr = any(x.startswith("chr") for x in avail)

    def norm(ch: str) -> str:
        ch = ch.strip()
        if ch.startswith("chr"):
            return ch
        return f"chr{ch}" if has_chr else ch

    if spec == "autosomes":
        wanted = [norm(str(i)) for i in range(1, 23)]
    elif spec == "autosomes_plus_x":
        wanted = [norm(str(i)) for i in range(1, 23)] + [norm("X")]
    elif spec == "autosomes_plus_xy":
        wanted = [norm(str(i)) for i in range(1, 23)] + [norm("X"), norm("Y")]
    elif spec == "all":
        bad = ("random", "alt", "decoy", "un", "hap", "ebv", "_fix", "_patch")
        wanted = [c for c in avail if not any(x in c.lower() for x in bad) and c.lower() not in {"chrm", "mt", "m"}]
    else:
        wanted = [norm(x) for x in re.split(r"[,; ]+", spec) if x.strip()]

    selected = [c for c in wanted if c in avail_set]
    if not selected:
        die(f"No selected chromosomes from --chromosomes '{spec}' were found in FAI.")
    return selected


def build_or_use_bed(args: argparse.Namespace, conda: str, runner: Runner, outdir: Path) -> Path:
    if args.bed:
        bed = Path(args.bed).resolve()
        if not bed.exists() or bed.stat().st_size == 0:
            die(f"--bed missing or empty: {bed}")
        return bed

    if args.fai:
        fai = Path(args.fai).resolve()
    else:
        fasta = Path(args.reference_fasta).resolve()
        if not fasta.exists() or fasta.stat().st_size == 0:
            die(f"--reference-fasta missing or empty: {fasta}")
        fai = Path(str(fasta) + ".fai")
        if not fai.exists() or fai.stat().st_size == 0:
            print(f"Creating FASTA index: {fai}")
            runner.run(conda_run(conda, args.env_name, ["samtools", "faidx", str(fasta)]))

    if not fai.exists() or fai.stat().st_size == 0:
        die(f"FAI missing or empty: {fai}")

    if args.region_size_bp < args.bin_size_bp:
        die("--region-size-bp must be >= --bin-size-bp")

    contigs = parse_fai(fai)
    lengths = dict(contigs)
    chroms = select_chromosomes(args.chromosomes, [c for c, _ in contigs])

    bed_dir = outdir / "reference"
    mkdir(bed_dir)
    bed = bed_dir / f"ifCNV_lpwgs_{args.bin_size_bp}bp_{args.region_grouping}.bed"

    n = 0
    with bed.open("w") as fh:
        for chrom in chroms:
            length = lengths[chrom]
            bin_idx = 0
            for start in range(0, length, args.bin_size_bp):
                end = min(start + args.bin_size_bp, length)
                if end - start < args.min_last_bin_bp:
                    continue
                bin_idx += 1

                if args.region_grouping == "bin":
                    name = f"{chrom}-{start}-{end}"
                elif args.region_grouping == "chromosome":
                    name = f"{chrom}_bin{bin_idx:06d}"
                elif args.region_grouping == "window":
                    group_start = (start // args.region_size_bp) * args.region_size_bp
                    group_end = min(group_start + args.region_size_bp, length)
                    name = f"{chrom}-{group_start}-{group_end}_bin{bin_idx:06d}"
                else:
                    die(f"Unknown --region-grouping: {args.region_grouping}")

                fh.write(f"{chrom}\t{start}\t{end}\t{name}\n")
                n += 1

    if n == 0:
        die("Generated BED has zero intervals.")

    print(f"Generated BED with {n:,} intervals: {bed}")
    return bed


###############################################################################
# ifCNV run
###############################################################################


def run_ifcnv(args: argparse.Namespace, conda: str, runner: Runner, outdir: Path, bed: Path) -> None:
    ifcnv_out = outdir / "ifCNV"
    input_bams = outdir / "input_bams_clean"
    mkdir(ifcnv_out)

    reads_matrix = Path(args.ifcnv_reads_matrix_output).resolve() if args.ifcnv_reads_matrix_output else ifcnv_out / "reads_matrix.tsv"

    cmd = [
        "ifCNV",
        "-i",
        str(input_bams),
        "-b",
        str(bed),
        "-o",
        str(ifcnv_out),
        "-m",
        args.ifcnv_mode,
        "-r",
        args.ifcnv_run_name,
    ]

    if args.ifcnv_skip_matrix:
        matrix = Path(args.ifcnv_skip_matrix).resolve()
        if not matrix.exists() or matrix.stat().st_size == 0:
            die(f"--ifcnv-skip-matrix missing or empty: {matrix}")
        cmd += ["-s", str(matrix)]
    elif args.use_existing_matrix_if_present and reads_matrix.exists() and reads_matrix.stat().st_size > 0:
        print(f"Existing ifCNV reads matrix found; reusing it with -s: {reads_matrix}")
        cmd += ["-s", str(reads_matrix)]
    else:
        cmd += ["-rm", str(reads_matrix)]

    if args.ifcnv_min_reads is not None:
        cmd += ["-min", str(args.ifcnv_min_reads)]
    if args.ifcnv_conta_samples is not None:
        cmd += ["-cs", str(args.ifcnv_conta_samples)]
    if args.ifcnv_conta_targets is not None:
        cmd += ["-ct", str(args.ifcnv_conta_targets)]
    if args.ifcnv_score_threshold is not None:
        cmd += ["-sT", str(args.ifcnv_score_threshold)]
    if args.ifcnv_reg_sample:
        cmd += ["-rS", args.ifcnv_reg_sample]
    if args.ifcnv_reg_targets:
        cmd += ["-rT", args.ifcnv_reg_targets]
    if args.ifcnv_verbose is not None:
        cmd += ["-v", args.ifcnv_verbose]
    if args.ifcnv_auto_open:
        cmd += ["-a", "True"]
    if args.ifcnv_save:
        cmd += ["-sv", "True"]
    if args.ifcnv_lib_resources:
        cmd += ["-l", str(Path(args.ifcnv_lib_resources).resolve())]
    if args.ifcnv_extra_args:
        cmd += shlex.split(args.ifcnv_extra_args)

    print("\nRunning ifCNV")
    print("------------")
    runner.run(conda_run(conda, args.env_name, cmd))


###############################################################################
# Reports
###############################################################################


def write_report(args: argparse.Namespace, outdir: Path, bed: Path, records: list[AlignmentRecord]) -> None:
    report = outdir / "ifCNV_ONT_LPWGS_run_report.md"
    with report.open("w") as fh:
        fh.write("# ifCNV ONT low-pass WGS run report\n\n")
        fh.write(f"Generated: {ts()}\n\n")
        fh.write("## Purpose\n\n")
        fh.write(
            "This is a dedicated ifCNV run. It does not modify the existing "
            "SAMURAI/QDNAseq/ichorCNA pipeline folders. BAMs are linked or copied "
            "into an independent workspace and ifCNV is executed there.\n\n"
        )
        fh.write("## Key compatibility note\n\n")
        fh.write(
            "ifCNV was designed around read-depth distributions from targeted NGS. "
            "Here it is used exploratorily on ONT low-pass WGS by supplying a tiled "
            "genome-wide BED. Interpret results as an additional read-depth perspective "
            "to compare against QDNAseq and ichorCNA, not as a replacement.\n\n"
        )
        fh.write("## Inputs\n\n")
        fh.write(f"- Input: `{Path(args.input).resolve()}`\n")
        fh.write(f"- Output: `{outdir}`\n")
        fh.write(f"- BED: `{bed}`\n")
        fh.write(f"- bin_size_bp: `{args.bin_size_bp}`\n")
        fh.write(f"- region_size_bp: `{args.region_size_bp}`\n")
        fh.write(f"- region_grouping: `{args.region_grouping}`\n")
        fh.write(f"- conda env: `{args.env_name}`\n")
        fh.write(f"- NumPy pin: `{args.numpy_version}`\n\n")
        fh.write("## Samples\n\n")
        fh.write("| sample | mapped reads | workspace alignment |\n")
        fh.write("|---|---:|---|\n")
        for rec in records:
            reads = "" if rec.mapped_reads is None else str(rec.mapped_reads)
            fh.write(f"| {rec.sample} | {reads} | `{rec.workspace}` |\n")
    print(f"Report written: {report}")


###############################################################################
# CLI
###############################################################################


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="run_ifcnv_ont_lpwgs.py",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
        description="Run ifCNV on ONT low-pass WGS BAM/CRAM files in a dedicated environment.",
    )

    p.add_argument("--input", required=True, help="Input BAM/CRAM file or folder containing BAM/CRAM files.")
    p.add_argument("--output", required=True, help="Output workspace for this ifCNV run.")

    p.add_argument("--env-name", default="ifcnv_ont_lpwgs_env", help="Dedicated conda environment name.")
    p.add_argument("--python-version", default="3.11", help="Python version for the dedicated env.")
    p.add_argument("--numpy-version", default="1.26.4", help="Pinned NumPy version with np.in1d available.")
    p.add_argument("--fix-numpy", action="store_true", default=True, help="Pin/fix NumPy for ifCNV compatibility.")
    p.add_argument("--no-fix-numpy", dest="fix_numpy", action="store_false", help="Do not pin/fix NumPy.")
    p.add_argument("--install-dependencies", action="store_true", default=True, help="Install ifCNV/samtools/bedtools in env.")
    p.add_argument("--no-install-dependencies", dest="install_dependencies", action="store_false")
    p.add_argument("--ifcnv-install-method", choices=["auto", "conda", "pip"], default="auto")
    p.add_argument("--no-create-env", action="store_true", help="Use existing env only.")
    p.add_argument("--recreate-env", action="store_true", help="Remove and recreate the dedicated env.")

    p.add_argument("--bam-glob", default="**/*.bam,**/*.cram", help="Comma-separated glob(s) when --input is a folder.")
    p.add_argument(
        "--exclude-dirs",
        default="work,.nextflow,nextflow_launch,tmp,merged_fastq,results,ifCNV,reference,logs",
        help="Directory names to ignore during recursive BAM search.",
    )
    p.add_argument("--include-sample-regex", default="", help="Keep sample names matching regex.")
    p.add_argument("--exclude-sample-regex", default="", help="Exclude sample names matching regex.")
    p.add_argument("--link-mode", choices=["symlink", "copy"], default="symlink", help="How to stage BAMs.")
    p.add_argument("--force", action="store_true", help="Overwrite existing staged symlinks/files.")
    p.add_argument("--index-bams", action="store_true", default=True, help="Create missing indexes inside output workspace.")
    p.add_argument("--no-index-bams", dest="index_bams", action="store_false")
    p.add_argument("--samtools-quickcheck", action="store_true", default=True)
    p.add_argument("--no-samtools-quickcheck", dest="samtools_quickcheck", action="store_false")
    p.add_argument("--count-reads", action="store_true", default=True)
    p.add_argument("--no-count-reads", dest="count_reads", action="store_false")
    p.add_argument("--min-samples", type=int, default=3, help="Minimum samples for ifCNV run.")

    p.add_argument("--bed", default="", help="User-supplied BED. If absent, WGS tiling BED is generated.")
    p.add_argument(
        "--reference-fasta",
        default="/media/server/STORAGE/LPWGS_2025/references/samurai_hg38/genome.fa",
        help="Reference FASTA used to find/create .fai for BED generation.",
    )
    p.add_argument("--fai", default="", help="FAI file. Overrides --reference-fasta.fai.")
    p.add_argument("--bin-size-bp", type=int, default=500000, help="Tiling bin size for generated BED.")
    p.add_argument("--region-size-bp", type=int, default=5000000, help="Window grouping size for BED column 4.")
    p.add_argument(
        "--region-grouping",
        choices=["window", "chromosome", "bin"],
        default="window",
        help="BED column-4 grouping strategy.",
    )
    p.add_argument(
        "--chromosomes",
        default="autosomes",
        help="autosomes, autosomes_plus_x, autosomes_plus_xy, all, or comma-separated contigs.",
    )
    p.add_argument("--min-last-bin-bp", type=int, default=100000, help="Drop terminal bins shorter than this.")

    p.add_argument("--ifcnv-mode", choices=["fast", "extensive"], default="extensive")
    p.add_argument("--ifcnv-run-name", default="ONT_LPWGS_ifCNV")
    p.add_argument("--ifcnv-skip-matrix", default="", help="Existing reads matrix for ifCNV -s.")
    p.add_argument("--ifcnv-reads-matrix-output", default="", help="Path for ifCNV -rm reads matrix.")
    p.add_argument(
        "--use-existing-matrix-if-present",
        action="store_true",
        default=True,
        help="If reads_matrix.tsv exists in output, reuse it with ifCNV -s.",
    )
    p.add_argument("--no-use-existing-matrix", dest="use_existing_matrix_if_present", action="store_false")
    p.add_argument("--ifcnv-min-reads", type=float, default=None, help="ifCNV -min.")
    p.add_argument("--ifcnv-conta-samples", type=float, default=None, help="ifCNV -cs.")
    p.add_argument("--ifcnv-conta-targets", type=float, default=None, help="ifCNV -ct.")
    p.add_argument("--ifcnv-score-threshold", type=float, default=None, help="ifCNV -sT.")
    p.add_argument("--ifcnv-reg-sample", default="", help="ifCNV -rS.")
    p.add_argument("--ifcnv-reg-targets", default="", help="ifCNV -rT.")
    p.add_argument("--ifcnv-verbose", default=None, help="ifCNV -v value.")
    p.add_argument("--ifcnv-auto-open", action="store_true", help="Pass -a True. Off by default for servers.")
    p.add_argument("--ifcnv-save", action="store_true", default=True, help="Pass -sv True.")
    p.add_argument("--no-ifcnv-save", dest="ifcnv_save", action="store_false")
    p.add_argument("--ifcnv-lib-resources", default="", help="ifCNV -l path.")
    p.add_argument("--ifcnv-extra-args", default="", help="Raw extra arguments appended to ifCNV command.")

    p.add_argument("--threads", type=int, default=8, help="Threads for samtools operations.")
    p.add_argument("--write-report", action="store_true", default=True)
    p.add_argument("--no-write-report", dest="write_report", action="store_false")
    p.add_argument("--dry-run", action="store_true", help="Print commands without executing them.")

    return p


###############################################################################
# Main
###############################################################################


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    outdir = Path(args.output).resolve()
    mkdir(outdir)
    mkdir(outdir / "logs")
    runner = Runner(outdir / "logs" / "run_ifcnv_ont_lpwgs.commands.sh", dry_run=args.dry_run)

    print("=" * 78)
    print("Dedicated ifCNV wrapper for ONT low-pass WGS")
    print("=" * 78)
    print(f"Input     : {Path(args.input).resolve()}")
    print(f"Output    : {outdir}")
    print(f"Conda env : {args.env_name}")
    print(f"NumPy pin : {args.numpy_version}")
    print("Existing SAMURAI/QDNAseq/ichorCNA folders are read-only inputs.")
    print("=" * 78)

    conda = create_or_fix_env(args, runner)
    records = prepare_alignment_workspace(args, conda, runner, outdir)
    bed = build_or_use_bed(args, conda, runner, outdir)

    if args.write_report:
        write_report(args, outdir, bed, records)

    run_ifcnv(args, conda, runner, outdir, bed)

    print("\nDone.")
    print(f"Output folder : {outdir}")
    print(f"ifCNV output  : {outdir / 'ifCNV'}")
    print(f"Input BAM dir : {outdir / 'input_bams_clean'}")
    print(f"BED file      : {bed}")
    print(f"Command log   : {outdir / 'logs' / 'run_ifcnv_ont_lpwgs.commands.sh'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
