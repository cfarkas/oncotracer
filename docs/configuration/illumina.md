# Illumina Configuration

Use this route for single-end or paired-end Illumina FASTQ files. OncoTracer aligns the reads, calls broad copy-number changes with SAMURAI/qDNAseq, refines CNA boundaries from the BAM files, creates tables and plots, and writes a run summary.

## Recommended: create the YAML automatically

Choose automatic setup when each sample has either one exact
`<sample>.fastq.gz`/`<sample>.fq.gz` file or one compressed R1/R2 pair in a
single folder. Use one layout consistently across the run.

### 1. Arrange the FASTQs

Place the reads in `illumina_fastq/`. The example below assumes the repository
is `/home/student/oncotracer`; replace that prefix if your clone is elsewhere.
The configuration and result folders are created automatically.

```bash
cd /home/student/oncotracer
find /home/student/oncotracer/project/input/illumina_fastq \
  -maxdepth 1 -type f -name '*.fastq.gz' -print | sort
```

The filenames must share the sample prefix:

```text
oncotracer/
└── project/
    └── input/
        └── illumina_fastq/
            ├── Patient_A_R1.fastq.gz
            ├── Patient_A_R2.fastq.gz
            ├── Patient_B_R1.fastq.gz
            ├── Patient_B_R2.fastq.gz
            ├── ctrl001_R1.fastq.gz
            ├── ctrl001_R2.fastq.gz
            ├── ctrl002_R1.fastq.gz
            ├── ctrl002_R2.fastq.gz
            ├── ctrl003_R1.fastq.gz
            ├── ctrl003_R2.fastq.gz
            ├── ctrl004_R1.fastq.gz
            └── ctrl004_R2.fastq.gz
```

`Patient_A_R1.fastq.gz` pairs with `Patient_A_R2.fastq.gz`. Names ending in
`_1.fastq.gz` and `_2.fastq.gz`, and the corresponding `.fq.gz` forms, are
also accepted. For a single-end run, use exact names such as
`Patient_A.fastq.gz` and `Patient_B.fastq.gz`. Automatic setup expects exactly
one supported input per sample at the top level of this folder and rejects a
mixed single-end/paired-end cohort.

### 2. Create the sample table

```bash
nano project/input/illumina_fastq/samples.csv                         # create the sample-to-status table
```

Enter the header and one row per sample:

```csv
sample_name,status
Patient_A,TUMOR
Patient_B,TUMOR
ctrl001,NORMAL
ctrl002,NORMAL
ctrl003,NORMAL
ctrl004,NORMAL
```

For paired data, `sample_name` must exactly match the filename text before
`_R1`/`_R2` (or `_1`/`_2`). For single-end data, it must exactly match the
filename without `.fastq.gz` or `.fq.gz`. `status` must be `TUMOR` or `NORMAL`
(case-insensitive). In Nano, save with `Ctrl+O`, press `Enter`, then exit with
`Ctrl+X`.

Automatic Setup counts the normal rows. Zero normals disables the local PoN;
exactly one is a configuration error; and two or more enable it. For an
enabled panel, the generated YAML lists the normal IDs in table order and sets
the minimum to that count. Here, all four controls are required. `NORMAL`
samples build the reference but are not included in corrected CNA output;
`TUMOR` samples are the reported cohort. Keep tumor and normal FASTQs in the
same single-end or paired-end layout.

Inspect the table:

```bash
sed -n '1,20p' project/input/illumina_fastq/samples.csv               # verify the header and rows
```

### 3. Generate the configuration

```bash
nextflow run main.nf --auto_params \
  --mode illumina \
  --reads_folder /home/student/oncotracer/project/input/illumina_fastq \
  --sample_table /home/student/oncotracer/project/input/illumina_fastq/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/illumina \
  --auto_outdir /home/student/oncotracer/project/runs/illumina_auto
```

