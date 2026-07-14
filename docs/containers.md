# Containers and execution environments

A container packages analysis software, but Nextflow still runs on the host and needs permission to launch that container. Choose one runtime for each command.

| Situation | Recommended flag | What must work on the host |
| --- | --- | --- |
| Linux workstation/server with Docker | `--docker` | Java, Nextflow, Docker daemon; launch helpers used before nested containers |
| HPC where Docker is forbidden | `--singularity` | Java, Nextflow, Singularity/Apptainer configured by the cluster |
| No container runtime | `--conda` | Java, Nextflow, Conda and enough space for environments |

Do not combine runtime flags.

## Docker: recommended first route

From the cloned repository:

```bash
docker pull carlosfarkas/oncotracer:latest                        # download/update the maintained image
docker run --rm hello-world                                       # prove this account can use the daemon
nextflow run main.nf -stub-run --docker -params-file params/my_run.yml # validate workflow wiring
nextflow run main.nf --docker -params-file params/my_run.yml -resume   # run the analysis
```

`--docker` is an OncoTracer parameter. Do not replace it with `-profile docker` in the documented top-level commands.

### What is mounted

OncoTracer bind-mounts `lpwgs_root` at the identical path inside the container. Put the YAML's inputs, reference/cache, and `outdir` below that directory:

```yaml
lpwgs_root: /data/oncotracer_project
outdir: /data/oncotracer_project/runs/sample_a
illumina_samplesheet: /data/oncotracer_project/input/illumina.samplesheet.csv
```

A file at `/other_disk/sample.fastq.gz` is invisible when `/other_disk` is not below `lpwgs_root`, even if the host user can read it.

### File ownership

The default container user is `1000:1000`. On a system with different numeric IDs, add:

```yaml
docker_user: "1234:1234" # replace with your output from id -u and id -g
```

```bash
id -u
id -g
```

### Reproducible image identity

`latest` is convenient for tutorials but can change. Record the digest used in a study:

```bash
docker image inspect carlosfarkas/oncotracer:latest --format '{{index .RepoDigests 0}}'
```

For a frozen analysis, use an approved immutable digest and record it with the OncoTracer commit/YAML. Pulling again reuses unchanged Docker layers.

## Singularity or Apptainer on HPC

Check the command provided by the cluster:

```bash
singularity --version   # some systems retain this command name
apptainer --version     # newer installations often use Apptainer
```

Then use OncoTracer's flag name:

```bash
nextflow run main.nf -stub-run --singularity -params-file params/my_run.yml
nextflow run main.nf --singularity -params-file params/my_run.yml -resume
```

The configured image is `docker://carlosfarkas/oncotracer:latest`, and `lpwgs_root` is bound into the container. Use a cache directory on a filesystem with sufficient quota, following your cluster's Nextflow/Apptainer instructions. A Docker success does not guarantee the cluster permits the same bind mounts or outbound image pulls; test with `-stub-run` and a public example on the target system.

## Conda fallback

Use Conda only when container execution is unavailable or when running the optional classifier profile:

```bash
conda env create -f environment.yml
conda activate oncotracer
nextflow run main.nf -stub-run --conda -params-file params/my_run.yml
nextflow run main.nf --conda -params-file params/my_run.yml -resume
```

Conda resolves/downloads packages on the first run and can be slower or less portable across platforms than an immutable image. Preserve `environment.yml`, the solved environment export, and the OncoTracer commit for reproducibility.

## Understand the execution layers

OncoTracer coordinates three layers:

1. the host launches Java/Nextflow and validates/prepares paths and references;
2. OncoTracer processes use the selected container/Conda environment;
3. stage 01 starts the nested [SAMURAI](https://github.com/dincalcilab/samurai) workflow with the corresponding runtime.

This is why `--docker` does not make a missing host Java installation disappear. Reference preparation and ONT launch helpers may also check host commands such as `python3`, `samtools`, `minimap2`, or `pigz`; the [Programs](programs.md) page explains where each tool is used.

## Cache and storage locations

- `work/`: top-level Nextflow task cache used by `-resume`.
- `<outdir>/01_samurai_*/work/`: nested SAMURAI task cache.
- `.nextflow/` and stage-01 `.nextflow/`: workflow/runtime metadata.
- `.singularity_cache/` below `lpwgs_root`: downloaded Singularity images.
- Docker's system store: managed by the Docker daemon.

Do not clean these while the run is active. Verify and archive results before removing caches.

## Security notes

- Treat Docker access as privileged according to local policy.
- Use trusted, recorded image names/digests.
- Never embed registry credentials in a YAML or shell history.
- Mount only the project root needed by the analysis.
- Public examples are not a substitute for institutional governance of patient data.

See [Troubleshooting](troubleshooting.md) for daemon permissions, bind-path errors, disk usage, and task logs.
