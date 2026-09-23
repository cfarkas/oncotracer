<a id="quick-start"></a>

# QuickStart 1: Illumina and ONT

Run a complete native analysis of one Illumina and one ONT public library:
approximately 225 MB of reads, plus tools, references and results. This CNA
example cannot assess methylation from FASTQs.

[Install OncoTracer once](installation.md). Then **download → configure in the
browser → run → inspect results**, or use the [terminal-only alternative](#alternative-scripted-setup-and-terminal-run). Choose either platform. For SSH, see [headless servers](headless.md).

## 1. Download and verify the reads

Replace `/path/to/my/analyses_dir/` with your writable analysis directory in every
block. `$PWD` means that directory's absolute path.

```bash
cd /path/to/my/analyses_dir/
mkdir -p oncotracer-quickstart1/input/illumina \
  oncotracer-quickstart1/input/fastq_pass/barcode01

curl --fail --location --continue-at - \
  --output oncotracer-quickstart1/input/illumina/ERR12341627_1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/027/ERR12341627/ERR12341627_1.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart1/input/illumina/ERR12341627_2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/027/ERR12341627/ERR12341627_2.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart1/input/fastq_pass/barcode01/DRR165691_1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/DRR165/DRR165691/DRR165691_1.fastq.gz

md5sum -c <<'MD5'
4c96d551152694b3893ea98b7781a3ae  oncotracer-quickstart1/input/illumina/ERR12341627_1.fastq.gz
1b20d9eb98f755244f6383ea1354bd40  oncotracer-quickstart1/input/illumina/ERR12341627_2.fastq.gz
55a3984cb0334aa4cb0a38255cb71c06  oncotracer-quickstart1/input/fastq_pass/barcode01/DRR165691_1.fastq.gz
MD5
```

Require three checksum `OK` lines. Repeat interrupted `curl` commands to resume.
The ONT folder is `barcode01`.

## 2. Open setup and run

Each command opens prefilled browser setup. Keep its terminal open. Finish and
stop one server with Ctrl+C before starting another.

### Illumina

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/illumina" \
  --mode illumina --input-folder "$PWD/oncotracer-quickstart1/input/illumina"
```

Move `ERR12341627` to **Cancer**. Keep CNA, QDNAseq and **100 kb** bins.

### ONT

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/ont" \
  --mode ont --input-folder "$PWD/oncotracer-quickstart1/input/fastq_pass"
```

Move `barcode01` to **Cancer**, rename it `DRR165691`, and keep CNA,
ichorCNA and **500 kb** bins. All files in the barcode form one sample.

### In either browser window

Choose **Fresh or FFPE** from specimen metadata before input discovery.

1. Choose threads, for example **4**, and your installed backend.
2. Keep reference download. Optional reports work for one sample; GISTIC requires two.
3. Click **Save configuration and check**, then **Run analysis** after checks pass.

Sample types here are example metadata. Saving/checking does not start analysis
or reference downloads. Use [the demo](browser_demo.md) to preview the interface.

## 3. Read the results

Open results from the browser, or find them under
`oncotracer-quickstart1/illumina/results/` and `oncotracer-quickstart1/ont/results/`:

| File | Purpose |
| --- | --- |
| `index.html` | Results dashboard |
| `06_workflow_summary/workflow_summary.txt` | Completion status |
| `06_workflow_summary/final_report.html` | Combined findings |
| `03_cna_codification/cna_events.tsv` | Copy-number changes |
| `04_cna_custom_plots/cna_per_sample_pages.pdf` | Sample plots |
| `.oncotracer-native/trace.tsv` | Recorded commands |

Next: [your own samples](setup.md) or [three-library QuickStart 2](public_cohort.md).

## Alternative: scripted setup and terminal run

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

## Optional: reuse prepared genome indexes

With the default choice, run downloads prebuilt indexes: about 8.0 GiB for
Illumina or 9.7 GiB for ONT. Completed downloads are reused.
Choose **Reuse a prepared OncoTracer reference** in the browser if one exists.

<details markdown="1">
<summary>Show commands that reuse an existing reference</summary>

Instead of step 2, select your reference parent or its
`references/samurai_hg38` folder. Illumina needs BWA; ONT needs minimap2.

### Illumina with an existing reference

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/illumina" \
  --mode illumina --input-folder "$PWD/oncotracer-quickstart1/input/illumina" \
  --hg38_build /data/shared-reference
```

### ONT with an existing reference

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/ont" \
  --mode ont --input-folder "$PWD/oncotracer-quickstart1/input/fastq_pass" \
  --hg38_build /data/shared-reference
```

### Same reference examples — Terminal / headless

Use step 2's names/settings; these terminal commands configure, check and run:

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --terminal --run --backend conda \
  --project "$PWD/oncotracer-quickstart1/illumina" --mode illumina \
  --input-folder "$PWD/oncotracer-quickstart1/input/illumina" \
  --hg38_build /data/shared-reference
```

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --terminal --run --backend conda \
  --project "$PWD/oncotracer-quickstart1/ont" --mode ont \
  --input-folder "$PWD/oncotracer-quickstart1/input/fastq_pass" \
  --hg38_build /data/shared-reference
```

</details>

For a local build, replace `--hg38_build /data/shared-reference` with
`--build_reference`; indexing needs more RAM, disk and time. Use one reference
choice. A bare `--hg38_build` selects automatic download. See [genome indexes](reference_indexes.md).

## Optional: run or resume from the terminal

**Skip if already running.** These use saved settings. Resume with `run`; omit `--force`.

<details markdown="1">
<summary>Show Illumina and ONT commands</summary>

### Illumina

```bash
cd /path/to/my/analyses_dir/
oncotracer check --config "$PWD/oncotracer-quickstart1/illumina/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/illumina/config/run.yml"
```

### ONT

```bash
cd /path/to/my/analyses_dir/
oncotracer check --config "$PWD/oncotracer-quickstart1/ont/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/ont/config/run.yml"
```

`--config` selects saved settings; `--backend conda` selects installed tools.
Use `--backend docker` or `--backend singularity` for the corresponding
[installed backend](containers.md).

</details>

For questions, add `--terminal` to step 2. Enter accepts the default:

```text
CNA bin size (kb) (1/5/10/15/30/50/100/500/1000) [100]:
```

QDNAseq defaults to 100 kb; ONT ichorCNA to 500 kb.
