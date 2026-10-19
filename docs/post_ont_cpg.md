# After ONT: drag samples into a CpG classifier

Open your existing ONT project. OncoTracer restores its sample names, roles,
reference and reusable settings; the original project stays unchanged.

```bash
oncotracer --interactive --cpg-from /path/to/ONT-project
```

This shortcut requires the [current main source](https://github.com/cfarkas/oncotracer/tree/main);
use the [GitHub installation option](installation_details.md#install-from-github).
Previously published releases and PyPI 2.1.0 do not include it.

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

Actual browser controls, synthetic prior project. **No analysis or patient data.**

## 1. Drag samples

Drag documented **leukemia** samples to **MARLIN**, or **CNS-tumour** samples to
**Sturgeon**. Use the dropdown if preferred. Choose from clinical records, not a
previous classifier prediction; leave unknown or unsuitable samples unassigned.

## 2. Save and check

Choose the classifier group, then **Save configuration and check**. OncoTracer
suggests a separate `ONT-project_MARLIN` or `ONT-project_Sturgeon` folder.
Only the chosen group is included; other samples are **not queued**.

If a requirement is missing, the page shows what to supply once: original
modified-base BAMs or POD5, installed classifier resources, and any required
license confirmation. For Sturgeon, acknowledge the license only after you have
obtained it and accepted its terms. Resolve missing items before running.

## 3. Run when ready

Click **Run analysis** explicitly after checks pass. The video stops after saving.
To run the other group, open the same command in another terminal and choose that
group. You can append `--classifier marlin` or `--classifier sturgeon` to preselect
the group; still review its sample assignments.

<details markdown="1">
<summary>Input requirements and result details</summary>

From the current source checkout, the equivalent Nextflow entry is:

```bash
nextflow run main.nf --interactive --cpg_from /path/to/ONT-project --backend conda
```

- The original sample FASTQs select read membership. Modified-base BAMs must
  contain `MM`/`ML` tags; an ordinary CNA BAM is insufficient. Alternatively,
  completed POD5 files need compatible Dorado basecalling/modification models.
- Reused tool/model paths must exist. Optional methylation tools and licensed
  assets are separate from the base installation; use the
  [resource reference](configuration/methylation_reference.md). Setup does not
  download them or accept terms for you. Native Conda/host/Poetry is required.
- Each project runs one classifier. Divide CPU/GPU capacity between concurrent
  projects; see [run, stop and resume](running.md).
- Review `results/07_methylation/methylation_status.json`, classifier scores and
  usable CpG/probe coverage. Missing predictions do not mean normal. See
  [result meanings](configuration/methylation.md#read-the-result).

</details>
