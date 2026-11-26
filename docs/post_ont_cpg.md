# After ONT: drag samples into a CpG classifier

Version 6.3 supports **MARLIN and Sturgeon in one project**, with an explicit
**Skip methylation** group. Rockfish is the default caller and requires compatible
raw POD5. Choose Modkit to reuse existing MM/ML calls.

The archived video below shows an earlier interface that saved each classifier
separately. Use the current instructions on this page for version 6.3.

Start methylation directly from named ONT inputs, or reuse a saved ONT project.
A completed CNA analysis is not required. Keep original inputs grouped by intended
library, using the same names as the ONT setup:

```text
ONT/
├── ONCO-001/
│   ├── fastq_pass/
│   ├── fastq_fail/
│   ├── pod5_pass/
│   ├── pod5_fail/
│   └── bam_pass/       # or modified_bam/
└── ONCO-002/
    ├── fastq_pass/
    ├── fastq_fail/
    ├── pod5_pass/
    ├── pod5_fail/
    └── bam_pass/       # or modified_bam/
```

Methylation automatically links each named library's `pod5_pass`. The browser
lists its ONCO-style name; drag samples to MARLIN or Sturgeon using clinical
records. Saved or explicitly selected modified-base BAMs remain valid alternatives.

CNA/variants use only `fastq_pass`. FASTQs define sample read membership but
contain no methylation marks. CpG analysis also needs matching original
modified-base BAMs with `MM`/`ML` tags, or POD5 plus compatible Dorado models.
The fail folders are retained originals, not additional analysis inputs; signal
folders are optional for CNA/variants. Legacy MinKNOW/barcode and single ligation
layouts remain supported. See [ONT input settings](configuration/ont.md).

**New project from named inputs:** open ordinary setup, discover the libraries,
and assign samples to the appropriate classifier group:

```bash
oncotracer --interactive --backend conda --mode ont --analysis methylation --input-folder /data/ONT
```

**Reuse a saved ONT project:** restore its names, roles, reference and reusable
settings directly into the CpG assignment page. The original stays unchanged:

```bash
oncotracer --interactive --cpg-from /path/to/ONT-project
```

Use [OncoTracer 6.3 or later](first_run.md) for this workflow.

[Open the playable guide](https://cfarkas.github.io/oncotracer/post_ont_cpg/).

<section class="quickstart-video-card" aria-label="Simple post-ONT CpG walkthrough">
<h2>Watch: restore, drag and save</h2>
<div class="quickstart-player">
<video controls preload="metadata" playsinline poster="../assets/tutorial/post-ont-cpg.jpg" aria-label="Restore an ONT project and drag samples into CpG classifier groups">
<source src="../assets/tutorial/post-ont-cpg.mp4" type="video/mp4">
<track src="../assets/tutorial/post-ont-cpg.vtt" kind="captions" srclang="en" label="English">
</video>
<button hidden type="button" class="quickstart-play" aria-label="Play short post-ONT CpG walkthrough"><span aria-hidden="true">▶</span> Play video</button>
</div>
<p><a href="../assets/tutorial/post-ont-cpg.txt">Transcript</a> · <a href="../assets/tutorial/post-ont-cpg.mp4">Download video</a></p>
</section>

Actual browser controls, synthetic named libraries and POD5 files. The video
shows the saved-project reuse route, pointer drags including a return to Unknown,
and separate checks.
**No analysis or patient data.** Fixture models are not scientific models.

## 1. Drag samples

Drag documented **leukemia** samples to **MARLIN**, or **CNS-tumour** samples to
**Sturgeon**. Use the dropdown if preferred. Choose from clinical records, not a
previous classifier prediction. **Cancer/Normal** records the clinical role;
**MARLIN/Sturgeon/Skip methylation** chooses a CpG classifier independently. Drag unknown
or unsuitable samples to **Skip methylation**. This excludes their CpG
classification without changing their clinical role or labeling them normal.

## 2. Save and check

Keep **Per-sample assignments · both groups**, then click **Save configuration
and check**. Continuation suggests a separate `ONT-project_CpG` folder, preserving
the original project. Both assigned groups are included.

The review lists the enabled analyses and each sample's classifier. Docker
prepares the managed tools and model resources. Review and accept the linked
Sturgeon license when assigning CNS samples. Resolve missing inputs or resources
before running; [resource details](configuration/methylation_reference.md) explain
the advanced path settings. No classifier is inferred from a sample name.

## 3. Run when ready

Click **Run analysis** after checks pass. Both groups run from the saved
assignments. The live page continues to show each sample's classifier. Legacy
single-group continuation remains available through the assignment-mode selector.

<details markdown="1">
<summary>Input requirements and result details</summary>

From the current source checkout, the equivalent Nextflow entry is:

```bash
nextflow run main.nf --interactive --cpg_from /path/to/ONT-project --backend conda
```

- The original sample FASTQs select read membership. Modified-base BAMs must
  contain `MM`/`ML` tags; an ordinary CNA BAM is insufficient. Alternatively,
  completed POD5 files need compatible Dorado basecalling/modification models.
- Docker supplies supported runtimes and prepares model resources after any
  required license acceptance. Conda/host/Poetry can reuse explicitly provided
  tools and models; see the [resource reference](configuration/methylation_reference.md).
- Both classifier groups share one project. CPU/GPU limits still apply to the
  whole run; see [run, stop and resume](running.md).
- Review `results/07_methylation/methylation_status.json`, classifier scores and
  usable CpG/probe coverage. Missing predictions do not mean normal. See
  [result meanings](configuration/methylation.md#read-the-result).

</details>
