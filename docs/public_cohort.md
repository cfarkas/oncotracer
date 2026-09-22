# QuickStart 2: three HCC1143 libraries

Analyze three public Illumina libraries in one project. Download all six paired-end
FASTQs, assign the three samples in the browser, then run.
[Install once](installation.md); [QuickStart 1](quick_start.md) is the smaller first example.
Reads total approximately 1.16 GB, plus tools, references, BAMs and results.

| Sample | Public run |
| --- | --- |
| `HCC1143_DMSO` | `SRR7085656` |
| `HCC1143_BEZ235` | `SRR7085655` |
| `HCC1143_TRAMETINIB` | `SRR7085657` |

DMSO is a treatment control, not a normal genome. All three are tumor libraries.

## 1. Download and verify the reads

Replace `/path/to/my/analyses_dir/` with your writable analysis directory in every
block. `$PWD` means its absolute path.

```bash
cd /path/to/my/analyses_dir/
mkdir -p oncotracer-quickstart2/input

curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_DMSO_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_1.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_DMSO_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_2.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_BEZ235_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_1.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_BEZ235_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_2.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_1.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_2.fastq.gz

md5sum -c <<'MD5'
419b241a073ecdbb0df5a9bec918c58b  oncotracer-quickstart2/input/HCC1143_DMSO_R1.fastq.gz
6398bf13a33c25e33693682bdeb00253  oncotracer-quickstart2/input/HCC1143_DMSO_R2.fastq.gz
3870b3fc2c693679cac9cce50cff2371  oncotracer-quickstart2/input/HCC1143_BEZ235_R1.fastq.gz
d21feb72ead870fb44c3e62d887e4974  oncotracer-quickstart2/input/HCC1143_BEZ235_R2.fastq.gz
688ee1571e591795b060963aacf942d3  oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R1.fastq.gz
f5975d77f63e9b139a24fb011580fd4d  oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R2.fastq.gz
MD5
```

Continue only when all six lines say `OK`. The versioned manifest records each
exact size and MD5 checksum. Repeat an interrupted `curl` command to resume.

## 2. Open setup and run

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart2/analysis" \
  --mode illumina --input-folder "$PWD/oncotracer-quickstart2/input"
```

Keep the terminal open; use its complete printed URL if the browser does not open.

1. Move all three detected paired-end samples into **Cancer**, including DMSO.
2. Review names and threads. Keep CNA, QDNAseq, **100 kb** bins and reference download.
3. Select your installed backend, click **Save configuration and check**, then
   **Run analysis** after checks pass. GISTIC is optional for this three-sample cohort.

Each library is analyzed separately. Setup protects existing configurations.

## 3. Review the results

Open the browser results dashboard, or visit `oncotracer-quickstart2/analysis/results/`.
Start with `06_workflow_summary/workflow_summary.txt`, then inspect plots and
`03_cna_codification/cna_events.tsv`. See [outputs](outputs.md).

## Alternative: scripted setup and terminal run

**Skip this section if you used the browser above.** It creates the same project
without questions. Use only one setup method per project.

<details markdown="1">
<summary>Show samplesheet, setup, check and run commands</summary>

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
  --mode illumina --analysis cna \
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

</details>

## Optional: reuse a reference

With the default choice, run downloads about 8.0 GiB of prebuilt hg38 indexes into
`analysis/reference/`. Saving/checking does not download them.
To reuse QuickStart 1's reference, select it in the browser or replace the bare
`--hg38_build` flag with
`--hg38_build /absolute/path/oncotracer-quickstart1/illumina/reference`.
To build locally, replace it with `--build_reference`; this needs more RAM, disk
and time. See [genome indexes](reference_indexes.md).

## Resume

Repeat the same `run` command after fixing an error. Keep the samplesheet, YAML
and output directory unchanged; omit setup and `--force` for a normal resume.
See [running and resuming](running.md).
