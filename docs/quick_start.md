<a id="quick-start"></a>

# QuickStart 1: Illumina and ONT

<p class="quickstart-source-link"><strong><a href="https://cfarkas.github.io/oncotracer/quick_start/">Open this guide with playable videos</a></strong></p>

Analyze libraries for copy-number changes (CNA). Choose
Illumina, ONT or both in separate projects. Reads total approximately 225 MB,
plus tools, references and results. FASTQs alone cannot assess ONT methylation.

With the [current GitHub source](installation_details.md#install-from-github),
select `ONT/`: each named folder is one intended library and supplies its sample
name. This named layout is newer than PyPI 2.1.0.

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

CNA/variants use only `fastq_pass`. Other folders are optional; methylation needs
matching `MM`/`ML` modified-base BAMs or POD5 plus compatible Dorado models.
FASTQs contain no methylation marks. The downloads/videos below use the supported
legacy `fastq_pass/barcode01` layout; single MinKNOW runs and ligation libraries
also remain supported. [ONT layout and settings](configuration/ont.md).

[Installation](installation.md) · [Terminal commands](quickstart_terminal.md#quickstart-1).

<nav class="quickstart-options" aria-label="QuickStart options">
<a href="#1-interactive-launch">1. Interactive launch</a>
<a href="#2-terminal-questions-for-headless-servers">2. Terminal questions for headless servers</a>
</nav>

## 1. Interactive launch

Choose CPU/RAM budgets and concurrent sample counts before saving.
[Multiple samples and continuation](MULTISAMPLE_QUICKSTART.md).

[Download and verify the reads](#1-download-and-verify-the-reads) before choosing inputs. Open setup:

```bash
oncotracer --interactive --backend conda
```

Or use Nextflow from the source checkout:

```bash
nextflow run main.nf --interactive --backend conda
```

Use the full path to `main.nf` from another directory. For Docker, use `--backend docker`.
Choose **1. New analysis** for setup, or **2. Continue an existing analysis**
for saved work ([video](running.md#resume-behavior)).
**Run analysis** starts the work. These videos use synthetic inventories and checks.

<div class="quickstart-videos">
<section class="quickstart-video-card" aria-label="Illumina video">
<h3>Illumina</h3>
<div class="quickstart-player">
<video controls preload="metadata" playsinline poster="../assets/tutorial/quick-start-illumina.jpg" aria-label="Illumina setup video">
<source src="../assets/tutorial/quick-start-illumina.mp4" type="video/mp4">
<track src="../assets/tutorial/quick-start-illumina.vtt" kind="captions" srclang="en" label="English">
</video>
<button hidden type="button" class="quickstart-play" aria-label="Play Illumina setup video"><span aria-hidden="true">▶</span> Play video</button>
</div>
<p><a href="../assets/tutorial/quick-start-illumina.txt">Transcript</a> · <a href="../assets/tutorial/quick-start-illumina.mp4">Download video</a></p>
</section>
<section class="quickstart-video-card" aria-label="ONT video">
<h3>ONT</h3>
<div class="quickstart-player">
<video controls preload="metadata" playsinline poster="../assets/tutorial/quick-start-ont.jpg" aria-label="ONT setup video">
<source src="../assets/tutorial/quick-start-ont.mp4" type="video/mp4">
<track src="../assets/tutorial/quick-start-ont.vtt" kind="captions" srclang="en" label="English">
</video>
<button hidden type="button" class="quickstart-play" aria-label="Play ONT setup video"><span aria-hidden="true">▶</span> Play video</button>
</div>
<p><a href="../assets/tutorial/quick-start-ont.txt">Transcript</a> · <a href="../assets/tutorial/quick-start-ont.mp4">Download video</a></p>
</section>
</div>

<a id="1-download-and-verify-the-reads"></a>

## Example data: download and verify

Replace `/path/to/my/analyses_dir/` with your writable analysis directory in every
block. `$PWD` means that directory's absolute path.

```bash
cd /path/to/my/analyses_dir/
mkdir -p oncotracer-quickstart1/input/illumina \
  oncotracer-quickstart1/input/fastq_pass/barcode01

curl --fail --location --continue-at - \
  --output oncotracer-quickstart1/input/illumina/ERR12341627_1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/027/ERR12341627/ERR12341627_1.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart1/input/illumina/ERR12341627_2.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/ERR123/027/ERR12341627/ERR12341627_2.fastq.gz
curl --fail --location --continue-at - \
  --output oncotracer-quickstart1/input/fastq_pass/barcode01/DRR165691_1.fastq.gz \
  https://ftp.sra.ebi.ac.uk/vol1/fastq/DRR165/DRR165691/DRR165691_1.fastq.gz

md5sum -c <<'MD5'
4c96d551152694b3893ea98b7781a3ae  oncotracer-quickstart1/input/illumina/ERR12341627_1.fastq.gz
1b20d9eb98f755244f6383ea1354bd40  oncotracer-quickstart1/input/illumina/ERR12341627_2.fastq.gz
55a3984cb0334aa4cb0a38255cb71c06  oncotracer-quickstart1/input/fastq_pass/barcode01/DRR165691_1.fastq.gz
MD5
```

Require three checksum `OK` lines. Repeat interrupted `curl` commands to resume.
The ONT folder is `barcode01`.

<a id="2-open-setup-and-run"></a>

<a id="2-follow-the-browser-walkthrough"></a>
<a id="walkthrough-in-text"></a>

## 2. Terminal questions for headless servers

Over SSH, choose **one setup route per project**. These videos use synthetic
FASTQs without running analysis; use the verified public downloads above.

<div class="quickstart-videos quickstart-terminal-videos">
<section class="quickstart-video-card" aria-label="Illumina terminal video">
<h3>Illumina</h3>
<div class="quickstart-player">
<video controls preload="metadata" playsinline poster="../assets/tutorial/quick-start-terminal-illumina.jpg" aria-label="Illumina terminal questions video">
<source src="../assets/tutorial/quick-start-terminal-illumina.mp4" type="video/mp4">
<track src="../assets/tutorial/quick-start-terminal-illumina.vtt" kind="captions" srclang="en" label="English">
</video>
<button hidden type="button" class="quickstart-play" aria-label="Play Illumina terminal questions video"><span aria-hidden="true">▶</span> Play video</button>
</div>
<p><a href="../assets/tutorial/quick-start-terminal-illumina.txt">Transcript</a> · <a href="../assets/tutorial/quick-start-terminal-illumina.mp4">Download video</a></p>
</section>
<section class="quickstart-video-card" aria-label="ONT terminal video">
<h3>ONT</h3>
<div class="quickstart-player">
<video controls preload="metadata" playsinline poster="../assets/tutorial/quick-start-terminal-ont.jpg" aria-label="ONT terminal questions video">
<source src="../assets/tutorial/quick-start-terminal-ont.mp4" type="video/mp4">
<track src="../assets/tutorial/quick-start-terminal-ont.vtt" kind="captions" srclang="en" label="English">
</video>
<button hidden type="button" class="quickstart-play" aria-label="Play ONT terminal questions video"><span aria-hidden="true">▶</span> Play video</button>
</div>
<p><a href="../assets/tutorial/quick-start-terminal-ont.txt">Transcript</a> · <a href="../assets/tutorial/quick-start-terminal-ont.mp4">Download video</a></p>
</section>
</div>

### Answer the questions

These commands supply the project, platform and input folder; the wizard asks
for the remaining settings. Press Enter to accept a displayed default.

**Illumina**

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --terminal --backend conda \
  --project "$PWD/oncotracer-quickstart1/illumina" \
  --mode illumina --input-folder "$PWD/oncotracer-quickstart1/input/illumina"
```

**ONT**

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --terminal --backend conda \
  --project "$PWD/oncotracer-quickstart1/ont" \
  --mode ont --input-folder "$PWD/oncotracer-quickstart1/input/fastq_pass"
```

For an installed Docker backend, use `--backend docker`.
Select `all`, assign `cancer`, and choose `cna`:

| Project | Sample name | CNA settings |
| --- | --- | --- |
| Illumina | Keep `ERR12341627` | QDNAseq; answer `100` kb |
| ONT | Rename `barcode01` to `DRR165691` | Choose `ichorcna`; fixed 500 kb |

All FASTQ batches inside `barcode01` form one sample. Set a CPU budget, for example
`4`, and answer `no` to interpretation reports and small variants. CNA-only
terminal setup does not ask Fresh/FFPE; preservation is asked when enabling variants.

For **hg38 reference**, choose `download`, or `reuse` and give a prepared reference
folder. With `download` selected, the first analysis downloads about **8.0 GiB for
Illumina** or **9.7 GiB for ONT**; setup does not download indexes. See [genome indexes](reference_indexes.md).

At **Final action**, enter `save`. Review the checked configuration and sample
names. When ready, launch the printed `oncotracer run` command in your terminal.

## 3. Read the results

When finished, click **Open results** in the browser or open `results/index.html`.
Files are under
`oncotracer-quickstart1/illumina/results/` or `oncotracer-quickstart1/ont/results/`:

| File | Purpose |
| --- | --- |
| `index.html` | Results dashboard |
| `06_workflow_summary/workflow_summary.txt` | Completion status |
| `06_workflow_summary/final_report.html` | Combined findings |
| `03_cna_codification/cna_events.tsv` | Copy-number changes |
| `04_cna_custom_plots/cna_per_sample_pages.pdf` | Sample plots |
| `.oncotracer-native/trace.tsv` | Recorded commands |

## Stop or resume

For a terminal run, press **Ctrl+C**; use [tmux](headless.md#keep-a-long-run-open)
to keep a remote run alive after disconnecting. For a browser run, use **Stop
analysis**, wait for it to stop, and choose **No** in **Delete incomplete run?**.
Closing its browser or setup terminal does not stop that analysis.

Follow [Run, stop and resume](running.md#resume-behavior) with the saved project
path. Valid completed steps are reused. Concurrent runs need separate projects
and a share of the server's CPU and memory.

Next: [your own samples](setup.md) or [QuickStart 2: HCC1143](public_cohort.md).

<a id="alternative-scripted-setup-and-terminal-run"></a>
For automated setup and terminal execution, use the
[complete terminal commands](quickstart_terminal.md#quickstart-1).
