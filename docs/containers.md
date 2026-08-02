# Execution environments

Nextflow runs OncoTracer with one execution option per analysis command.

| Environment | Option | What Nextflow uses |
| --- | --- | --- |
| Linux with Miniforge or Conda | `--conda` | Conda environments created automatically from the versioned environment definitions |
| Linux workstation or server with Docker | `--docker` | [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer) |
| HPC with Singularity or Apptainer | `--singularity` | `docker://carlosfarkas/oncotracer:latest` |

Use `/path/to/my/directory/oncotracer` as the repository path in these examples.

## Conda

Install [Miniforge](https://github.com/conda-forge/miniforge) or another compatible Conda distribution, then run:

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Create or reuse the required Conda environments and test the software.
nextflow run "$REPO_DIR/main.nf" --install --conda \
  --lpwgs_root "$REPO_DIR/project"

# Optionally check a YAML without running the scientific tools.
nextflow run "$REPO_DIR/main.nf" -stub-run --conda \
  -params-file "$REPO_DIR/params/my_run.yml"

# Run or resume the analysis with Conda.
nextflow run "$REPO_DIR/main.nf" --conda \
  -params-file "$REPO_DIR/params/my_run.yml" \
  -resume
```

Nextflow creates and reuses Conda environments automatically. The first command solves and creates the top-level environment, which is cached below `lpwgs_root/.oncotracer/conda`; the nested SAMURAI workflow also creates and reuses its required Conda environments.

## Docker

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Pull or reuse the Docker image and test the installed software.
nextflow run "$REPO_DIR/main.nf" --install --docker \
  --lpwgs_root "$REPO_DIR/project"

# Optionally check a YAML without running the scientific tools.
nextflow run "$REPO_DIR/main.nf" -stub-run --docker \
  -params-file "$REPO_DIR/params/my_run.yml"

# Run or resume the analysis.
nextflow run "$REPO_DIR/main.nf" --docker \
  -params-file "$REPO_DIR/params/my_run.yml" \
  -resume
```

Nextflow pulls or reuses `carlosfarkas/oncotracer:latest` as needed. Use `--docker` on the OncoTracer command; do not replace it with `-profile docker`.

## Singularity or Apptainer

```bash
# Confirm which HPC launcher is available.
command -v singularity
command -v apptainer
```

Then use `--singularity`:

```bash
# Set the standard repository path and enter it.
REPO_DIR=/path/to/my/directory/oncotracer
cd "$REPO_DIR"

# Pull or reuse docker://carlosfarkas/oncotracer:latest and test the software.
nextflow run "$REPO_DIR/main.nf" --install --singularity \
  --lpwgs_root "$REPO_DIR/project"

# Optionally check a YAML without running the scientific tools.
nextflow run "$REPO_DIR/main.nf" -stub-run --singularity \
  -params-file "$REPO_DIR/params/my_run.yml"

# Run or resume the analysis on HPC.
nextflow run "$REPO_DIR/main.nf" --singularity \
  -params-file "$REPO_DIR/params/my_run.yml" \
  -resume
```

Use a Singularity/Apptainer cache directory on a filesystem with enough quota. Cluster scheduler and bind-mount rules still apply.

## Project paths

Keep the YAML inputs, reference/cache, work directory, and results below a common project root:

```yaml
lpwgs_root: /path/to/my/directory/oncotracer/project
outdir: /path/to/my/directory/oncotracer/project/results
illumina_samplesheet: /path/to/my/directory/oncotracer/project/config/illumina.samplesheet.csv
```

For container runs, a path outside `lpwgs_root` may not be visible inside the container. For Conda runs, a common project root keeps environments, references, work files, and outputs together.

## File ownership with Docker

The default container user is `1000:1000`. When the host uses different numeric IDs, record them:

```bash
# Print the host user and group IDs.
id -u
id -g
```

Then add the matching value to the YAML when required:

```yaml
docker_user: "1234:1234"
```

## Record the environment identity

```bash
# Set the standard repository path.
REPO_DIR=/path/to/my/directory/oncotracer

# Read the environment or image identity recorded by the installation check.
cat "$REPO_DIR/.oncotracer/install/install_manifest.txt"
```

For a formal analysis, preserve the OncoTracer commit, YAML, generated samplesheet, installation manifest, and nested `pipeline_info` files. Use an approved immutable image digest or explicit Conda package specification when the study requires a frozen runtime.

## Cache and storage locations

- `work/`: top-level Nextflow cache used by `-resume`.
- `<outdir>/01_samurai_*/work/`: nested SAMURAI cache.
- `lpwgs_root/.oncotracer/conda/`: top-level Conda environment cache.
- `.nextflow/`: Nextflow metadata.
- `.singularity_cache/` below `lpwgs_root`: Singularity/Apptainer images.
- Docker system storage: managed by the Docker daemon.

Do not remove these while a run is active. Verify and archive the final results before cleaning caches.

## Security notes

- Treat Docker access as privileged according to local policy.
- Use trusted image names, recorded digests, and trusted Conda channels.
- Do not place registry credentials in YAML files or shell history.
- Mount only the project directories needed for the analysis.
- Follow institutional rules for patient data.

See [Troubleshooting](troubleshooting.md) for runtime permissions, paths, disk usage, environment solving, and task logs.
