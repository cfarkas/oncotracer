# Run, stop and resume

## Start in the browser

After [installation](installation.md), open a new project:

```bash
oncotracer setup --project "$PWD/my-study"
```

Choose your files and settings. Click **Save configuration and check**, review
any messages, then **Run analysis**. Keep the terminal open to keep the server
available. [Try the demo](browser_demo.md) or follow the [setup guide](setup.md).

## Run a saved configuration

If you closed setup after saving, run from the terminal:

```bash
oncotracer check --config "$PWD/my-study/config/run.yml"
oncotracer run --config "$PWD/my-study/config/run.yml"
```

Use the actual path printed by setup. Without `--backend`, `run` uses the most
recently installed backend. To choose explicitly, use **one** of these:

```bash
oncotracer run --backend conda --config "$PWD/my-study/config/run.yml"
```

```bash
oncotracer run --backend docker --config "$PWD/my-study/config/run.yml" \
  --image carlosfarkas/oncotracer:fastq-variants-20260921
```

## Check progress and results

The browser shows the current stage and available progress estimates. At the
end, click **Open results**. You can also open `results/index.html` in your
project. `results/06_workflow_summary/workflow_summary.txt` records completion
and output locations.

For a partial failure, read the run summary to see which outputs completed and
which step needs attention. [Troubleshooting](troubleshooting.md) explains common
problems; [outputs](outputs.md) describes the result files.

## Stop a run

Click **Stop analysis** in the browser, or press **Ctrl+C** in the terminal running
the analysis. In the browser, choose **Keep project** to retain work for resuming.

## Resume behavior

Correct the reported issue, then repeat the same `oncotracer run` command.
Completed steps are reused only when their inputs, settings and outputs still
match. Incomplete steps run again. No separate resume flag is needed.

To resume in the terminal using the backend and image saved by browser setup:

```bash
oncotracer setup --project "$PWD/my-study" --run
```

## Optional terminal controls

Add `--threads 8` to set the thread count, or `--dry-run` to review planned
commands without starting analysis. Use `--force` only to deliberately rerun
completed work. Give a different analysis a new project folder.

See the [execution reference](running_details.md) for backend internals,
reference mounts, the stage graph and audit records.
