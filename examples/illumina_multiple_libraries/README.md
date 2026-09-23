# Mock example: multiple Illumina libraries

This **fictional template** shows two tumor libraries and one normal library.
No sequencing reads or analysis results are included. Replace every `/data` and
`/work` path below with your real, writable paths before running commands. The
FASTQs must already exist; do not create empty files to satisfy the template.
[Install the launcher and Conda tools](../../docs/installation.md) first.

## 1. Organize the inputs

Each library has its own R1/R2 pair. Keep these sample identifiers consistent in
filenames, the browser and the [samplesheet.csv template](samplesheet.csv).
Consolidate multiple lanes for one library before using this example.

```text
/data/illumina_multiple_libraries/
├── samplesheet.csv
└── fastq/
    ├── TUMOR01_R1.fastq.gz
    ├── TUMOR01_R2.fastq.gz
    ├── TUMOR02_R1.fastq.gz
    ├── TUMOR02_R2.fastq.gz
    ├── NORMAL01_R1.fastq.gz
    └── NORMAL01_R2.fastq.gz
```

| Sample identifier | R1 file | R2 file | Browser assignment | CSV status |
| --- | --- | --- | --- | --- |
| `TUMOR01` | `TUMOR01_R1.fastq.gz` | `TUMOR01_R2.fastq.gz` | Cancer | `tumor` |
| `TUMOR02` | `TUMOR02_R1.fastq.gz` | `TUMOR02_R2.fastq.gz` | Cancer | `tumor` |
| `NORMAL01` | `NORMAL01_R1.fastq.gz` | `NORMAL01_R2.fastq.gz` | Normal | `normal` |

Expected inventory: **3 samples and 6 FASTQ files**. Assigning a library as Normal
records its role; it does not declare a matched tumor–normal pair. QDNAseq analyzes
these three libraries independently, without pooling or subtracting the normal.

Choose **one** route below. Both use Illumina, Fresh preservation, Conda,
QDNAseq at 100 kb, four CPU threads and automatic hg38 reference preparation.
CNA interpretation reports and variant calling remain off. Fresh is a fictional
example choice: use FFPE instead if that matches your specimen records.

## 2A. Browser setup and run

```bash
oncotracer setup \
  --project /work/illumina-three-libraries \
  --mode illumina --analysis cna --backend conda \
  --input-folder /data/illumina_multiple_libraries/fastq \
  --variant-specimen-type fresh --threads 4 --hg38_build
```

1. Select **Illumina**, then **Fresh**. Confirm the FASTQ folder and click
   **Discover samples**. Expect the three grouped sample rows above.
2. Assign `TUMOR01` and `TUMOR02` to **Cancer**, and `NORMAL01` to **Normal**.
   Keep their identifiers unchanged; leave no sample unassigned.
3. Select **Copy-number analysis**, **100 kb**, **4 threads**, **Conda** and
   **Download prepared indexes when needed**. Illumina uses QDNAseq automatically. Leave the optional
   interpretation-report and small-variant checkboxes unchecked.
4. Confirm project folder `/work/illumina-three-libraries`. Click
   **Save configuration and check**, resolve any reported problems, then
   **Run analysis**. The browser writes `config/samplesheet.csv` and `config/run.yml`.

For a remote server, follow the [SSH tunnel instructions](../../docs/headless.md)
to open this same browser on your own computer. For navigation practice without
real files, use the [synthetic interactive demo](https://cfarkas.github.io/oncotracer/assets/setup-demo/index.html).

## 2B. Terminal / headless setup, check and run

No browser or terminal questions are used. First copy the linked CSV template to
`/data/illumina_multiple_libraries/samplesheet.csv`, then edit its paths to match
your six real FASTQs. Alternatively, this block writes the same CSV; use a new
path because `cat >` replaces an existing file:

```bash
mkdir -p "/data/illumina_multiple_libraries"
cat > "/data/illumina_multiple_libraries/samplesheet.csv" <<'CSV'
sample,fastq_1,fastq_2,status
TUMOR01,"/data/illumina_multiple_libraries/fastq/TUMOR01_R1.fastq.gz","/data/illumina_multiple_libraries/fastq/TUMOR01_R2.fastq.gz",tumor
TUMOR02,"/data/illumina_multiple_libraries/fastq/TUMOR02_R1.fastq.gz","/data/illumina_multiple_libraries/fastq/TUMOR02_R2.fastq.gz",tumor
NORMAL01,"/data/illumina_multiple_libraries/fastq/NORMAL01_R1.fastq.gz","/data/illumina_multiple_libraries/fastq/NORMAL01_R2.fastq.gz",normal
CSV
```

After reviewing the CSV, configure the same three-library project:

```bash
oncotracer setup --non-interactive \
  --project /work/illumina-three-libraries \
  --mode illumina --analysis cna --backend conda \
  --samplesheet /data/illumina_multiple_libraries/samplesheet.csv \
  --variant-specimen-type fresh --threads 4 --hg38_build
oncotracer check --config /work/illumina-three-libraries/config/run.yml
oncotracer run --backend conda \
  --config /work/illumina-three-libraries/config/run.yml
```

`setup` and `check` prepare and validate the configuration. `run` starts analysis
and downloads prepared reference indexes when needed. The samplesheet sets the
roles shown above; QDNAseq and 100 kb are the Illumina CNA defaults.
For the FFPE version, replace `--variant-specimen-type fresh` with
`--variant-specimen-type ffpe` in either setup command. Preservation metadata
alone does not enable variant calling or FFPERASE.

## 3. Review and resume

The expected output directory after a successful real run is
`/work/illumina-three-libraries/results/`. Start with the workflow summary and
[output guide](../../docs/outputs.md); this repository contains no generated results
for the fictional samples.

```bash
sed -n '1,120p' /work/illumina-three-libraries/results/06_workflow_summary/workflow_summary.txt
```

Resume by repeating only the same `oncotracer run` command. Existing configuration
files are protected; do not run both setup alternatives into the same project.
The original [two-library sampleA/sampleB example](../../docs/setup.md#illumina-multiple-libraries)
is also retained.
