"""Beginner-facing options around the shared, validated FASTQ table generator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .runtime import OncoTracerError
from .setup import _existing_hg38_parent


def add_auto_arguments(parser) -> None:
    parser.description = (
        "Create settings and a sample mapping, then use check and run. "
        "No analysis or genome download is started. Existing configurations are never overwritten."
    )
    parser.add_argument("--mode", choices=("illumina", "ont"), required=True, help="sequencing platform")
    parser.add_argument("--reads-folder", required=True, metavar="PATH", help="Illumina FASTQ folder, or ONT fastq_pass parent of barcode folders")
    parser.add_argument("--sample-table", required=True, metavar="CSV", help="Illumina: sample_name,status; ONT: barcode,sample_name,status")
    parser.add_argument("--config-dir", metavar="PATH", help="folder for saved settings (default: READS/oncotracer_config); existing files are protected")
    parser.add_argument("--outdir", metavar="PATH", help="analysis results (default: READS/oncotracer_results); created only by run")
    parser.add_argument("--threads", type=int, default=8, help="CPU worker threads saved in YAML (default: 8)")
    reference = parser.add_mutually_exclusive_group()
    reference.add_argument("--hg38_build", nargs="?", const="", metavar="PATH", help="reuse a prepared OncoTracer hg38 directory; no PATH selects automatic prebuilt download at run time (default)")
    reference.add_argument("--build_reference", action="store_true", help="build missing hg38 indexes on CPU at run time; needs more RAM, disk and time")
    parser.add_argument("--run-cna-classifier", action="store_true", help="add copy-number interpretation reports, not methylation classification")
    parser.add_argument("--cna-classifier-sample-set", default="broad_cancer", metavar="NAME", help="study context for CNA reports, e.g. sarcoma (default: broad_cancer); requires --run-cna-classifier")
    parser.add_argument("--no-pathology-models", action="store_true", help="disable optional biomedical model downloads/inference in CNA reports")
    parser.add_argument("--dry-run", action="store_true", help="show the configuration-generation command without creating files or checking gzip data")
    parser.add_argument("--root", help=argparse.SUPPRESS)


def prepare_auto_command(args) -> list[str | Path]:
    if args.threads < 1:
        raise OncoTracerError("--threads must be positive")
    if (args.cna_classifier_sample_set != "broad_cancer" or args.no_pathology_models) and not args.run_cna_classifier:
        raise OncoTracerError("report options require --run-cna-classifier")
    reads = Path(args.reads_folder).expanduser().resolve()
    config = Path(args.config_dir).expanduser().resolve() if args.config_dir else reads / "oncotracer_config"
    outdir = Path(args.outdir).expanduser().resolve() if args.outdir else reads / "oncotracer_results"
    reference = (
        _existing_hg38_parent(Path(args.hg38_build).expanduser().resolve(), args.mode)
        if args.hg38_build else config / "reference"
    )
    # The public route has explicit barcode IDs. Never infer biological identity
    # from directory sort order. Internal frozen-example callers remain supported.
    command: list[str | Path] = [
        "--mode", args.mode, "--reads-folder", reads,
        "--sample-table", Path(args.sample_table).expanduser().resolve(),
        "--config-dir", config, "--outdir", outdir,
        "--reference-root", reference,
        "--hg38-auto-download", str(not (args.hg38_build or args.build_reference)).lower(),
        "--threads", str(args.threads), "--no-overwrite", "--explicit-barcodes",
        "--table-python", sys.executable,
        "--offline-knowledge",
        "--run-cna-classifier", str(args.run_cna_classifier).lower(),
        "--cna-classifier-sample-set", args.cna_classifier_sample_set,
    ]
    if args.no_pathology_models:
        command.extend(["--pathology-use-biomed-models", "false", "--pathology-biomed-local-files-only", "true"])
    return command
