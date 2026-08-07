# Programs and provenance

OncoTracer v2 connects established alignment, quality-control, copy-number, refinement, plotting, and reporting programs through one native stage graph. Normal users invoke the installed `oncotracer` executable rather than calling these programs independently.

## Native application and backend layer

| Component | Role |
| --- | --- |
| `oncotracer` | Parses flat YAML, schedules stages, records argument-array traces, validates outputs, and resumes content-matched work |
| Conda backend | Five isolated versioned prefixes for incompatible scientific stacks |
| Docker backend | Native v2 image from GitHub Container Registry |
| Singularity/Apptainer backend | Same native image converted to and reused as a SIF |
| Poetry route | Source-development launcher plus the same five scientific Conda prefixes |

The five groups are `core`, `qdnaseq`, `ichorcna`, `classifier`, and `gistic`.

## Illumina route

| Program or library | Purpose | Representative output |
| --- | --- | --- |
| BWA-MEM | Single-end or paired-end alignment to hg38 | `01_samurai_illumina/alignment/*.bam` |
| SAMtools | FASTA/BAM indexing and BAM validation | BAM/BAI and reference indexes |
| Picard | Duplicate marking and whole-genome metrics | stage-01 BAMs and metrics |
| qDNAseq | Read-depth correction, segmentation, calls, and optional local normal panel | `01_samurai_illumina/qdnaseq/` or `qdnaseq_local_pon/` |
| Native boundary-refinement Python | Local BAM-depth boundary evaluation | `02_bam_refinement/` |
| Native CNA codification/plotting | Event tables, cytogenomic notation, cohort and sample plots | stages 03 and 04 |

The standard Illumina configuration uses hg38, qDNAseq, and 100 kb coarse bins.

## ONT route

| Program or library | Purpose | Representative output |
| --- | --- | --- |
| pigz/Python gzip handling | Validate and merge barcode FASTQs | stage-01 merged FASTQ/logs |
| minimap2 | ONT alignment to hg38 | `01_samurai_ont/bam/*.bam` |
| SAMtools | Sort, index, and validate BAMs | BAM/BAI |
| HMMcopy `readCounter` | Genomic read-count bins | ichorCNA input WIG files |
| ichorCNA | Read-depth copy-number and tumor-fraction-oriented fitting | `01_samurai_ont/results/ichorcna/` |
| Native boundary refinement/codification/plotting | Refined segments and final result products | stages 02–04 |

The standard ONT configuration uses hg38, ichorCNA, and 500 kb coarse bins.

## Optional interpretation route

When `run_cna_classifier: true`, the native classifier uses Python packages such as pandas, NumPy, SciPy, scikit-learn, Matplotlib, Jinja2, ReportLab, openpyxl, and optional Transformers/PyTorch support. GISTIC2 is isolated in its own prefix because it requires the MATLAB Compiler Runtime.

## Inspect the installed toolchain

```bash
oncotracer --version
oncotracer provenance --json
oncotracer doctor --backend conda
```

`doctor` uses exact configured prefixes and semantic probes. It does not infer correctness merely because a similarly named command appears first on a login shell's `PATH`.

For Docker:

```bash
oncotracer install --docker
oncotracer doctor --backend docker
```

For Singularity or Apptainer:

```bash
oncotracer install --singularity
oncotracer doctor --backend singularity
```

## Provenance from a completed analysis

```bash
OUT="$PWD/project/results"

cat "$OUT/06_workflow_summary/workflow_summary.txt"
cat "$OUT/06_workflow_summary/native_run_manifest.json"
cat "$OUT/.oncotracer-native/trace.tsv"
find "$OUT" -type f \
  \( -name '*versions*' -o -name '*manifest*' -o -name '*SHA256SUMS*' \) \
  -print | sort
```

Preserve:

- the exact `oncotracer provenance --json` output;
- the YAML and generated samplesheet/mapping table;
- input and reference checksums;
- explicit package specifications for all five Conda prefixes, or the immutable container digest;
- native trace, state, run manifest, stage-specific version files, and result checksums.

## Frozen v1.1 comparator

The v2 release gate executes the immutable v1.1 workflow as an independent comparator. Its SAMURAI source, Nextflow distribution, containers, and inputs are pinned and audited. This comparator is not part of normal v2 analysis execution.

## Scientific responsibility

Each component has assumptions about genome build, coverage, tumor fraction, ploidy, mappability, bin size, and sample type. Reproducible execution does not make an unsuitable method valid. Predefine settings, retain QC, and confirm important findings with an appropriate orthogonal assay.
