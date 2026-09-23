# Run, stop and resume

## Start in the browser

After [installation](installation.md), open a new project:

```bash
oncotracer setup --project "$PWD/my-study"
```

Choose your files and settings. Click **Save configuration and check**, review
any messages, then **Run analysis**. Keep the terminal open to keep the server
available. [Try the demo](browser_demo.md) or follow the [setup guide](setup.md).

**Terminal equivalent for a new project:**

```bash
oncotracer setup --terminal --project "$PWD/my-study" --backend conda --run
```

This asks questions in the terminal, validates, and starts analysis.
For scripts or remote access, see [CLI and headless servers](headless.md).

## Run a saved configuration

If you closed setup after saving, run from the terminal:

```bash
oncotracer check --config "$PWD/my-study/config/run.yml"
oncotracer run --backend conda --config "$PWD/my-study/config/run.yml"
```

Use the path printed by setup. The command above uses Conda. For Docker, use:

```bash
oncotracer run --backend docker --config "$PWD/my-study/config/run.yml" \
  --image carlosfarkas/oncotracer:fastq-variants-20260922
```

## Check progress and results

1. Follow progress in the setup browser.
2. When the run finishes, click **Open results**.
3. Check the run status before using its tables or plots.

From the terminal:

```bash
cat "$PWD/my-study/results/06_workflow_summary/workflow_summary.txt"
```

**Partial failure** means some work completed. The summary tells you what succeeded
and what needs attention. See [troubleshooting](troubleshooting.md) for the next
step, or the [output guide](outputs.md) to find a result file.

## Stop a run

Click **Stop analysis** in the browser, or press **Ctrl+C** in the terminal running
the analysis. In the browser, choose **Keep project** to retain work for resuming.

## Resume behavior

Fix the reported problem, then restart the saved project:

```bash
oncotracer setup --project "$PWD/my-study" --run
```

This uses the project's saved backend and settings. Completed work is reused
when still valid. You can also repeat your original `oncotracer run` command.

## Preview a run

Add `--dry-run` to see the planned commands without starting analysis:

```bash
oncotracer run --backend conda --config "$PWD/my-study/config/run.yml" --dry-run
```

Use a new project folder for a different analysis.

See the [execution reference](running_details.md) for backend internals,
reference mounts, the stage graph and audit records.
