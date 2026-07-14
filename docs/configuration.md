# Choose how to configure a run

You normally need **one run YAML**, not every configuration page. Use this page as a route selector.

## Which route should I choose?

| Your goal | Start here | What you will create/use |
| --- | --- | --- |
| Verify that OncoTracer works on this computer | [Quick verification](quick_start.md) | Public Illumina and ONT data plus generated test YAMLs |
| Run the real three-library/six-FASTQ cohort | [Three-sample public cohort](public_cohort.md) | Download manifest, automatic YAML, full cohort results |
| Run your own folder with standard names | [Automatic setup](auto_params.md) | A small sample table; OncoTracer generates YAML/samplesheet |
| Write an Illumina YAML manually | [Illumina YAML example](configuration/illumina.md) | Paired-end samplesheet plus one copied YAML |
| Write an ONT YAML manually | [ONT YAML example](configuration/ont.md) | Barcode folders/mapping plus one copied YAML |
| Add CNA classifier/pathology comparison | [Pathology and classifier](configuration/pathology.md) | Extra keys in the same Illumina YAML plus matched pathology CSV |
| Change boundary-refinement behavior | [Advanced refinement](configuration/refinement.md) | Carefully justified changes in the same run YAML |
| Look up one field/default | [All parameters](configuration/parameter_reference.md) | Reference only; do not copy every option |

If this is your first run with your own data, use automatic setup. Manual YAML is useful when filenames do not follow the expected patterns or when you need non-default options.

## One YAML controls one run

A YAML is a plain-text list of `key: value` settings. It points to input files and the output directory; it does not contain FASTQ data.

```yaml
mode: illumina
lpwgs_root: /home/user/oncotracer/project
outdir: /home/user/oncotracer/project/runs/sample_a
illumina_samplesheet: /home/user/oncotracer/project/input/illumina.samplesheet.csv
```

All three paths in this example belong to the same project root. See [YAML examples and path rules](configuration/yaml_basics.md) before editing paths.

Pathology is not a second YAML format. Copy the Illumina pathology template and use that single file for the Illumina run, classifier settings, and pathology column mapping.

## Safe manual workflow

### 1. Copy a template

```bash
cd oncotracer                                                   # run main.nf from the repository
cp params/illumina.minimal.yml params/my_illumina.yml           # Illumina
# or: cp params/ont.minimal.yml params/my_ont.yml                # ONT
```

Never edit the versioned template directly; a later `git pull` should not overwrite your run settings.

### 2. Resolve and check paths

```bash
realpath .
realpath project
realpath project/input
```

Use those absolute paths in the YAML. Keep inputs and outputs under `lpwgs_root` so the container can access them.

### 3. Edit

```bash
nano params/my_illumina.yml
```

Move with arrow keys, edit the value after each colon, save with `Ctrl+O`, press Enter, then exit with `Ctrl+X`. Do not use tabs. Do not remove a key unless its page says it is optional.

### 4. Validate wiring

```bash
nextflow run main.nf -stub-run --docker -params-file params/my_illumina.yml
```

A successful stub creates placeholders and proves that parameters/channels connect. It does **not** analyze reads or validate scientific output.

### 5. Run for real

```bash
nextflow run main.nf --docker -params-file params/my_illumina.yml -resume
```

Use the same YAML and command when resuming. Read [Output files](outputs.md) after completion and [Troubleshooting](troubleshooting.md) if a task fails.

## Settings beginners should usually leave alone

Keep caller, analysis type, binsize, and refinement defaults from the relevant template for the first successful run. Do not add internal SAMURAI output paths: OncoTracer derives stage 01 from `outdir` automatically.

Do not edit `nextflow.config` for a normal analysis. A run-specific YAML is easier to archive, compare, and reproduce.

## Runtime flag

Use exactly one:

```text
--docker       workstation/server with Docker
--singularity  supported HPC container runtime
--conda        fallback without containers
```

See [Containers and execution environments](containers.md) for mounts, permissions, caches, and reproducible image identity.