This command checks that every row has exactly one single-end file or one R1/R2 pair, requires one layout for the whole run, and verifies every gzip stream. It creates:

```text
project/config/illumina/
├── auto_params_manifest.tsv   # mode, sample counts, and checksums
├── illumina.auto.yml          # pass this file to -params-file
└── illumina.samplesheet.csv   # detected single-end or R1/R2 paths
```

It does **not** start alignment or CNA analysis.

The samplesheet and manifest are published before the YAML, which is the final
transactional commit point for the runnable configuration.

### 4. Inspect the generated files

```bash
sed -n '1,120p' project/config/illumina/illumina.auto.yml              # inspect settings and absolute paths
sed -n '1,20p' project/config/illumina/illumina.samplesheet.csv        # inspect detected inputs and status values
sed -n '1,10p' project/config/illumina/auto_params_manifest.tsv        # inspect counts and checksums
```

The generated YAML will resemble this. It is a **YAML example**, not a terminal command:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/runs/illumina_auto
illumina_samplesheet: /home/student/oncotracer/project/config/illumina/illumina.samplesheet.csv
illumina_analysis_type: solid_biopsy
illumina_caller: qdnaseq
illumina_binsize_kb: 100
run_cna_classifier: false
illumina_build_pon: true
illumina_pon_normal_samples: "ctrl001,ctrl002,ctrl003,ctrl004"
illumina_pon_min_normals: 4
illumina_pon_name: ctrl001_ctrl002_ctrl003_ctrl004_PoN
illumina_pon_min_mapq: 37
illumina_pon_r_container: docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1
force: false
```

The generator chooses an absolute `lpwgs_root` that contains the reads,
generated configuration, and results. It emits all six PoN settings: the exact
quoted control list in table order, its count as the minimum, a reproducible
name made from sanitized control IDs plus `_PoN`, MAPQ `37`, and the pinned
qDNAseq container. With no controls it writes `illumina_build_pon: false`;
exactly one control stops before a YAML is published.

### 5. Run and inspect the summary

```bash
nextflow run main.nf --docker \
  -params-file /home/student/oncotracer/project/config/illumina/illumina.auto.yml \
  -resume
