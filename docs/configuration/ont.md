# ONT Configuration

Use this route for Oxford Nanopore FASTQ files organized in barcode directories. OncoTracer merges reads per selected barcode, aligns them, calls broad copy-number changes with SAMURAI/ichorCNA, refines CNA boundaries from the BAM files, and creates tables, plots, and a run summary.

## Recommended: create the YAML automatically

Automatic setup is safest because it checks each barcode folder and writes the comma-separated barcode/sample lists in matching order.

### 1. Arrange the FASTQs

Point OncoTracer to the `fastq_pass` directory, not to an individual barcode directory:

```text
oncotracer/
└── project/
    └── input/
        └── fastq_pass/
            ├── barcode01/
            │   ├── reads_001.fastq.gz
            │   └── reads_002.fastq.gz
            └── barcode02/
                └── reads_001.fastq.gz
```

Each barcode can contain one or more `.fastq`, `.fq`, `.fastq.gz`, or `.fq.gz` files directly inside it. For an unbarcoded run, create a directory such as `barcode01` and put the FASTQs inside it.

Inspect the reads already placed in the project. The example below assumes the
repository is `/home/student/oncotracer`; replace that prefix if your clone is
elsewhere. The configuration and result folders are created automatically.

```bash
cd /home/student/oncotracer
find /home/student/oncotracer/project/input/fastq_pass \
  -maxdepth 2 -type d -print | sort
find /home/student/oncotracer/project/input/fastq_pass \
  -maxdepth 2 -type f -print | sort | head -20
```

### 2. Create an explicit barcode table

```bash
nano project/input/fastq_pass/samples.csv                             # create barcode-to-sample metadata
```

Enter:

```csv
barcode,sample_name,status
barcode01,Patient_A,TUMOR
barcode02,Patient_B,NORMAL
```

- `barcode` must exactly match a directory name.
- `sample_name` becomes the biological label in the outputs.
- `status` must be `TUMOR` or `NORMAL` (case-insensitive).
- At least one row must be `TUMOR`.

Save with `Ctrl+O`, press `Enter`, then exit with `Ctrl+X`. Inspect the file:

```bash
sed -n '1,20p' project/input/fastq_pass/samples.csv                    # verify the header and mappings
```

### 3. Generate the configuration

```bash
nextflow run main.nf --auto_params \
  --mode ont \
  --reads_folder /home/student/oncotracer/project/input/fastq_pass \
  --sample_table /home/student/oncotracer/project/input/fastq_pass/samples.csv \
  --auto_config_dir /home/student/oncotracer/project/config/ont \
  --auto_outdir /home/student/oncotracer/project/runs/ont_auto
```

Automatic setup verifies that each listed barcode exists, contains FASTQs, and has no empty or incomplete gzip file. It writes `project/config/ont/ont.auto.yml`; it does **not** start alignment or CNA analysis.

### 4. Inspect the generated YAML

```bash
sed -n '1,160p' project/config/ont/ont.auto.yml                        # inspect all generated settings
```

For the table above, the file will resemble this. It is a **YAML example**, not a terminal command:

```yaml
mode: ont
lpwgs_root: /home/student/oncotracer/project
outdir: /home/student/oncotracer/project/runs/ont_auto
ont_folder: /home/student/oncotracer/project/input/fastq_pass
ont_barcodes: barcode01
ont_sample_names: Patient_A
ont_analysis_type: liquid_biopsy
ont_caller: ichorcna
ont_binsize_kb: 500
ont_min_age_minutes: 0
run_cna_classifier: false
force: false
ont_normal_folder: /home/student/oncotracer/project/input/fastq_pass
ont_normal_barcodes: barcode02
ont_normal_sample_names: Patient_B
```

Tumor and normal barcodes are written to separate settings. Every comma-separated list is positional: the first barcode maps to the first sample name. Your absolute root will differ from `/home/student/oncotracer`.

### 5. Run and inspect the summary

```bash
nextflow run main.nf --docker \
  -params-file /home/student/oncotracer/project/config/ont/ont.auto.yml \
  -resume
cat /home/student/oncotracer/project/runs/ont_auto/06_workflow_summary/workflow_summary.txt
```

Use `--singularity` instead of `--docker` on a configured HPC system.

