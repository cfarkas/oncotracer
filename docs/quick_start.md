<a id="quick-start"></a>

# QuickStart 1: Illumina and ONT

Run a complete native analysis using one public Illumina library and one ONT
library. Reads total approximately 225 MB; allow space for tools, references and results. This is copy-number analysis, not methylation:
FASTQ files do not contain methylation calls.

[Install OncoTracer](installation.md) first. These examples use the same
`setup`, `check` and `run` commands as your own samples.

## 1. Download the reads

Replace `/path/to/my/analyses_dir/` with your writable analysis directory.
Use that directory in every block; `$PWD` is its absolute path.

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

`--output` names the downloaded file; `--continue-at -` resumes an interrupted
download. Continue only when all three checksum lines say `OK`.

The ONT library uses `barcode01` as its sample folder.

## 2. Set up interactively (recommended)

Choose Illumina or ONT, or follow both using separate project folders.
Each command opens browser setup at **127.0.0.1:8888** with paths prefilled and
samples discovered. If needed, open the complete printed URL. Keep the terminal
open; stop it with Ctrl+C before opening the other example.

### Illumina

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/illumina" \
  --mode illumina --input-folder "$PWD/oncotracer-quickstart1/input/illumina"
```

1. Drag `ERR12341627` into **Cancer**, or use its dropdown. Edit its name as needed.
2. Keep `cna`, review CPU/RAM/GPU information, and choose threads, for example `4`.
3. Keep **100 kb (default)** bins. Optional CNA reports and local models work for
   one sample; **GISTIC is disabled** because it needs two.
4. Choose Conda and reference download. Click **Save configuration and check**, then
   **Run analysis**, or use the Illumina commands in step 3.

### ONT

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --project "$PWD/oncotracer-quickstart1/ont" \
  --mode ont --input-folder "$PWD/oncotracer-quickstart1/input/fastq_pass"
```

1. Drag `barcode01` into **Cancer** and edit its name to `DRR165691`.
   All FASTQs within this barcode are one sample.
2. Keep analysis `cna`, review hardware, and choose threads, for example `4`.
3. Keep caller `ichorcna` (**500 kb bins**).
4. Choose Conda and reference download, then **Save configuration and check**.
   Click **Run analysis**, or leave it saved and use the ONT commands in step 3.
   GISTIC is unavailable for this single sample; optional CNA reports still work.

Sample types here are example metadata. Setup saves `config/run.yml` and sample
metadata without starting analysis or downloads. Existing configurations are protected.

### Prefer terminal prompts?

Add `--terminal` to either command above. Press Enter to accept a displayed default:

```text
CNA bin size (kb) (1/5/10/15/30/50/100/500/1000) [100]:
```

Press Enter for `[100]`: **100 kb**. ONT ichorCNA
uses 500 kb instead. Finish with `save` or `run`. Single-sample projects skip GISTIC.

For scripts, [supply answers with `--non-interactive`](setup.md#optional-scripted-setup-without-prompts).
[Your own samples](setup.md) covers normal roles, custom tags and methylation.

## Optional: reuse prepared genome indexes

With the default download choice, run downloads prebuilt indexes automatically: about 8.0 GiB for Illumina
and 9.7 GiB for ONT, under each project's `reference/` folder. Completed downloads
are reused. **No reference flag is needed** for the steps above.

If a prepared reference already exists at `/data/shared-reference`, use one of these
commands **instead of the corresponding step 2 command**, before creating its config:

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

The browser prefills the reference. Assign samples, save and run.
Replace `/data/shared-reference` with your prepared reference parent or its
`references/samurai_hg38` folder. Illumina needs BWA indexes; ONT needs minimap2.

`--hg38_build` **without a path** selects automatic download. In the browser,
choosing **Reuse a prepared OncoTracer reference** and browsing to that folder has the same effect
as supplying that path in these commands.
To build locally, replace `--hg38_build /data/shared-reference` in either example
with `--build_reference`. Run then downloads source files as needed and builds
indexes on CPU. Choose one option. Local indexing needs more RAM, temporary disk
and time. See [reference details](reference_indexes.md) and
[RAM requirements](installation.md#requirements).

## 3. Check and run

Use the block matching the project you configured. Resolve any check errors before
running. `--config` selects saved settings; `--backend conda` selects installed
analysis tools.

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

Docker and Apptainer use `--backend docker` and `--backend singularity`, after
[installing that backend](containers.md).

## 4. Read the results

Results are under `oncotracer-quickstart1/illumina/results/` and
`oncotracer-quickstart1/ont/results/`:

| File | Purpose |
| --- | --- |
| `index.html` | Results dashboard and stage indexes |
| `06_workflow_summary/final_report.html` | Combined findings |
| `06_workflow_summary/workflow_summary.txt` | Completion status |
| `03_cna_codification/cna_events.tsv` | Copy-number changes |
| `04_cna_custom_plots/cna_per_sample_pages.pdf` | Plots |
| `.oncotracer-native/trace.tsv` | Recorded commands |

After fixing errors, resume with the same `run` command; omit setup and `--force`.

Next: [your own samples](setup.md) or [QuickStart 2](public_cohort.md).
