# QuickStart 2: three HCC1143 libraries

<p class="quickstart-source-link"><strong><a href="https://cfarkas.github.io/oncotracer/public_cohort/">Open this guide with playable videos</a></strong></p>

Analyze three public Illumina libraries in one project. Download all six paired-end
FASTQs, follow the browser walkthrough, then inspect the cohort results.
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
<a href="#2-follow-the-browser-walkthrough">2. Follow the browser walkthrough</a>
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
Choose inputs and settings in the browser; **Run analysis** starts the work.
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

## 2. Follow the browser walkthrough

<video controls preload="none" playsinline width="100%" poster="../assets/tutorial/quick-start-hcc1143.jpg" aria-label="QuickStart 2 browser setup walkthrough for three HCC1143 libraries">
  <source src="../assets/tutorial/quick-start-hcc1143.mp4" type="video/mp4">
  <track src="../assets/tutorial/quick-start-hcc1143.vtt" kind="captions" srclang="en" label="English">
  Your browser does not support embedded video. Use the download link below.
</video>

[Watch or download the video](assets/tutorial/quick-start-hcc1143.mp4) ·
[Text walkthrough](#walkthrough-in-text) · [Full transcript](assets/tutorial/quick-start-hcc1143.txt) ·
[Try the interface](browser_demo.md)

The silent video includes on-screen instructions, optional captions and a transcript.

The video uses the current interface with synthetic file inventories and simulated
checks. It stops before starting analysis. Use the verified downloads above for
your real run.

### Open your project

```bash
cd /path/to/my/analyses_dir/
oncotracer --interactive --backend conda \
  --project "$PWD/oncotracer-quickstart2/analysis" \
  --mode illumina --input-folder "$PWD/oncotracer-quickstart2/input"
```

For an installed Docker backend, replace `--backend conda` with `--backend docker`.
Keep the terminal open; if the browser does not open, use the complete printed
URL. For a remote server, see [SSH forwarding](headless.md#remote-browser-through-ssh).

### Walkthrough in text

Select preservation, discover the folder, and move **all three samples to Cancer**,
including DMSO. Confirm that each row contains its own R1/R2 pair. The video uses
**Fresh** as example metadata; use specimen records when choosing **Fresh** or
**FFPE** for your own samples.

Keep **CNA**, **QDNAseq**, **100 kb** bins and small variants off for this tutorial.
Choose your CPU budget, for example **4** threads. Click **Save configuration and
check**, review the three sample names, then click **Run analysis** when ready.
Each library is analyzed separately. GISTIC is optional for this three-sample cohort.

The first run downloads about **8.0 GiB** of hg38 indexes. To reuse QuickStart 1's
reference, choose **Reuse a prepared OncoTracer reference** and select
`oncotracer-quickstart1/illumina/reference`. Saving and checking does not download
references. See [genome indexes](reference_indexes.md).

## 3. Review the results

Click **Open results**, or open `oncotracer-quickstart2/analysis/results/`.
Check `06_workflow_summary/workflow_summary.txt` for completion, then inspect the
sample plots and `03_cna_codification/cna_events.tsv`. See [output files](outputs.md).

<a id="resume"></a>

## Stop or resume

Use **Stop analysis** in the browser. Wait until it stops, then choose **No** in
**Delete incomplete run?** to retain the project.
Closing the browser or setup terminal leaves an already-started analysis running.
Follow [Run, stop and resume](running.md#resume-behavior) with the saved project
path to continue; valid completed steps are reused.

Other studies can run at the same time in separate terminals, using different
project directories and a share of the server's CPU and memory.

<a id="alternative-scripted-setup-and-terminal-run"></a>
For unattended execution, use the [complete terminal commands](quickstart_terminal.md#quickstart-2).
