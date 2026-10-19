# QuickStart 2: three HCC1143 libraries

<p class="quickstart-source-link"><strong><a href="https://cfarkas.github.io/oncotracer/public_cohort/">Open this guide with playable videos</a></strong></p>

Analyze three public Illumina libraries in one project. Download all six paired-end
FASTQs, choose browser or terminal setup, then inspect the cohort results.
[Install OncoTracer](installation.md) first; [QuickStart 1](quick_start.md) is the
smaller example. Reads total approximately **1.16 GB**, plus tools, references,
BAMs and results.

| Sample | Public run |
| --- | --- |
| `HCC1143_DMSO` | `SRR7085656` |
| `HCC1143_BEZ235` | `SRR7085655` |
| `HCC1143_TRAMETINIB` | `SRR7085657` |

All three are tumor libraries. DMSO is a treatment control, not a normal genome.

<nav class="quickstart-options" aria-label="QuickStart options">
<a href="#1-interactive-launch">1. Interactive launch</a>
<a href="#2-terminal-questions-for-headless-servers">2. Terminal questions for headless servers</a>
</nav>

## 1. Interactive launch

Watch the video, then [download and verify the example reads](#1-download-and-verify-the-reads).
After [installation](installation.md), open setup:

```bash
oncotracer --interactive --backend conda
```

Or use Nextflow from the source checkout:

```bash
nextflow run main.nf --interactive --backend conda
```

Use the full path to `main.nf` from another directory. For Docker, use `--backend docker`.
Choose **1. New analysis**, then your inputs and settings; **Run analysis** starts the work.
To reuse a saved project, choose **2. Continue an existing analysis** and follow
the [continuation video](running.md#resume-behavior).
The videos show synthetic inventories and checks, without starting analysis.

<div class="quickstart-videos">
<section class="quickstart-video-card" aria-label="HCC1143 video">
<h3>HCC1143</h3>
<div class="quickstart-player">
<video controls preload="metadata" playsinline poster="../assets/tutorial/quick-start-hcc1143.jpg" aria-label="HCC1143 setup video">
<source src="../assets/tutorial/quick-start-hcc1143.mp4" type="video/mp4">
<track src="../assets/tutorial/quick-start-hcc1143.vtt" kind="captions" srclang="en" label="English">
</video>
<button hidden type="button" class="quickstart-play" aria-label="Play HCC1143 setup video"><span aria-hidden="true">▶</span> Play video</button>
</div>
<p><a href="../assets/tutorial/quick-start-hcc1143.txt">Transcript</a> · <a href="../assets/tutorial/quick-start-hcc1143.mp4">Download video</a></p>
</section>
</div>

<a id="1-download-and-verify-the-reads"></a>

## Example data: download and verify

Replace `/path/to/my/analyses_dir/` with your writable analysis directory in every
block. `$PWD` means its absolute path.

```bash
cd /path/to/my/analyses_dir/
mkdir -p oncotracer-quickstart2/input

curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_DMSO_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_1.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_DMSO_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/006/SRR7085656/SRR7085656_2.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_BEZ235_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_1.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_BEZ235_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/005/SRR7085655/SRR7085655_2.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_1.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/SRR708/007/SRR7085657/SRR7085657_2.fastq.gz

md5sum -c <<'MD5'
419b241a073ecdbb0df5a9bec918c58b  oncotracer-quickstart2/input/HCC1143_DMSO_R1.fastq.gz
6398bf13a33c25e33693682bdeb00253  oncotracer-quickstart2/input/HCC1143_DMSO_R2.fastq.gz
3870b3fc2c693679cac9cce50cff2371  oncotracer-quickstart2/input/HCC1143_BEZ235_R1.fastq.gz
d21feb72ead870fb44c3e62d887e4974  oncotracer-quickstart2/input/HCC1143_BEZ235_R2.fastq.gz
688ee1571e591795b060963aacf942d3  oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R1.fastq.gz
f5975d77f63e9b139a24fb011580fd4d  oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R2.fastq.gz
MD5
```

Continue only when all six lines say `OK`. The versioned manifest records each
exact size and MD5 checksum. Repeat an interrupted `curl` command to resume.

<a id="2-open-setup-and-run"></a>

<a id="2-follow-the-browser-walkthrough"></a>
<a id="walkthrough-in-text"></a>

## 2. Terminal questions for headless servers

Use this option over SSH without a browser. Choose **one setup route per project**.
The videos show real terminal questions using tiny synthetic FASTQs; no analysis
runs. Use the verified public downloads above for your analysis.

<div class="quickstart-videos quickstart-terminal-videos">
<section class="quickstart-video-card" aria-label="HCC1143 terminal video">
<h3>HCC1143</h3>
<div class="quickstart-player">
<video controls preload="metadata" playsinline poster="../assets/tutorial/quick-start-terminal-hcc1143.jpg" aria-label="HCC1143 terminal questions video">
<source src="../assets/tutorial/quick-start-terminal-hcc1143.mp4" type="video/mp4">
<track src="../assets/tutorial/quick-start-terminal-hcc1143.vtt" kind="captions" srclang="en" label="English">
</video>
<button hidden type="button" class="quickstart-play" aria-label="Play HCC1143 terminal questions video"><span aria-hidden="true">▶</span> Play video</button>
</div>
<p><a href="../assets/tutorial/quick-start-terminal-hcc1143.txt">Transcript</a> · <a href="../assets/tutorial/quick-start-terminal-hcc1143.mp4">Download video</a></p>
</section>
</div>

### Answer the questions

This supplies the project, platform and input folder; the wizard asks for the
remaining settings. Press Enter to accept a displayed default.

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --terminal --backend conda \
  --project "$PWD/oncotracer-quickstart2/analysis" \
  --mode illumina --input-folder "$PWD/oncotracer-quickstart2/input"
```

For an installed Docker backend, use `--backend docker`.

Select `all`, keep the three sample names, and assign **every sample `cancer`**,
including DMSO. Confirm each sample has its own R1/R2 pair. Choose `cna`, a CPU
budget such as `4`, and `100` kb bins for QDNAseq. Answer `no` to interpretation
reports and small variants. CNA-only terminal setup does not ask Fresh/FFPE;
preservation is asked when enabling variants.

For **hg38 reference**, choose `download` for about **8.0 GiB** of indexes at run
time. To reuse QuickStart 1's reference, answer `reuse` and give
`oncotracer-quickstart1/illumina/reference`. Setup does not download indexes.
See [genome indexes](reference_indexes.md).

At **Final action**, enter `save`. Review the checked configuration and all three
sample names. When ready, launch the printed `oncotracer run` command. Each library
is analyzed separately; GISTIC is disabled in this example.

## 3. Review the results

Click **Open results** in the browser, or open
`oncotracer-quickstart2/analysis/results/index.html`.
Check `06_workflow_summary/workflow_summary.txt` for completion, then inspect the
sample plots and `03_cna_codification/cna_events.tsv`. See [output files](outputs.md).

<a id="resume"></a>

## Stop or resume

For a terminal run, press **Ctrl+C**; use [tmux](headless.md#keep-a-long-run-open)
to keep a remote run alive after disconnecting. For a browser run, use **Stop
analysis**, wait for it to stop, and choose **No** in **Delete incomplete run?**.
Closing its browser or setup terminal does not stop that analysis.

Follow [Run, stop and resume](running.md#resume-behavior) with the saved project
path. Valid completed steps are reused. Concurrent runs need separate projects
and a share of the server's CPU and memory.

<a id="alternative-scripted-setup-and-terminal-run"></a>
For unattended execution, use the [complete terminal commands](quickstart_terminal.md#quickstart-2).
