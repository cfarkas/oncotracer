<a id="quick-start"></a>

# QuickStart 1: Illumina and ONT

Run a complete native analysis of one public library per platform. Choose
Illumina, ONT, or both in separate projects. The reads total approximately 225 MB;
tools, references and results need additional space. This example analyzes
copy-number changes (CNA); FASTQs alone cannot assess ONT methylation.

[Install OncoTracer](installation.md), download the reads, then follow the browser
walkthrough. For a script without a browser, use the
[terminal version](quickstart_terminal.md#quickstart-1).

## 1. Download and verify the reads

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

## 2. Follow the browser walkthrough

<video controls preload="none" playsinline width="100%" poster="../assets/tutorial/quick-start-illumina-ont.jpg" aria-label="QuickStart 1 browser setup walkthrough for Illumina and ONT">
  <source src="../assets/tutorial/quick-start-illumina-ont.mp4" type="video/mp4">
  <track src="../assets/tutorial/quick-start-illumina-ont.vtt" kind="captions" srclang="en" label="English">
  Your browser does not support embedded video. Use the download link below.
</video>

[Watch or download the video](assets/tutorial/quick-start-illumina-ont.mp4) ·
[Text walkthrough](#walkthrough-in-text) · [Full transcript](assets/tutorial/quick-start-illumina-ont.txt) ·
[Try the interface](browser_demo.md)

**Video chapters:** Illumina at **0:00**; [ONT at **1:11**](assets/tutorial/quick-start-illumina-ont.mp4#t=71).
The silent video includes on-screen instructions, optional captions and a transcript.

The video uses the current interface with synthetic file inventories and simulated
checks. It stops before starting analysis. The downloaded reads above are the
real inputs for your run.

### Open your project

From your analysis directory, choose a platform. These commands only open setup;
you review the settings before starting an analysis.

**Illumina**

```bash
cd /path/to/my/analyses_dir/
oncotracer --interactive --backend conda \
  --project "$PWD/oncotracer-quickstart1/illumina" \
  --mode illumina --input-folder "$PWD/oncotracer-quickstart1/input/illumina"
```

**ONT**

```bash
cd /path/to/my/analyses_dir/
oncotracer --interactive --backend conda \
  --project "$PWD/oncotracer-quickstart1/ont" \
  --mode ont --input-folder "$PWD/oncotracer-quickstart1/input/fastq_pass"
```

For an installed Docker backend, replace `--backend conda` with `--backend docker`.
Keep each launching terminal open and use its complete printed browser URL.
You can run both projects in separate terminals; each session selects an available
port. Divide CPU and memory between runs. For a remote server, use [SSH forwarding](headless.md#remote-browser-through-ssh).

### Walkthrough in text

Choose preservation, discover the inputs, and assign samples as shown in the video:

| Project | Sample in Cancer | Analysis | Bin size |
| --- | --- | --- | --- |
| Illumina | `ERR12341627` | CNA, QDNAseq | 100 kb |
| ONT | Rename `barcode01` to `DRR165691` | CNA, ichorCNA | 500 kb |

All FASTQ batches inside `barcode01` form one sample. The video uses **Fresh** as
example metadata; choose **Fresh** or **FFPE** from specimen records for your own
data. FFPE is available in the browser without an extra command-line flag.

Keep small variants off for this CNA tutorial. Choose a thread budget, for example
**4**, then **Save configuration and check**. Review the checks and click **Run
analysis** when ready. Saving and checking does not start analysis or download
references. GISTIC requires at least two samples, so leave it off here.

The first run downloads prebuilt indexes: about **8.0 GiB for Illumina** or
**9.7 GiB for ONT**. To reuse existing indexes, choose **Reuse a prepared OncoTracer
reference** in the browser. See [genome indexes](reference_indexes.md).

## 3. Read the results

Click **Open results** when the run finishes. Files are also under
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

Use **Stop analysis** to stop a browser-started run. Wait until it stops, then
choose **No** in **Delete incomplete run?** to retain the project. Closing the browser or its setup terminal does not stop that analysis.
To resume, follow [Run, stop and resume](running.md#resume-behavior) with the saved
project's path. Valid completed steps are reused; no new setup is needed.

Next: [your own samples](setup.md) or [QuickStart 2: HCC1143](public_cohort.md).

<a id="alternative-scripted-setup-and-terminal-run"></a>
For automated setup and terminal execution, use the
[complete terminal commands](quickstart_terminal.md#quickstart-1).
