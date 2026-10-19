# Run, stop and resume

## Start in the browser

After [installation](installation.md), open a new project:

```bash
oncotracer --interactive --backend conda --project "$PWD/my-study"
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
  --image carlosfarkas/oncotracer:run-audit-20261018
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
the analysis. Wait until stopping finishes. In the browser, choose **No** in
**Delete incomplete run?** to retain work for resuming.
Closing the browser or the setup-server terminal does **not** stop an already-started
analysis. Use its **Stop analysis** control before closing setup when you want it stopped.

## Resume behavior

Stop the current analysis first. In the browser, use **Continue an existing
analysis**, choose its project folder, then **Load saved settings and check**.
This retains the saved sample names, groups, backend, reference and analysis
settings. Review them and click **Run analysis** to continue.

You can prefill the same browser page with native Python or Nextflow:

```bash
oncotracer --interactive --project /path/to/existing-project

# From the current OncoTracer source checkout:
nextflow run main.nf --interactive --project /path/to/existing-project
```

For a headless server, continue the saved settings directly:

```bash
oncotracer setup --project "$PWD/my-study" --run
```

This uses the project's saved backend and settings. Completed work is reused
only when stage signatures and output checks still match. Missing, changed or
incompatible stages are recomputed. Existing outputs alone do not prove completion.

### Moving a project

Move the **whole project** after stopping its analysis, including `config/`,
`results/`, `logs/`, any project-local reference, and hidden directories. Select
the project at its new location using the commands above. For example, after
moving an output collection into `Paper`, select
`/data/Paper/oncotracer_outputs/ONT_fresh`, not the collection folder.

OncoTracer backs up the previous configuration, updates paths inside the moved
project, and records the relocation and runtime in `config/resume.json`.
External reads, references and models keep their original paths; if you also
move those, update their paths explicitly before resuming. A copied project
whose original still exists is rejected to avoid ambiguous ownership.

Use the current GitHub code and, for Docker, an image built from that code.
An older image does not understand the new relocation record. Changes in tools,
source code or Nextflow working paths can require some stages to run again.
No output is deleted and no incomplete stage is marked complete by resume.
To select an updated image explicitly, add `--backend docker --image IMAGE` to
the interactive command. The chosen image is verified and saved for subsequent runs.

## Run separate projects at the same time

Open another terminal and launch `oncotracer --interactive --backend conda`
(or `--backend docker`) for each project. Each session chooses an available port;
use its own printed URL. Give each run a different project/output directory and
a share of the available CPU and memory. Concurrent writes to the same project
are rejected. See [concurrent projects](nextflow_entry.md#concurrent-projects).

## Preview a run

Add `--dry-run` to see the planned commands without starting analysis:

```bash
oncotracer run --backend conda --config "$PWD/my-study/config/run.yml" --dry-run
```

Use a new project folder for a different analysis.

See the [execution reference](running_details.md) for backend internals,
reference mounts, the stage graph and audit records.
