# ONT YAML

This YAML tells OncoTracer where an Oxford Nanopore `fastq_pass` tree is, which barcode directories belong to which sample names, where SAMURAI/ichorCNA should write, and where final OncoTracer outputs belong.

## Recommended folder layout

```text
oncotracer/
├── main.nf
├── params/
│   ├── ont.minimal.yml        # versioned template
│   └── my_ont.yml             # your copied and edited YAML
└── project/
    ├── input/
    │   └── fastq_pass/
    │       ├── barcode01/
    │       │   └── reads.fastq.gz
    │       └── barcode02/
    │           └── reads.fastq.gz
    └── runs/
        └── ont_run/            # created by the workflow
```

## Create and edit the YAML

```bash
git clone https://github.com/cfarkas/oncotracer.git # clone the repository
cd oncotracer                                       # enter the repository
mkdir -p project/input/fastq_pass project/runs      # create a simple project tree
cp params/ont.minimal.yml params/my_ont.yml         # copy the template; preserve the original
find project/input/fastq_pass -maxdepth 2 -type f | head # inspect barcode FASTQ placement
realpath project/input/fastq_pass                   # obtain the absolute ONT folder path
nano params/my_ont.yml                              # edit the copied YAML
```

In `nano`, use arrow keys to move and replace the example paths and barcode/sample lists. Save with `Ctrl+O`, press Enter, then exit with `Ctrl+X`. Validate and run.

## What the YAML means

```yaml
mode: ont                                           # choose the Oxford Nanopore branch
lpwgs_root: /home/user/oncotracer                   # common parent mounted into Docker/Singularity
outdir: /home/user/oncotracer/project/runs/ont_run  # final numbered OncoTracer outputs
ont_folder: /home/user/oncotracer/project/input/fastq_pass # folder containing barcode directories
ont_barcodes: barcode01,barcode02                   # barcode directory names, in order
ont_sample_names: Patient_A,Patient_B               # biological names in the same order
ont_samurai_outdir: /home/user/oncotracer/project/runs/ont_run/01_samurai_ont # ichorCNA/SAMURAI output
ont_analysis_type: liquid_biopsy                    # SAMURAI analysis preset
ont_caller: ichorcna                                # ONT CNA caller
ont_binsize_kb: 500                                 # coarse ichorCNA bin size
ont_min_age_minutes: 0                              # process completed FASTQ immediately
run_cna_classifier: false                           # optional classifier/report stage
force: false                                        # preserve existing outputs unless intentionally refreshing
```

The lists are positional: `barcode01` becomes `Patient_A`, and `barcode02` becomes `Patient_B`. The list lengths must match.

## Check the input before running

```bash
find project/input/fastq_pass -maxdepth 2 -type d | sort # list barcode directories
find project/input/fastq_pass -maxdepth 2 -type f | head # show example FASTQ files
ls -lh project/input/fastq_pass/barcode01                # confirm barcode01 is not empty
```

For a single unbarcoded folder, the wrapper still needs a barcode/sample selection consistent with the documented layout. The public tutorial shows a complete single-barcode example.

## Validate and run

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_ont.yml # validate settings without analysis
nextflow run main.nf --docker -params-file params/my_ont.yml -resume   # run the complete ONT analysis
cat project/runs/ont_run/06_workflow_summary/workflow_summary.txt      # inspect final output locations
```

| Field | Required? | Meaning |
| --- | --- | --- |
| `mode` | Yes | Must be `ont`. |
| `lpwgs_root` | Yes | Absolute common parent mounted into the runtime. |
| `outdir` | Yes | Main numbered-output directory. |
| `ont_folder` | Yes | Folder containing barcode FASTQ directories. |
| `ont_barcodes` | Yes | Comma-separated barcode IDs. |
| `ont_sample_names` | Recommended | Comma-separated names matching barcode order. |
| `ont_samurai_outdir` | Yes | SAMURAI/ichorCNA output directory. |
| `ont_analysis_type` | Default | Usually `liquid_biopsy`. |
| `ont_caller` | Default | `ichorcna`. |
| `ont_binsize_kb` | Default | `500`. |
| `ont_min_age_minutes` | Optional | Use `0` for completed FASTQ; positive values avoid files still being written. |
| `ont_ref` | Optional | Reference FASTA path. |
| `ont_normal_folder` | Optional | Normal/control FASTQ folder. |
| `ont_normal_barcodes` | Optional | Normal/control barcode IDs. |
| `ont_normal_sample_names` | Optional | Matching normal/control names. |
| `ont_build_pon` | Optional | Build a panel of normals when supported. |
| `ont_force_realign` | Optional | Force ONT realignment. |
| `force` | Optional | Allows supported refresh behavior. Prefer `false` for real projects. |
