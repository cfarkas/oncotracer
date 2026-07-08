# Running OncoTracer

## Basic Command Pattern

Native/Conda fallback:

```bash
nextflow run main.nf --conda \
  -params-file params/<your_config>.yml \
  -resume
```

Nextflow Docker executor:

```bash
nextflow run main.nf --docker \
  -params-file params/<your_config>.yml \
  -resume
```

Nextflow Singularity/Apptainer executor:

```bash
nextflow run main.nf --singularity \
  -params-file params/<your_config>.yml \
  -resume
```

Use `--docker` for Docker, `--singularity` for Singularity/Apptainer, or `--conda` for the native Conda fallback. Use `-resume` whenever possible. It lets Nextflow reuse completed processes after an interrupted run.

The `--docker` and `--singularity` flags include the needed default container settings internally. New users should not need to add `--docker_run_options` or `--singularity_run_options`.

## ONT From FASTQ/Barcodes

Edit `params/ont.example.yml`, then run:

```bash
nextflow run main.nf -profile local \
  -params-file params/ont.example.yml \
  -resume
```

This can run:

```text
01_samurai_ont -> 02_bam_refinement -> 03_cna_codification -> 04_cna_custom_plots
```

If `run_cna_classifier: true`, it also runs:

```text
05_cna_classifier
```

## ONT From Existing ichorCNA/SAMURAI Outputs

Edit `params/ont.from_existing_samurai.example.yml`, then run:

```bash
nextflow run main.nf -profile local \
  -params-file params/ont.from_existing_samurai.example.yml \
  -resume
```

This skips `01_samurai_ont` and starts from BAM refinement.

## Illumina From qDNAseq/SAMURAI Outputs

Edit `params/illumina.example.yml`, then run:

```bash
nextflow run main.nf -profile local \
  -params-file params/illumina.example.yml \
  -resume
```

## Run With Classifier and Reports

```bash
nextflow run main.nf -profile local \
  -params-file params/illumina.example.yml \
  --run_cna_classifier true \
  -resume
```

## Run With Pathology Concordance

```bash
nextflow run main.nf -profile local \
  -params-file params/illumina.example.yml \
  --run_cna_classifier true \
  --pathology_csv /path/to/pathology.csv \
  --pathology_sample_col illumina_sample_id \
  --pathology_case_col case_code \
  --pathology_diagnosis_col final_diagnosis \
  -resume
```

## Fast CNA-Only Run

Disable classifier/reporting:

```bash
nextflow run main.nf -profile local \
  -params-file params/illumina.example.yml \
  --run_cna_classifier false \
  -resume
```

This still produces refined segments, CNA event tables, and custom plots.

## Strict Offline/Deterministic Classifier Runs

The top-level wrapper exposes pathology model toggles. Additional classifier settings can be edited in `bin/cna_classifier_nf/nextflow.config` or surfaced as needed.

Useful model-disabling options inside the classifier are:

```text
knowledge_literature_llm = false
knowledge_deep_enable_llm_ranker = false
pathology_use_biomed_models = false
```

These reduce dependence on Hugging Face model loading and network state.


## Standalone Docker Entrypoint

The Docker image can run the workflow without installing Nextflow on the host:

```bash
docker run --rm -it \
  -v /media/server/STORAGE/LPWGS_2025:/media/server/STORAGE/LPWGS_2025 \
  carlosfarkas/oncotracer:latest \
  -profile local \
  -params-file /media/server/STORAGE/LPWGS_2025/OncoTracer/params/illumina.example.yml \
  -resume
```

All paths in your YAML must be visible inside the container through `-v` mounts.
