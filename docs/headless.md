# CLI and headless servers

OncoTracer can configure and run entirely in a terminal. On a server without a
desktop, you can also connect your laptop browser through SSH.

## Choose your setup route

| Route | Command | What happens |
| --- | --- | --- |
| Browser | `oncotracer setup` | Starts the local setup server and opens a browser. |
| Terminal questions | `oncotracer setup --terminal` | Asks for inputs/settings in the terminal; finish with `save` or `run`. |
| Scripted setup | `oncotracer setup --non-interactive ...` | Writes configuration from explicit sample flags; no browser or questions. |
| Remote browser | `oncotracer setup --no-browser --port 8888` | Starts the setup server and prints its URL; connect through SSH below. |
| Saved analysis | `oncotracer run --backend conda --config /work/study/config/run.yml` | Runs saved settings in the terminal. |

**`--no-browser` still starts a web server.** Use `--terminal` or
`--non-interactive` for no web interface. There is no `--headless` flag.

Install the [launcher and analysis tools](installation.md) on the server first.
Activate the launcher environment in each shell. All read, reference, model,
database and project paths belong to the server. Replace `/data` and `/work`
paths with your own. Choose one setup route per new project.

## Terminal questions

The equivalent of browsing an Illumina FASTQ folder:

```bash
oncotracer setup --terminal --project /work/illumina-study \
  --mode illumina --input-folder /data/illumina --backend conda
```

Assign sample names and Cancer/Normal roles. Choose `save`, then:

```bash
oncotracer check --config /work/illumina-study/config/run.yml
oncotracer run --backend conda --config /work/illumina-study/config/run.yml
```

For ONT, use `--mode ont --input-folder /data/run/fastq_pass` and a different
project. Append `--run` to configure and run in the same terminal wizard.

## Scripted Illumina: setup, check and run

One paired-end library, with every sample choice supplied:

```bash
oncotracer setup --non-interactive \
  --project /work/illumina-scripted --mode illumina --analysis cna \
  --sample-name sampleA --status tumor \
  --fastq-1 /data/illumina/sampleA_R1.fastq.gz \
  --fastq-2 /data/illumina/sampleA_R2.fastq.gz \
  --backend conda --threads 8
oncotracer check --config /work/illumina-scripted/config/run.yml
oncotracer run --backend conda --config /work/illumina-scripted/config/run.yml
```

Omit `--fastq-2` for single-end reads. For several libraries, replace the
single-sample flags with `--samplesheet /data/illumina/samplesheet.csv`; use the
[four-column samplesheet](setup.md#illumina-multiple-libraries).
`--input-folder` is for interactive discovery and cannot be combined with
`--non-interactive`.

## Scripted ONT: setup, check and run

Each selected barcode includes all its FASTQ batches:

```bash
oncotracer setup --non-interactive \
  --project /work/ont-scripted --mode ont --analysis cna \
  --reads-folder /data/run/fastq_pass \
  --barcodes barcode01,barcode02 --sample-names sampleA,sampleB \
  --backend conda --threads 8
oncotracer check --config /work/ont-scripted/config/run.yml
oncotracer run --backend conda --config /work/ont-scripted/config/run.yml
```

Both scripted examples select CNA and automatic reference preparation at run
time. To reuse a reference, add `--hg38_build /data/shared-reference` during
setup. For ONT controls and sample roles, follow [batch setup](auto_params.md).

## Docker without a browser

The launcher runs on the server; analysis runs in Docker. Install the
[variant-capable image](installation.md) first. This CNA example needs no host Conda:

```bash
oncotracer setup --non-interactive \
  --project /work/docker-study --mode illumina --analysis cna \
  --sample-name sampleA --status tumor \
  --fastq-1 /data/illumina/sampleA_R1.fastq.gz \
  --fastq-2 /data/illumina/sampleA_R2.fastq.gz \
  --backend docker --image carlosfarkas/oncotracer:fastq-variants-20260921 \
  --threads 8
oncotracer check --config /work/docker-study/config/run.yml
oncotracer run --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260921 \
  --config /work/docker-study/config/run.yml
```

For variants, use the complete [FFPE and ONT terminal examples](variants_reference.md),
including caller/model paths. [ANNOVAR](annovar.md) also has terminal examples.
For an **existing-BAM variant YAML**, use:

```bash
oncotracer variants --config /data/variants.yml --dry-run
oncotracer variants --config /data/variants.yml --threads 8
```

This command uses host tools and runtime paths in its variant YAML; it has
no `--backend` option. It is the terminal counterpart of the existing-BAM
browser form, `setup --variant-config`, which itself is browser-only.

## Remote browser through SSH

**On the server**, start setup without launching a browser:

```bash
oncotracer setup --project /work/remote-study --backend conda \
  --no-browser --port 8888
```

**On your laptop**, open another terminal. Replace `user@server` with your SSH login:

```bash
ssh -N -o ExitOnForwardFailure=yes \
  -L 127.0.0.1:8888:127.0.0.1:8888 user@server
```

Open the **entire URL printed on the server** in your laptop browser, including
the session code after `#`. Keep `127.0.0.1` and port `8888` exactly as printed:
the server validates both. If the port is busy, choose another, such as `8890`,
in the server command and both port positions in the tunnel command.
The server listens only on loopback; no public interface or firewall opening is needed.

Browse and Autodetect inspect the **server**, including its tools and databases.
Follow the usual Save/check/Run steps. Keep the setup process and tunnel running
while using the interface.

For remote existing-BAM settings, replace the server command with:

```bash
oncotracer setup --variant-config /data/variants.yml --no-browser --port 8888
```

Use the same tunnel. For terminal-only execution, use the `oncotracer variants`
commands above. For a new FASTQ project without a browser, use the scripted
Illumina or ONT commands above instead.

## Long runs, logs and resuming

If `tmux` is installed, create a persistent session **on the server**:

```bash
tmux new -s oncotracer
```

Inside that session, activate your launcher environment and run:

```bash
mkdir -p /work/illumina-scripted/logs
set -o pipefail
oncotracer run --backend conda --config /work/illumina-scripted/config/run.yml \
  2>&1 | tee -a /work/illumina-scripted/logs/run.log
```

Detach with **Ctrl+B**, then **D**. Reconnect with `tmux attach -t oncotracer`.
The setup server can also run inside tmux. Closing the browser or pressing
Ctrl+C in the setup-server terminal leaves an already-started analysis running.
Use **Stop analysis** in the interface, or Ctrl+C in a terminal running
`oncotracer run`, to stop the analysis itself.

Inspect results without a browser:

```bash
tail -n 40 /work/illumina-scripted/logs/run.log
cat /work/illumina-scripted/results/06_workflow_summary/workflow_summary.txt
```

After correcting a partial failure, repeat the same `run` command. Completed,
matching stages are reused; omit `--force` for normal resuming.
`run` without `--backend` uses the most recently installed backend. To reuse
the backend and image saved in a project instead:

```bash
oncotracer setup --project /work/illumina-scripted --run
```

## Complete examples

[QuickStart 1](quick_start.md#alternative-scripted-setup-and-terminal-run) and
[QuickStart 2](public_cohort.md#alternative-scripted-setup-and-terminal-run) include
terminal-only setup/check/run commands using their downloaded reads.
The [full public cohort](full_tutorial.md), [batch setup](auto_params.md),
[methylation guide](configuration/methylation.md), [variant reference](variants_reference.md)
and [ANNOVAR guide](annovar.md) also include terminal runs.
