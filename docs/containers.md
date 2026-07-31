# Containers and execution environments

OncoTracer runs through Nextflow with one runtime option:

| Environment | Option | Image |
| --- | --- | --- |
| Linux workstation or server | `--docker` | [`carlosfarkas/oncotracer:latest`](https://hub.docker.com/r/carlosfarkas/oncotracer) |
| HPC with Singularity or Apptainer | `--singularity` | `docker://carlosfarkas/oncotracer:latest` |
| No container runtime | `--conda` | Local Conda environments |

Do not combine runtime options.

## Docker

```bash
# Enter the cloned repository.
cd /home/student/oncotracer

# Prepare and test Docker, the image, and the SAMURAI cache.
nextflow run main.nf --install --docker \
  --lpwgs_root /home/student/oncotracer/test \
  -work-dir /home/student/oncotracer/test/work/install_docker

# Optionally check a YAML without running the scientific tools.
nextflow run main.nf -stub-run --docker \
  -params-file params/my_run.yml

# Run or resume the real analysis with Docker.
nextflow run main.nf --docker \
  -params-file params/my_run.yml \
  -resume
```

Nextflow pulls or reuses the published image automatically.

## Singularity or Apptainer on HPC

```bash
# Confirm which HPC launcher is installed.
command -v singularity
command -v apptainer

# Enter the cloned repository.
cd /home/student/oncotracer

# Prepare and test the HPC runtime and image cache.
nextflow run main.nf --install --singularity \
  --lpwgs_root /home/student/oncotracer/test \
  -work-dir /home/student/oncotracer/test/work/install_singularity

# Optionally check a YAML without running the scientific tools.
nextflow run main.nf -stub-run --singularity \
  -params-file params/my_run.yml

# Run or resume the real analysis with Singularity or Apptainer.
nextflow run main.nf --singularity \
  -params-file params/my_run.yml \
  -resume
```

The configured image is `docker://carlosfarkas/oncotracer:latest`. The cluster administrator must allow image retrieval and the project bind paths.

## Conda fallback

```bash
# Prepare the Conda environments and SAMURAI cache.
nextflow run main.nf --install --conda \
  --lpwgs_root /home/student/oncotracer/test \
  -work-dir /home/student/oncotracer/test/work/install_conda

# Run or resume the analysis with Conda.
nextflow run main.nf --conda \
  -params-file params/my_run.yml \
  -resume
```

Use Conda when neither Docker nor Singularity/Apptainer is available.

## Project paths and mounts

`lpwgs_root` must contain every configured input, cache, work path, and output path that the workflow needs:

```yaml
lpwgs_root: /data/oncotracer_project
outdir: /data/oncotracer_project/results
illumina_samplesheet: /data/oncotracer_project/config/illumina.samplesheet.csv
```

A FASTQ outside `lpwgs_root` may not be visible inside the container.

## File ownership

When Docker writes files with the wrong numeric owner, add the host user and group IDs to the YAML:

```bash
# Print the host user ID.
id -u

# Print the host group ID.
id -g
```

```yaml
docker_user: "1234:1234"
```

Replace `1234:1234` with the values returned by `id -u` and `id -g`.

## Record the image used

```bash
# Read the runtime and image information recorded by --install.
cat .oncotracer/install/install_manifest.txt

# Record the OncoTracer commit.
git rev-parse HEAD
```

Keep the manifest, commit, YAML, samplesheet, and workflow summary with published results.

## Caches

- `work/`: top-level Nextflow resume cache.
- `<outdir>/01_samurai_*/work/`: nested SAMURAI cache.
- `.nextflow/`: workflow metadata.
- `.singularity_cache/` below `lpwgs_root`: Singularity/Apptainer images.
- Docker image storage: managed by the Docker daemon.

Do not delete caches while an analysis is active. See [Troubleshooting](troubleshooting.md) for permissions, bind paths, disk use, and task logs.
