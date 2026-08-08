#!/usr/bin/env python3
"""Align release documentation and the standalone server audit with permanent gates."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_exact(path: Path, old: str, new: str, *, count: int = 1) -> None:
    text = path.read_text(encoding="utf-8")
    observed = text.count(old)
    if observed != count:
        raise SystemExit(
            f"{path}: expected {count} exact occurrence(s), observed {observed}"
        )
    path.write_text(text.replace(old, new, count), encoding="utf-8")


def replace_regex(path: Path, pattern: str, replacement: str) -> None:
    text = path.read_text(encoding="utf-8")
    updated, observed = re.subn(pattern, replacement, text, count=1, flags=re.DOTALL)
    if observed != 1:
        raise SystemExit(f"{path}: expected one regex replacement, observed {observed}")
    path.write_text(updated, encoding="utf-8")


def patch_server_driver() -> None:
    path = ROOT / "scripts" / "validate_v2_release.sh"
    replacement = r'''generate_samurai_trace_audit() {
  local root="$1" mode="$2" expected_rows="$3" destination="$4" trace_copy="$5"
  local selected="${6-}" temporary selected_output copy_tmp
  temporary="$(mktemp "$TMP_DIR/samurai-trace-audit.XXXXXX")"
  if ! selected_output="$(
    python3 - \
      "$root" "$mode" "$expected_rows" \
      "$CONTEXT_DIR/samurai-container-pins.tsv" "$temporary" "$selected" \
      "$REPOSITORY_ROOT/tests" <<'PY'
import csv
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

root = Path(sys.argv[1]).resolve()
mode = sys.argv[2]
expected_rows = int(sys.argv[3])
pins_path = Path(sys.argv[4])
destination = Path(sys.argv[5])
selected_arg = sys.argv[6]
tests_dir = Path(sys.argv[7]).resolve()
sys.path.insert(0, str(tests_dir))
from combine_nested_samurai_traces import combine_root  # noqa: E402

expected_processes = {
    "illumina": {
        "SAMURAI:FASTQ_TRIM_FASTP_FASTQC:FASTQC_RAW",
        "SAMURAI:FASTQ_ALIGN_DNA:BWAMEM1_MEM",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:PICARD_MARKDUPLICATES",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:SAMTOOLS_INDEX",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:BAM_STATS_SAMTOOLS:SAMTOOLS_STATS",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:BAM_STATS_SAMTOOLS:SAMTOOLS_FLAGSTAT",
        "SAMURAI:BAM_MARKDUPLICATES_PICARD:BAM_STATS_SAMTOOLS:SAMTOOLS_IDXSTATS",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTMULTIPLEMETRICS",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTWGSMETRICS",
        "SAMURAI:SOLID_BIOPSY:QDNASEQ",
        "SAMURAI:SOLID_BIOPSY:CONCATENATE_QDNASEQ_PLOTS",
        "SAMURAI:MULTIQC",
    },
    "ont": {
        "SAMURAI:SAMTOOLS_INDEX",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTMULTIPLEMETRICS",
        "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTWGSMETRICS",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:HMMCOPY_READCOUNTER_ICHORCNA",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:ICHORCNA_RUN",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:AGGREGATE_ICHORCNA_TABLE",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:CORRECT_LOGR_ICHORCNA",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:PLOT_ICHORCNA",
        "SAMURAI:LIQUID_BIOPSY:ICHORCNA:CONCATENATE_BIN_PLOTS",
        "SAMURAI:MULTIQC",
    },
}
expected_images = {
    "illumina": {
        "quay.io/biocontainers/fastqc:0.12.1--hdfd78af_0",
        "community.wave.seqera.io/library/bwa_htslib_samtools:83b50ff84ead50d0",
        "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
        "quay.io/biocontainers/samtools:1.22.1--h96c455f_0",
        "quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1",
        "docker.io/t0shy/qpdf-docker:11.3.0",
        "community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf",
    },
    "ont": {
        "quay.io/biocontainers/samtools:1.22.1--h96c455f_0",
        "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
        "community.wave.seqera.io/library/hmmcopy_samtools:875db3767c6d4ea2",
        "community.wave.seqera.io/library/r-ichorcna:0.5.1--eed4be826f05c9d4",
        "quay.io/einar_rainhart/pandas-pandera:1.5.3",
        "community.wave.seqera.io/library/polars_procps-ng_typer:d1a53d7945a021e3",
        "community.wave.seqera.io/library/procps-ng_r-argparser_r-dplyr_r-ggplot2_pruned:10da72fa04bcba1a",
        "docker.io/t0shy/qpdf-docker:11.3.0",
        "community.wave.seqera.io/library/multiqc:1.32--d58f60e4deb769bf",
    },
}
ont_resume_processes = {
    "SAMURAI:SAMTOOLS_INDEX",
    "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTMULTIPLEMETRICS",
    "SAMURAI:BAM_QC_PICARD:PICARD_COLLECTWGSMETRICS",
    "SAMURAI:LIQUID_BIOPSY:ICHORCNA:HMMCOPY_READCOUNTER_ICHORCNA",
}
ont_resume_images = {
    "quay.io/biocontainers/samtools:1.22.1--h96c455f_0",
    "community.wave.seqera.io/library/picard:3.4.0--e9963040df0a9bf6",
    "community.wave.seqera.io/library/hmmcopy_samtools:875db3767c6d4ea2",
}
if mode not in expected_processes:
    raise SystemExit(f"unsupported SAMURAI trace mode: {mode}")

pins = {}
with pins_path.open(encoding="utf-8") as handle:
    for line in handle:
        tag, digest = line.rstrip("\n").split("\t")
        aliases = {tag, f"{tag}@{digest}"}
        if tag.startswith("quay.io/biocontainers/"):
            aliases.add(tag.removeprefix("quay.io/"))
        if tag.startswith("docker.io/"):
            aliases.add(tag.removeprefix("docker.io/"))
        for alias in aliases:
            if alias in pins and pins[alias] != (tag, digest):
                raise SystemExit(f"ambiguous SAMURAI image alias: {alias}")
            pins[alias] = (tag, digest)

selected, source_manifest, _ = combine_root(root)
selected = selected.resolve()
source_manifest = source_manifest.resolve()
if selected_arg and Path(selected_arg).resolve() != selected:
    raise SystemExit(
        f"recorded combined SAMURAI trace changed: expected {selected_arg}, observed {selected}"
    )

rows = []
process_names = []
containers = set()
with selected.open(encoding="utf-8-sig", newline="") as handle:
    reader = csv.DictReader(handle, delimiter="\t")
    required = {"name", "status", "exit", "container"}
    missing = required - set(reader.fieldnames or [])
    if missing:
        raise SystemExit(f"SAMURAI trace lacks required column(s): {sorted(missing)}")
    for row in reader:
        name = (row.get("name") or "").rstrip("\r")
        normalized = re.sub(
            r"\s+\([^()]*(?:\([^()]*\)[^()]*)*\)$", "", name.strip()
        )
        if ":SAMURAI:" in normalized:
            normalized = "SAMURAI:" + normalized.rsplit(":SAMURAI:", 1)[1]
        if normalized not in expected_processes[mode]:
            continue
        status = (row.get("status") or "").rstrip("\r").upper()
        exit_code = (row.get("exit") or "").rstrip("\r")
        container = (row.get("container") or "").rstrip("\r").removeprefix("docker://")
        if status not in {"COMPLETED", "CACHED"} or exit_code != "0":
            raise SystemExit(
                f"non-passing contracted SAMURAI row: {name!r} "
                f"status={status!r} exit={exit_code!r}"
            )
        if container in {"", "-", "null"} or container not in pins:
            raise SystemExit(f"unresolved or forbidden SAMURAI container: {container!r}")
        canonical, digest = pins[container]
        containers.add(canonical)
        process_names.append(normalized)
        rows.append(
            {
                "name": name,
                "normalized_process": normalized,
                "status": status,
                "exit": exit_code,
                "container": container,
                "canonical_container": canonical,
                "repo_digest": digest,
                "source_trace": row.get("source_trace", ""),
                "source_row": row.get("source_row", ""),
            }
        )

processes = set(process_names)
complete = (
    len(rows) == expected_rows
    and processes == expected_processes[mode]
    and containers == expected_images[mode]
)
resume = (
    mode == "ont"
    and len(rows) == 4
    and processes == ont_resume_processes
    and containers == ont_resume_images
)
if complete:
    evidence_mode = "complete-combined-trace"
elif resume:
    evidence_mode = "exact-ont-final-resume-trace"
else:
    raise SystemExit(
        "SAMURAI combined trace contract mismatch: "
        f"mode={mode!r} expected_rows={expected_rows} observed_rows={len(rows)} "
        f"counts={dict(Counter(process_names))!r} "
        f"missing_processes={sorted(expected_processes[mode] - processes)!r} "
        f"extra_processes={sorted(processes - expected_processes[mode])!r} "
        f"observed_containers={sorted(containers)!r}"
    )


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

with source_manifest.open(newline="", encoding="utf-8") as handle:
    source_rows = list(csv.DictReader(handle, delimiter="\t"))
available = []
for source in source_rows:
    source_path = (root / source["source_trace"]).resolve()
    source_path.relative_to(root)
    if sha256(source_path) != source["sha256"]:
        raise SystemExit(f"source trace checksum changed: {source_path}")
    available.append(
        {
            "path": str(source_path),
            "rows": int(source["rows"]),
            "successful_rows": int(source["successful_rows"]),
            "sha256": source["sha256"],
        }
    )

record = {
    "schema": "oncotracer-samurai-trace-audit-v1",
    "mode": mode,
    "evidence_mode": evidence_mode,
    "source_trace": str(selected),
    "source_trace_sha256": sha256(selected),
    "source_manifest": str(source_manifest),
    "source_manifest_sha256": sha256(source_manifest),
    "available_traces": available,
    "contract_row_count": expected_rows,
    "row_count": len(rows),
    "processes": sorted(processes),
    "contract_processes": sorted(expected_processes[mode]),
    "containers": sorted(containers),
    "contract_containers": sorted(expected_images[mode]),
    "rows": rows,
}
destination.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(selected)
PY
  )"; then
    rm -f -- "$temporary"
    return 1
  fi
  if [[ -z "$selected_output" || ! -f "$selected_output" || ! -s "$temporary" ]]; then
    rm -f -- "$temporary"
    return 1
  fi
  copy_tmp="$(mktemp "$TMP_DIR/samurai-trace-copy.XXXXXX")" || return 1
  if ! cp "$selected_output" "$copy_tmp"; then
    rm -f -- "$temporary" "$copy_tmp"
    return 1
  fi
  mv -- "$copy_tmp" "$trace_copy" || return 1
  mv -- "$temporary" "$destination" || return 1
}

verify_samurai_trace_audit() {'''
    replace_regex(
        path,
        r"generate_samurai_trace_audit\(\) \{.*?\n\}\n\nverify_samurai_trace_audit\(\) \{",
        replacement,
    )


def patch_documentation() -> None:
    path = ROOT / "docs" / "parity_release.md"
    replace_exact(
        path,
        '''- CNA events matched by sample, state, chromosome, and at least 0.80 reciprocal interval overlap;
- event recall and precision of at least 0.90;
- at least 0.95 of each refined-bin grid shared exactly;
- refined-bin Pearson correlation of at least 0.98;
- median absolute refined-bin log₂ difference no greater than 0.08.
''',
        '''- CNA events matched by sample, state, chromosome, and at least 0.80 reciprocal interval overlap;
- event-count recall and precision retained in the report as split/merge diagnostics;
- sample-, chromosome-, and state-specific CNA genomic-coverage recall and precision of at least 0.90;
- at least 0.95 of the original corrected-bin coordinate grid shared exactly;
- corrected input log₂-signal Pearson correlation of at least 0.98;
- median absolute corrected input log₂ difference no greater than 0.08.
''',
    )
    replace_exact(
        path,
        '''The Illumina parity contracts validate the BAM-stage SAMURAI execution actually
invoked by OncoTracer. QuickStart 1 therefore expects six nested tasks for one
sample, and QuickStart 2 expects fourteen nested tasks for three samples. Only
the five runtime images used by those BAM-stage tasks are pinned and required;
FASTQ QC and alignment occur in OncoTracer before SAMURAI is called.
''',
        '''The Illumina GitHub parity contracts validate the BAM-stage SAMURAI execution
actually invoked by OncoTracer. QuickStart 1 therefore expects six contracted
nested tasks for one sample, and QuickStart 2 expects fourteen contracted tasks
for three samples. Only the five runtime images used by those BAM-stage tasks
are required by those contracts; FASTQ QC and alignment occur in OncoTracer
before SAMURAI is called.

Nested Nextflow resumes can distribute successful tasks across several trace
files. Both the hosted release gate and the standalone validation-server driver
therefore build a deterministic combined trace from the latest successful or
cached occurrence of each canonical task and retain the complete source-trace
manifest. The ONT audit accepts either the complete ten-process contract or the
exact four-process final-resume trace observed after downstream ichorCNA stages
were already complete. That narrow fallback is accepted only while required
scientific outputs, the compatibility marker, and every immutable container pin
remain independently verified. A smaller or different subset fails closed.
''',
    )


def patch_tests() -> None:
    path = ROOT / "tests" / "test_native_docs.py"
    replace_exact(
        path,
        '''    def test_mkdocs_contains_assurance_pages(self) -> None:
''',
        '''    def test_parity_documentation_matches_the_executable_gate(self) -> None:
        text = (ROOT / "docs/parity_release.md").read_text(encoding="utf-8")
        self.assertIn("state-specific CNA genomic-coverage recall and precision", text)
        self.assertIn("corrected input log₂-signal Pearson correlation", text)
        self.assertIn("exact four-process final-resume trace", text)
        self.assertIn("A smaller or different subset fails closed", text)
        self.assertNotIn("event recall and precision of at least", text)
        self.assertNotIn("refined-bin Pearson correlation", text)

    def test_mkdocs_contains_assurance_pages(self) -> None:
''',
    )
    replace_exact(
        path,
        '''        self.assertIn("oncotracer-samurai-trace-audit-v1", text)
        self.assertIn('"container"', text)
''',
        '''        self.assertIn("oncotracer-samurai-trace-audit-v1", text)
        self.assertIn("from combine_nested_samurai_traces import combine_root", text)
        self.assertIn('evidence_mode = "exact-ont-final-resume-trace"', text)
        self.assertIn('"contract_containers": sorted(expected_images[mode])', text)
        self.assertNotIn("selected = max(traces", text)
        self.assertIn('"container"', text)
''',
    )


def main() -> None:
    patch_server_driver()
    patch_documentation()
    patch_tests()
    print("Applied release documentation and standalone server-audit alignment")


if __name__ == "__main__":
    main()
