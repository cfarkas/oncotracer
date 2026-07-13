# Illumina YAML

This page explains the file you pass after `-params-file`. The YAML does not contain sequencing reads. It contains settings and absolute paths that tell OncoTracer where the paired FASTQ samplesheet is, where SAMURAI/qDNAseq should write, and where the final numbered outputs belong.

## Recommended folder layout

You can keep the editable YAML in `params/` and your small input tables in a project folder inside the clone. FASTQ files may also live elsewhere, but every path must be visible below `lpwgs_root` when Docker or Singularity runs.

```text
oncotracer/
├── main.nf
├── params/
│   ├── illumina.minimal.yml     # versioned template
│   └── my_illumina.yml          # your copied and edited YAML
└── project/
    ├── input/
    │   └── illumina.samplesheet.csv
    └── runs/
        └── sample_a/            # created by the workflow
```

## Create and edit the YAML

```bash
git clone https://github.com/cfarkas/oncotracer.git  # clone the repository
cd oncotracer                                        # enter the repository
mkdir -p project/input project/runs                  # create a simple project tree
cp params/illumina.minimal.yml params/my_illumina.yml # copy the template; preserve the original
realpath .                                           # print the absolute clone path
nano params/my_illumina.yml                          # edit the copied YAML
```

Save with `Ctrl+O`, press Enter, then exit with `Ctrl+X`. Validate and run.

### Video: a human-style nano editing session

This real example pauses on each edit so you can follow it. It shows a user opening `nano`, replacing absolute paths, saving with `Ctrl+O`, exiting with `Ctrl+X`, and starting the complete Illumina run with the edited YAML.

<video controls preload="metadata" poster="../../assets/tutorial/edit_yaml_with_nano_poster.png" style="width:100%;max-width:960px">
  <source src="../../assets/tutorial/edit_yaml_with_nano.mp4" type="video/mp4">
  Your browser cannot play the embedded video. <a href="../../assets/tutorial/edit_yaml_with_nano.mp4">Download the MP4</a>.
</video>

## What the YAML means

```yaml
mode: illumina                                      # choose the Illumina branch
lpwgs_root: /home/user/oncotracer                   # common parent mounted into Docker/Singularity
outdir: /home/user/oncotracer/project/runs/sample_a # final numbered OncoTracer outputs
illumina_samplesheet: /home/user/oncotracer/project/input/illumina.samplesheet.csv # paired FASTQ table
illumina_samurai_outdir: /home/user/oncotracer/project/runs/sample_a/01_samurai_illumina # qDNAseq/SAMURAI output
illumina_analysis_type: solid_biopsy                # SAMURAI analysis preset
illumina_caller: qdnaseq                            # Illumina CNA caller
illumina_binsize_kb: 100                            # coarse qDNAseq bin size
run_cna_classifier: false                           # optional classifier/pathology stage
force: false                                        # preserve existing outputs unless intentionally refreshing
```

The first five lines answer: which branch, which container-visible root, where final results go, where samples are listed, and where upstream qDNAseq results go. The remaining lines select established defaults and optional stages.

## Create the samplesheet

Open a new file:

```bash
nano project/input/illumina.samplesheet.csv          # create the paired FASTQ table
```

Enter:

```csv
sample,fastq_1,fastq_2,status
Sample_A,/home/user/oncotracer/project/input/Sample_A_R1.fastq.gz,/home/user/oncotracer/project/input/Sample_A_R2.fastq.gz,tumor
```

Save with `Ctrl+O`, press Enter, then exit with `Ctrl+X`. Validate and run. First check every file:

```bash
realpath project/input/Sample_A_R1.fastq.gz          # confirm the R1 absolute path
realpath project/input/Sample_A_R2.fastq.gz          # confirm the R2 absolute path
head -5 project/input/illumina.samplesheet.csv       # confirm the CSV header and sample row
```

## Validate and run

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml # validate paths/settings without analysis
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume   # run the complete analysis
cat project/runs/sample_a/06_workflow_summary/workflow_summary.txt          # inspect final output locations
```

| Field | Required? | Meaning |
| --- | --- | --- |
| `mode` | Yes | Must be `illumina`. |
| `lpwgs_root` | Yes | Absolute common parent mounted into the runtime. |
| `outdir` | Yes | Main numbered-output directory. |
| `illumina_samplesheet` | Yes | CSV with `sample,fastq_1,fastq_2,status`. |
| `illumina_samurai_outdir` | Yes | SAMURAI/qDNAseq output directory. |
| `illumina_analysis_type` | Default | Usually `solid_biopsy`. |
| `illumina_caller` | Default | `qdnaseq`. |
| `illumina_binsize_kb` | Default | `100`. |
| `run_cna_classifier` | Optional | Enables classifier/report/pathology steps. |
| `force` | Optional | Allows supported refresh behavior. Prefer `false` for real projects. |
