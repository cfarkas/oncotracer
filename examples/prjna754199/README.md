# PRJNA754199 public archive

The [full tutorial](../../docs/full_tutorial.md) explains how to download the
12 public single-end Illumina libraries, prepare their samplesheet, and analyze
them with the ordinary `auto`, `check`, and `run` commands. The tutorial creates
the table with `cat`; each row maps to one single-end FASTQ. It explains every
path and shows the checksums before the analysis step.

This folder holds the versioned [manifest](manifest.tsv),
[sample labels](samples.csv), [archive provenance](PROVENANCE.md), output
verifier and gallery exporter. The manifest contains 12 public runs, not every
specimen in the associated article. Sample aliases are not independently
verified diagnoses.

Use the standard installation and run commands in the tutorial. Genome-index
reuse is optional; see [prebuilt indexes](../../docs/reference_indexes.md).

## Terminal / headless version

Install the launcher and Conda tools first. Start in the source checkout, then
set your writable analysis directory. The versioned manifest supplies every
URL and checksum; filenames retain the aliases required by `samples.csv`.

```bash
set -euo pipefail
ONCOTRACER_EXAMPLES="$PWD/examples"
cd /path/to/my/analyses_dir/
mkdir -p "$PWD/oncotracer-prjna754199/input/fastq"

while IFS=$'\t' read -r alias biosample experiment accession instrument layout read_length reads bases archive_filename bytes checksum url; do
  [[ "$alias" == "sample_alias" ]] && continue
  curl --fail --location --continue-at - \
    --output "$PWD/oncotracer-prjna754199/input/fastq/$alias.fastq.gz" "$url"
  printf '%s  %s\n' "$checksum" "$PWD/oncotracer-prjna754199/input/fastq/$alias.fastq.gz" | md5sum -c -
  gzip -t "$PWD/oncotracer-prjna754199/input/fastq/$alias.fastq.gz"
done < "$ONCOTRACER_EXAMPLES/prjna754199/manifest.tsv"
cp "$ONCOTRACER_EXAMPLES/prjna754199/samples.csv" \
  "$PWD/oncotracer-prjna754199/input/fastq/samples.csv"
```

Continue only after all 12 checksums report `OK` and gzip reports no errors.
The following matches the tutorial: four threads, hg38/QDNAseq, sarcoma-context
research reports, and biomedical pathology models disabled.

```bash
oncotracer auto --mode illumina \
  --reads-folder "$PWD/oncotracer-prjna754199/input/fastq" \
  --sample-table "$PWD/oncotracer-prjna754199/input/fastq/samples.csv" \
  --config-dir "$PWD/oncotracer-prjna754199/config" \
  --outdir "$PWD/oncotracer-prjna754199/results" \
  --hg38_build --threads 4 --run-cna-classifier \
  --cna-classifier-sample-set sarcoma --no-pathology-models
oncotracer check --config "$PWD/oncotracer-prjna754199/config/illumina.auto.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-prjna754199/config/illumina.auto.yml"
```

No browser is started. Reference preparation occurs at run time. Resume by
repeating the same `run` command; keep the inputs, YAML and output path unchanged.
Inspect `results/06_workflow_summary/workflow_summary.txt` and verify all twelve
libraries completed. These are library counts, not twelve verified patients.
