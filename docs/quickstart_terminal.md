# QuickStarts from the terminal

Use these commands when you want a scripted run without browser setup. First
[install a backend](installation.md) and download and verify the reads in
[QuickStart 1](quick_start.md#1-download-and-verify-the-reads) or
[QuickStart 2](public_cohort.md#1-download-and-verify-the-reads).
Use a new project folder; these commands replace the browser steps in those guides.

## QuickStart 1


After downloading, these **replace browser setup**. For Docker, use
`--backend docker` in setup/run plus your installed `--image`.
To match browser preservation metadata, append `--variant-specimen-type fresh`
or `--variant-specimen-type ffpe` to setup, according to specimen records.

### Illumina — terminal only

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --non-interactive \
  --project "$PWD/oncotracer-quickstart1/illumina" --mode illumina --analysis cna \
  --sample-name ERR12341627 --status tumor \
  --fastq-1 "$PWD/oncotracer-quickstart1/input/illumina/ERR12341627_1.fastq.gz" \
  --fastq-2 "$PWD/oncotracer-quickstart1/input/illumina/ERR12341627_2.fastq.gz" \
  --backend conda --threads 4
oncotracer check --config "$PWD/oncotracer-quickstart1/illumina/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/illumina/config/run.yml"
```

### ONT — terminal only

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --non-interactive \
  --project "$PWD/oncotracer-quickstart1/ont" --mode ont --analysis cna \
  --reads-folder "$PWD/oncotracer-quickstart1/input/fastq_pass" \
  --barcodes barcode01 --sample-names DRR165691 --backend conda --threads 4
oncotracer check --config "$PWD/oncotracer-quickstart1/ont/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/ont/config/run.yml"
```

## QuickStart 2


**Terminal / headless version:** after the downloads above, run these blocks
instead of browser setup. They create the same three-library project without
questions. Use only one setup method per project. Append
`--variant-specimen-type fresh` or `--variant-specimen-type ffpe` to setup to
record the same preservation selected in the browser, using specimen records.

Create the samplesheet with absolute paths. Paste the final `CSV` line too.
`cat >` replaces the CSV if it already exists.

```bash
cd /path/to/my/analyses_dir/
cat > "$PWD/oncotracer-quickstart2/input/samplesheet.csv" <<CSV
sample,fastq_1,fastq_2,status
HCC1143_DMSO,"$PWD/oncotracer-quickstart2/input/HCC1143_DMSO_R1.fastq.gz","$PWD/oncotracer-quickstart2/input/HCC1143_DMSO_R2.fastq.gz",tumor
HCC1143_BEZ235,"$PWD/oncotracer-quickstart2/input/HCC1143_BEZ235_R1.fastq.gz","$PWD/oncotracer-quickstart2/input/HCC1143_BEZ235_R2.fastq.gz",tumor
HCC1143_TRAMETINIB,"$PWD/oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R1.fastq.gz","$PWD/oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R2.fastq.gz",tumor
CSV
```

Create settings without starting analysis:

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --non-interactive \
  --project "$PWD/oncotracer-quickstart2/analysis" \
  --hg38_build \
  --mode illumina --analysis cna --backend conda \
  --samplesheet "$PWD/oncotracer-quickstart2/input/samplesheet.csv" \
  --threads 4
```

Check should list all three names. Resolve errors before running:

```bash
cd /path/to/my/analyses_dir/
oncotracer check --config "$PWD/oncotracer-quickstart2/analysis/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart2/analysis/config/run.yml"
```

Use the corresponding [backend](containers.md) for Docker or Apptainer.

## References and resuming

The examples prepare the required hg38 indexes when the analysis starts.
To reuse a prepared reference, add `--hg38_build /data/shared-reference` to
setup; for QuickStart 2, replace its bare `--hg38_build` flag. Illumina needs
BWA indexes and ONT needs minimap2 indexes. Use `--build_reference` instead to
build indexes locally. See [genome indexes](reference_indexes.md).

After interruption, repeat only the saved project's `oncotracer check` and
`oncotracer run` commands. Valid completed work is reused; omit `--force`.
See [run, stop and resume](running.md) for progress and stop controls, or
[headless servers](headless.md) for SSH and persistent terminal sessions.
