# QuickStart 2: three HCC1143 libraries

Analyze three public Illumina libraries using the same `setup → check → run`
workflow as your own data. Download all six paired-end FASTQs, check them, and
use one CSV to keep the sample pairs clear.

| Sample | Public run |
| --- | --- |
| `HCC1143_DMSO` | `SRR7085656` |
| `HCC1143_BEZ235` | `SRR7085655` |
| `HCC1143_TRAMETINIB` | `SRR7085657` |

DMSO is a treatment control, not a normal genome. All three rows are analyzed
as tumor libraries. Start with [installation](installation.md) and
[QuickStart 1](quick_start.md). The reads total approximately 1.16 GB; allow
additional space for tools, references, BAMs and results.

## 1. Download and check the six FASTQs

Replace `/path/to/my/analyses_dir/` with your writable analysis directory.
Use the same directory in every block. `$PWD` means its absolute path.

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
exact size and MD5 checksum; interrupted downloads can be resumed by repeating
the corresponding `curl` command.

## 2. Create the samplesheet

Open a text editor and save the following as
`oncotracer-quickstart2/input/samplesheet.csv`. Replace `/absolute/path` with the
absolute directory you used above. Keep the header and all three rows:

```csv
sample,fastq_1,fastq_2,status
HCC1143_DMSO,/absolute/path/oncotracer-quickstart2/input/HCC1143_DMSO_R1.fastq.gz,/absolute/path/oncotracer-quickstart2/input/HCC1143_DMSO_R2.fastq.gz,tumor
HCC1143_BEZ235,/absolute/path/oncotracer-quickstart2/input/HCC1143_BEZ235_R1.fastq.gz,/absolute/path/oncotracer-quickstart2/input/HCC1143_BEZ235_R2.fastq.gz,tumor
HCC1143_TRAMETINIB,/absolute/path/oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R1.fastq.gz,/absolute/path/oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R2.fastq.gz,tumor
```

Each row links one sample to its matching R1 and R2. It does not combine the
three libraries into one sample.

## 3. Set up the project

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --non-interactive \
  --project "$PWD/oncotracer-quickstart2/analysis" \
  --reference-root "$PWD/oncotracer-quickstart2/reference" \
  --mode illumina --analysis cna \
  --samplesheet "$PWD/oncotracer-quickstart2/input/samplesheet.csv" \
  --threads 4
```

`--project` sets the configuration/result location; `--samplesheet` selects the
CSV; `--threads` requests CPU workers.

If a genome is already prepared, set `--reference-root` to that OncoTracer
reference directory instead. For example, reuse QuickStart 1's reference path.
Alternatively, [download prebuilt indexes](reference_indexes.md) with
`--mode illumina` and `--lpwgs-root` set to the chosen reference directory.
This is optional: the normal run prepares missing reference files automatically.

## 4. Check and run

```bash
cd /path/to/my/analyses_dir/
oncotracer check --config "$PWD/oncotracer-quickstart2/analysis/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart2/analysis/config/run.yml"
```

Check should list all three sample names. Resolve errors before running.
Results go to `oncotracer-quickstart2/analysis/results/`. Start with
`06_workflow_summary/workflow_summary.txt`, then review the sample plots and
`03_cna_codification/cna_events.tsv`.

## Resume

Repeat the same `run` command after fixing any error. Leave the samplesheet,
YAML and output directory unchanged; do not repeat setup or use `--force`
for a normal resume. Other [backends](containers.md) use the same configuration.
