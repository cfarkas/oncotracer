# Explore the browser interface

**[Open the interactive demo](assets/setup-demo/index.html)**

Try the same setup interface used by `oncotracer setup`, with synthetic Illumina
and ONT examples. The demo runs in your browser: folder contents, configuration
checks, progress and results are simulated. It does not access your files or run
analysis.

[![Preview of the interactive OncoTracer setup demo](assets/setup-demo-preview.png)](assets/setup-demo/index.html)

## What to try

1. Load an **Illumina** or **ONT** example and explore the detected FASTQs.
2. Assign samples to **Cancer** or **Normal**, using drag-and-drop or dropdowns.
   Leave a sample unassigned to exclude it.
3. Choose **Conda** or **Docker** and enable small variants. Compare **Fresh** and
   **FFPE** settings and the callers available for each sequencing platform.
4. Explore the four variant sections: specimen and callers, caller tools and
   models, filtering and FFPE, and annotation. Open path controls and try
   **Autodetect resources** or a path's **Autodetect** button. Resource results,
   candidate choices and installation help use synthetic examples in the demo.
5. Click **Preview configuration** to inspect an example configuration and simulated checks.
6. Click **Simulate Run** to see simulated progress, then explore the example
   results. Use the demo controls to reset and try another configuration.

## Start the real server

After [installing OncoTracer](installation.md), activate your launcher environment
and start a project:

```bash
oncotracer setup --project "$PWD/my-study" --backend conda
```

For Docker, use the image that includes the variant tools:

```bash
oncotracer setup --project "$PWD/my-study" --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260921 --variants
```

### Terminal equivalents

Use these **instead of** the corresponding browser command for a new project.
Answer the terminal questions; `--run` validates and starts the analysis:

```bash
oncotracer setup --terminal --project "$PWD/my-study" --backend conda --run
```

```bash
oncotracer setup --terminal --project "$PWD/my-study" --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260921 --variants --run
```

The [headless guide](headless.md) includes fully scripted Illumina/ONT runs,
remote-browser SSH commands, logs and resuming. The public QuickStarts include
complete terminal runs using real downloadable examples. The synthetic demo
itself produces no reads or executable analysis.

### Server behavior

The real interface opens at **127.0.0.1:8888**. Keep the terminal open and use its
complete printed URL, including the session code after `#`, if the browser does
not open automatically. Folder browsing reads files on the computer running
OncoTracer. Saving and checking prepares the project; **Run analysis** starts the
work. The [setup guide](setup.md) explains references, resource selection,
stopping and resuming.

The demo is generated from the repository's actual setup page using
`scripts/build_setup_demo.py`. Its simulated results teach navigation; use the
[public-data QuickStarts](quick_start.md) for an executable analysis example.