## Second option: manual setup

Choose manual setup when you need to select only some barcodes, use a custom reference, or configure normal/control data explicitly.

### 1. Inspect the input tree

This example assumes `pwd` prints `/home/student/oncotracer`. Replace that prefix if your clone is elsewhere.

```bash
cd oncotracer
find project/input/fastq_pass -maxdepth 2 -type d -print | sort        # list barcode directories
find project/input/fastq_pass -maxdepth 2 -type f -print | sort | head -20 # inspect FASTQs
ls -lh project/input/fastq_pass/barcode01                             # confirm the first barcode is not empty
```

### 2. Copy and edit the YAML

```bash
cp params/ont.minimal.yml params/my_ont.yml                            # preserve the versioned template
nano params/my_ont.yml                                                # replace paths, barcodes, and names
```

A minimal two-tumor configuration is:

```yaml
mode: ont
lpwgs_root: /home/student/oncotracer
outdir: /home/student/oncotracer/project/runs/my_first_ont_run
ont_folder: /home/student/oncotracer/project/input/fastq_pass
ont_barcodes: barcode01,barcode02
ont_sample_names: Patient_A,Patient_B
force: false
```

The first barcode maps to the first sample name; list lengths must match. The standard defaults are `liquid_biopsy`, `ichorcna`, `500` kb bins, and a minimum file age of `0` minutes. OncoTracer automatically writes upstream results to `outdir/01_samurai_ont`; do not add a separate SAMURAI output path.

For a normal/control barcode in the same `fastq_pass` tree, add:

```yaml
ont_normal_folder: /home/student/oncotracer/project/input/fastq_pass
ont_normal_barcodes: barcode03
ont_normal_sample_names: Patient_Normal
```

Providing normal inputs activates the wrapper's local panel-of-normals route. Review the study design before combining controls from different runs.

Save with `Ctrl+O`, press `Enter`, then exit with `Ctrl+X`. Inspect the saved file:

```bash
sed -n '1,160p' params/my_ont.yml                                    # verify paths and positional lists
```

### 3. Check wiring and run

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_ont.yml # optional workflow-wiring check
nextflow run main.nf --docker -params-file params/my_ont.yml -resume   # real analysis
cat project/runs/my_first_ont_run/06_workflow_summary/workflow_summary.txt # inspect final locations
```

## What each setting means

| Setting | Type and accepted value | Default | Purpose |
| --- | --- | --- | --- |
| `mode` | text: `ont` | required | Selects the Oxford Nanopore route. |
| `lpwgs_root` | absolute directory | site-specific | Common parent mounted into the container. Every configured path must be below it. |
| `outdir` | absolute directory | required | Results for this run. Use a new directory for a new experiment. |
| `ont_folder` | absolute directory | required | Parent containing barcode directories. |
| `ont_barcodes` | comma-separated directory names | required | Tumor barcode selection. |
| `ont_sample_names` | comma-separated names | `null` | Biological names in the same order; strongly recommended. |
| `ont_analysis_type` | `liquid_biopsy` or `solid_biopsy` | `liquid_biopsy` | SAMURAI analysis preset. Keep the standard default unless the study design requires another supported route. |
| `ont_caller` | text: `ichorcna` | `ichorcna` | CNA caller required by the current downstream ONT route. |
| `ont_binsize_kb` | positive integer, kb | `500` | Width of the initial copy-number bins. |
| `ont_min_age_minutes` | non-negative integer, minutes | `0` | Use `0` for completed data; a positive delay helps avoid files still being written. |
| `ont_ref` | absolute FASTA path or `null` | `null` | Optional custom reference below `lpwgs_root`. |
| `ont_normal_*` | path and positional lists | `null` | Optional normal/control inputs. Supply the folder and barcode list together. |
| `ont_build_pon` | Boolean | `false` | Explicitly request the supported panel-of-normals route. Normal inputs also trigger the wrapper's default local PoN behavior. |
| `ont_force_realign` | Boolean | `false` | Recreate supported alignments instead of reusing them. |
| `force` | Boolean | `false` | Requests supported refresh behavior. Keep `false` for real runs. |

For all optional settings, see the [parameter reference](parameter_reference.md).