cat /home/student/oncotracer/project/runs/illumina_auto/06_workflow_summary/workflow_summary.txt
```

Use `--singularity` instead of `--docker` on a configured HPC system.

## Second option: manual setup

Choose manual setup when FASTQ naming does not match the supported automatic single-end/paired patterns or when you need advanced settings.

### 1. Create the samplesheet

This example assumes `pwd` prints `/home/student/oncotracer`. Replace that prefix everywhere if your clone is elsewhere.

```bash
cd oncotracer
mkdir -p project/input/illumina_fastq project/runs                     # create directories if absent
nano project/input/illumina.samplesheet.csv                            # create the FASTQ table
```

Enter:

```csv
sample,fastq_1,fastq_2,status
Patient_A,/home/student/oncotracer/project/input/illumina_fastq/Patient_A_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/Patient_A_R2.fastq.gz,tumor
Patient_B,/home/student/oncotracer/project/input/illumina_fastq/Patient_B_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/Patient_B_R2.fastq.gz,tumor
ctrl001,/home/student/oncotracer/project/input/illumina_fastq/ctrl001_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/ctrl001_R2.fastq.gz,normal
ctrl002,/home/student/oncotracer/project/input/illumina_fastq/ctrl002_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/ctrl002_R2.fastq.gz,normal
ctrl003,/home/student/oncotracer/project/input/illumina_fastq/ctrl003_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/ctrl003_R2.fastq.gz,normal
ctrl004,/home/student/oncotracer/project/input/illumina_fastq/ctrl004_R1.fastq.gz,/home/student/oncotracer/project/input/illumina_fastq/ctrl004_R2.fastq.gz,normal
```

Each row is one biological sample. `fastq_1` and `fastq_2` are absolute paths; `status` is `tumor` or `normal`. Save with `Ctrl+O`, press `Enter`, then exit with `Ctrl+X`.

For a single-end library, retain the four-column header and leave `fastq_2` empty:

```csv
sample,fastq_1,fastq_2,status
Patient_SE,/home/student/oncotracer/project/input/illumina_fastq/Patient_SE.fastq.gz,,tumor
```

One OncoTracer invocation must contain only one layout: all rows single-end or all rows paired-end. Mixed-layout samplesheets stop with an error because qDNAseq applies one paired-read setting to the run.

Check the table and files:

```bash
sed -n '1,20p' project/input/illumina.samplesheet.csv                 # inspect the saved CSV
ls -lh project/input/illumina_fastq/Patient_A_R1.fastq.gz             # confirm R1 exists and is not empty
ls -lh project/input/illumina_fastq/Patient_A_R2.fastq.gz             # confirm R2 exists and is not empty
gzip -t project/input/illumina_fastq/Patient_A_R1.fastq.gz            # no output means gzip is valid
gzip -t project/input/illumina_fastq/Patient_A_R2.fastq.gz            # test the mate too
```

### 2. Copy and edit the YAML

```bash
cp params/illumina.minimal.yml params/my_illumina.yml                  # preserve the versioned template
nano params/my_illumina.yml                                           # replace the example paths
```

For this tumor-plus-controls example, enable the local PoN explicitly:

```yaml
mode: illumina
lpwgs_root: /home/student/oncotracer
outdir: /home/student/oncotracer/project/runs/my_first_illumina_run
illumina_samplesheet: /home/student/oncotracer/project/input/illumina.samplesheet.csv
illumina_build_pon: true
illumina_pon_normal_samples: ctrl001,ctrl002,ctrl003,ctrl004
illumina_pon_min_normals: 4
illumina_pon_name: illumina_local_PoN
illumina_pon_min_mapq: 37
illumina_pon_r_container: docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1
force: false
```

The remaining settings use tested defaults: `solid_biopsy`, `qdnaseq`, and
`100` kb bins. OncoTracer writes the upstream results below
`outdir/01_samurai_illumina`; do not add a separate SAMURAI output path.

Save with `Ctrl+O`, press `Enter`, then exit with `Ctrl+X`. Inspect the result:

```bash
sed -n '1,120p' params/my_illumina.yml                                # verify every saved value
```

### How the local PoN is built

OncoTracer requires the explicit control list to contain every and only
samplesheet row marked `normal`, with no duplicates, and requires at least
`max(2, illumina_pon_min_normals)` usable controls. It processes all controls
with the same qDNAseq bin definition and uses the median normal log2 value at
every matched bin as the robust reference.
That reference is applied to each tumor independently. Corrected bins,
segments, combined tables, and plots contain only samples marked `tumor`.

The main audit files are:

- `01_samurai_illumina/logs/normal_panel_manifest.tsv`, the stable run-level
  copy of the exact controls used;
- `01_samurai_illumina/qdnaseq_local_pon/pon/normal_panel_manifest.tsv` and
  `pon/illumina_local_PoN.reference_bins.tsv`, the panel provenance and robust
  reference;
- `01_samurai_illumina/qdnaseq_local_pon/qc/normal_panel_sample_qc.tsv` and
  `qc/sample_qc.tsv`, leave-one-out normal stability against `N-1` controls and
  per-sample QC; and
- `01_samurai_illumina/qdnaseq_local_pon/qdnaseq_local_pon_summary.tsv`,
  `qdnaseq_local_pon_versions.tsv`, and `qdnaseq_local_pon.done`, the summary,
  software provenance, and validated completion marker.

Before panel generation starts, OncoTracer invalidates any prior
`qdnaseq_local_pon.done` marker. The helper writes the new marker last, after
its required outputs succeed, and the wrapper then validates the manifest and
tumor-only results. A failed run can leave partial files but cannot appear
complete. Require the new marker and the audit artifacts after the real run:

```bash
PON=project/runs/my_first_illumina_run/01_samurai_illumina/qdnaseq_local_pon
test "$(tr -d '\r\n' < "$PON/qdnaseq_local_pon.done")" = "QDNASEQ_LOCAL_PON_SUCCESS"
sed -n '1,12p' "$PON/pon/normal_panel_manifest.tsv"
sed -n '1,12p' "$PON/qc/normal_panel_sample_qc.tsv"
sed -n '1,12p' "$PON/qdnaseq_local_pon_summary.tsv"
find "$PON/bins" "$PON/segments" -maxdepth 1 -type f -print | sort
```

See [Output files](../outputs.md#illumina-local-panel-of-normals) for every
generated file and its interpretation.

### How to edit a YAML file from the terminal

The video below shows the same manual task: copy the example YAML, open it in Nano, replace the project root, samplesheet, and output paths, save with `Ctrl+O` and `Enter`, exit with `Ctrl+X`, inspect the saved YAML, perform a stub wiring check, and start the real run. The pauses are intentional so each edit can be followed.

<video controls preload="metadata" poster="../../assets/tutorial/edit_yaml_with_nano_poster.png" style="width:100%;max-width:960px">
  <source src="../../assets/tutorial/edit_yaml_with_nano.mp4" type="video/mp4">
  Your browser cannot play the embedded video. <a href="../../assets/tutorial/edit_yaml_with_nano.mp4">Open the MP4 video</a>.
</video>

### 3. Check wiring and run

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml # optional workflow-wiring check
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume   # real analysis
cat project/runs/my_first_illumina_run/06_workflow_summary/workflow_summary.txt # inspect final locations
```

