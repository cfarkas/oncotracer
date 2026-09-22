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
4. Click **Preview configuration** to inspect an example configuration and simulated checks.
5. Click **Simulate Run** to see simulated progress, then explore the example
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

The real interface opens at **127.0.0.1:8888**. Keep the terminal open and use its
complete printed URL, including the session code after `#`, if the browser does
not open automatically. Folder browsing reads files on the computer running
OncoTracer. Saving and checking prepares the project; **Run analysis** starts the
work. The [setup guide](setup.md) explains references, resource selection,
stopping and resuming.

The demo is generated from the repository's actual setup page using
`scripts/build_setup_demo.py`. Its simulated results teach navigation; use the
[public-data QuickStarts](quick_start.md) for an executable analysis example.
