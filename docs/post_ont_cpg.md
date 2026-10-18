# After an ONT run: CpG classification

Reuse the original ONT methylation inputs in a **new methylation-only project**.
Drag samples into **MARLIN · leukemias** or **Sturgeon · CNS tumours**, then save
one classifier group at a time. Your completed copy-number project stays separate.

The classifier assignment board requires the updated GitHub source or native
release. Earlier PyPI 2.1.0 packages may not include these controls. Follow the
[installation guide](installation.md) and use the
[current GitHub release](https://github.com/cfarkas/oncotracer/releases/latest).

[Open this guide with playable video](https://cfarkas.github.io/oncotracer/post_ont_cpg/).

<section class="quickstart-video-card" aria-label="Post-ONT CpG classification walkthrough">
<h2>Watch: assign samples and save two projects</h2>
<div class="quickstart-player">
<video controls preload="metadata" playsinline poster="../assets/tutorial/post-ont-cpg.jpg" aria-label="Assign synthetic ONT samples to MARLIN and Sturgeon classifier groups">
<source src="../assets/tutorial/post-ont-cpg.mp4" type="video/mp4">
<track src="../assets/tutorial/post-ont-cpg.vtt" kind="captions" srclang="en" label="English">
</video>
<button hidden type="button" class="quickstart-play" aria-label="Play post-ONT CpG walkthrough"><span aria-hidden="true">▶</span> Play video</button>
</div>
<p><a href="../assets/tutorial/post-ont-cpg.txt">Transcript</a> · <a href="../assets/tutorial/post-ont-cpg.mp4">Download video</a></p>
</section>

The recording uses the actual browser controls with synthetic samples, file
and resource fixtures. It demonstrates dragging and saving temporary configurations;
**no analysis, model download or patient data is included**.

## 1. Keep the original inputs

A completed ONT copy-number run does not itself supply CpG calls. Locate the
original inputs that belong to the same samples:

| Keep or reuse | Purpose |
| --- | --- |
| Original barcode FASTQs | Define the read IDs assigned to each sample |
| Original modified-base BAMs with `MM`/`ML` tags | Reuse existing methylation calls without basecalling |
| Alternatively, completed POD5 files | Call modified bases with compatible Dorado models |
| Prepared hg38 reference folder | Reuse the reference from the completed project |
| Installed tools and classifier resources | Run Dorado, Modkit and the selected classifier |

An ordinary aligned CNA BAM is insufficient unless it actually retains the
required modified-base tags. Do not mix alternative basecalls or duplicate copies
of the same reads. Use completed inputs, never files MinKNOW is still writing.

Choose the classifier from the **documented clinical diagnosis and intended
research question**. MARLIN is for leukemia; Sturgeon is for CNS tumours. Do not
use a previous MARLIN prediction to decide which classifier a sample should enter.
Leave samples with unknown or unsuitable clinical context **unassigned** until
that context is resolved.

## 2. Open a separate project

Use your installed native Conda backend. Optional methylation tools and model
files are installed separately; follow the [resource reference](configuration/methylation_reference.md).
Methylation currently uses a native host, Conda or Poetry runtime, even if the
preceding CNA run used Docker.

Replace these paths with your original FASTQ folder and a new project:

```bash
oncotracer --interactive --backend conda \
  --mode ont --analysis methylation \
  --input-folder /data/ont-run/fastq_pass \
  --project /work/cpg-marlin
```

Choose specimen preservation from your records. Discover samples, give each one
its intended analysis name, and assign its Normal, Cancer or custom analysis role.
These roles describe samples; the classifier board below selects their CpG model.

Select **Methylation classification**. Keep optional short variants off for this
separate CpG analysis. Set a CPU budget appropriate for other running projects.
Under reference settings, choose **Reuse a prepared OncoTracer reference** and enter the
completed project's actual `lpwgs_root` path from `config/run.yml`.

## 3. Link BAMs or POD5 and resources

In the ONT input section, enter the original modified-base BAM file or
`bam_pass` folder. Choose **Modified-base BAM · reuse existing calls** in the
methylation settings. The FASTQ read IDs keep samples separate even when they
share a BAM folder.

If suitable modified-base BAMs are unavailable, link the matching POD5 folder,
choose the POD5 source and supply compatible Dorado basecalling and 5mCG/5hmCG
models. This performs basecalling again and can take substantially longer.

Supply the installed tool/model paths or a resource YAML. A previous CNA-only
configuration may lack these optional paths. MARLIN needs its model, feature
order, class annotations and hg38 probe BED; Sturgeon needs its model and probe
BED. Confirm the Sturgeon license only after obtaining it under the applicable
terms. Setup does not download these optional assets or grant a license.

## 4. Drag samples into classifier groups

In the methylation assignment board:

1. Drag samples with the appropriate documented leukemia context to **MARLIN**.
2. Drag samples with the appropriate documented CNS-tumour context to **Sturgeon**.
3. Keep unknown or unsuitable samples in **Not assigned**. A sample's dropdown
   provides the same assignment without dragging.

Choose **Classifier group to save and run: MARLIN**. Check the included count and
project destination `/work/cpg-marlin`, then **Save configuration and check**.
Only MARLIN-assigned samples enter that project. The other group and unassigned
samples are **not queued**.

For the second project, change the destination to `/work/cpg-sturgeon`, select
**Sturgeon**, supply its resources and save again. Review the counts and names in
each saved configuration. The board retains the other group's assignments within
this browser session; a new scan or platform selection clears them.

## 5. Run and inspect each project

The video stops after saving. In your real projects, resolve check errors before
choosing **Run analysis**. That button starts the **last saved project** in its
browser session; it does not start the other group.

To run both configurations saved above, use a separate terminal session for each.
Keep the combined CPU/GPU requests within the server's available capacity.

Terminal 1:

```bash
oncotracer run --backend conda --config /work/cpg-marlin/config/run.yml
```

Terminal 2:

```bash
oncotracer run --backend conda --config /work/cpg-sturgeon/config/run.yml
```

Alternatively, configure and run each group in its own browser session. See
[run, stop and resume](running.md) for keeping sessions open and stopping safely.

Start with `results/07_methylation/methylation_status.json` in each project.
Review classifier scores, usable CpG/probe coverage and any warnings together.
A missing prediction or too few usable probes does not mean the sample is normal.
The [methylation guide](configuration/methylation.md#read-the-result) explains
`complete`, `no_cpg_modifications`, `no_classifier_probes` and `failed` states.