## What each setting means

| Setting | Type and accepted value | Default | Purpose |
| --- | --- | --- | --- |
| `mode` | text: `illumina` | required | Selects the Illumina route. |
| `lpwgs_root` | absolute directory | site-specific | Common parent mounted into the container. Every input and output must be below it. |
| `outdir` | absolute directory | required | Results for this run. Use a new directory for a new experiment. |
| `illumina_samplesheet` | absolute CSV path | required | Four columns: `sample,fastq_1,fastq_2,status`; leave `fastq_2` empty for a single-end run. |
| `illumina_analysis_type` | text: `solid_biopsy` | `solid_biopsy` | Standard SAMURAI analysis preset for this route. |
| `illumina_caller` | text: `qdnaseq` | `qdnaseq` | CNA caller used by the current Illumina workflow. |
| `illumina_binsize_kb` | positive integer, kb | `100` | Width of the initial copy-number bins. |
| `run_cna_classifier` | Boolean | `false` | Adds classifier/pathology outputs when `true`. |
| `illumina_build_pon` | Boolean | `false` | Enables local qDNAseq normal-reference construction and tumor correction. |
| `illumina_pon_normal_samples` | comma-separated exact sample IDs or `null` | `null` | Must contain every and only samplesheet ID marked `normal`, once each; missing, extra, or duplicate IDs fail validation. |
| `illumina_pon_min_normals` | integer greater than or equal to `2` | `2` | Minimum number of selected controls required to start panel construction. |
| `illumina_pon_name` | letters, numbers, `.`, `_`, or `-` | `illumina_local_PoN` | Names the generated local panel and reference artifacts. |
| `illumina_pon_min_mapq` | non-negative integer | `37` | Minimum alignment mapping quality for panel and tumor qDNAseq processing. |
| `illumina_pon_r_container` | container URI | `docker://quay.io/dincalcilab/qdnaseq:1.30.0-a28ebc1` | Reproducible R/qDNAseq runtime for the local-PoN helper. |
| `force` | Boolean | `false` | Requests supported refresh behavior. Keep `false` for real runs. |

For all optional settings, see the [parameter reference](parameter_reference.md).
