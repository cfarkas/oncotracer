# Programs and provenance

OncoTracer v2 connects established alignment, quality-control, copy-number, small-variant, refinement, plotting, and reporting programs through one native stage graph. Normal users invoke the installed `oncotracer` executable rather than calling these programs independently.

## Native application and backend layer

| Component | Role |
| --- | --- |
| `oncotracer` | Parses flat YAML, schedules stages, records argument-array traces, validates outputs, and resumes content-matched work |
| Conda backend | Five isolated core CNA prefixes; selected variant tools use additional environments |
| Docker backend | Stable CNA image on GHCR; dated CNA/variant image on Docker Hub |
| Singularity/Apptainer backend | Same native image converted to and reused as a SIF |
| Poetry route | Source-development launcher plus the same five scientific Conda prefixes |

The five core groups are `core`, `qdnaseq`, `ichorcna`, `classifier`, and `gistic`.
Optional variants use `variants`, `ffperase` and `strelka2` environments as needed;
ONT caller/model installation has its own requirements. See the
[backend support table](containers.md#which-analyses-does-each-backend-support).

## Illumina route

| Program or library | Purpose | Representative output |
| --- | --- | --- |
| BWA-MEM | Single-end or paired-end alignment to hg38 | `01_samurai_illumina/alignment/*.bam` |
| SAMtools | FASTA/BAM indexing and BAM validation | BAM/BAI and reference indexes |
| Picard | Duplicate marking and duplicate metrics | stage-01 BAMs and metrics |
| qDNAseq | Independent per-sample read-depth correction, segmentation, and calls | `01_samurai_illumina/qdnaseq/` |
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

## Optional small-variant route

Enable it during FASTQ setup or use an explicit existing-BAM manifest. The
[variant guide](variants.md) provides both browser and terminal examples.

| Program | Role | Main requirement |
| --- | --- | --- |
| GATK Mutect2 / FilterMutectCalls | Illumina tumor-only candidates and orientation-artifact filtering | Variant tool environment; sample read-group names. |
| FreeBayes / bcftools | Independent Illumina germline-style calls | Variant tool environment. |
| Strelka2 germline | Independent paired-end Illumina germline calls | Separate pinned Strelka2/Python 2.7 runtime. |
| Strelka2 somatic | Paired-end Illumina tumor/normal calls | Explicit matched-normal assignment; same separate runtime. |
| Clair3 | ONT germline-style calls | Compatible selected basecaller model. |
| ClairS-TO | ONT tumor-only candidates | Compatible installed platform/model preset. |
| FFPERASE | Optional artifact assessment for Illumina FFPE | Compatible runtime and licensed external source/models. |
| Varlociraptor | Optional per-candidate probabilistic evidence and local FDR filtering | Variant tool environment; default presence model or explicit custom scenario. |
| ANNOVAR | Optional gene/database annotation | Existing licensed local software and matching unpacked databases. |

Outputs are under `08_variants/`: one normalized VCF and evidence table per
sample/caller, assessment results, optional annotations, and status/provenance.
Native genotypes and failure filters are retained; missing genotypes are not
invented. Model downloads can be deferred to Run after explicit selection and,
for FFPERASE, license acknowledgment. No automatic ANNOVAR installation occurs.

## Optional ONT methylation route

| Program or library | Purpose | Representative output |
| --- | --- | --- |
| Dorado | Reuse modified-base BAM calls and align, or basecall POD5 with a matching modification model | `07_methylation/modbam/` |
| Modkit | CPU-threaded CpG conversion and deterministic bedMethyl pileup | `07_methylation/bedmethyl/` |
| Sturgeon | User-installed/licensed CNS-tumor research classification | `07_methylation/sturgeon/` |
| MARLIN adapter | Checksum-pinned leukemia research model preparation/prediction | `07_methylation/marlin/` |

This optional branch is not part of the five managed core CNA environments or the stable container. Users provide exact local executables/models/resources. `--gpu` targets Dorado and exposes the device to MARLIN; it does not make Modkit or Sturgeon GPU programs. The branch aborts classification on zero usable modified-CpG calls while CNA continues.

## Optional interpretation route

When `run_cna_classifier: true`, the native classifier uses Python packages such as pandas, NumPy, SciPy, scikit-learn, Matplotlib, Jinja2, ReportLab, openpyxl, and optional Transformers/PyTorch support. GISTIC2 is isolated in its own prefix because it requires the MATLAB Compiler Runtime.

## Inspect the installed toolchain

```bash
oncotracer --version
oncotracer provenance --json
oncotracer doctor --backend conda
```

`doctor` uses exact configured prefixes and semantic probes. It does not infer correctness merely because a similarly named command appears first on a login shell's `PATH`.

For the stable CNA Docker image:

```bash
oncotracer install --docker
oncotracer doctor --backend docker
```

For the dated variant image, inspect the selected toolchain explicitly:

```bash
oncotracer doctor --backend docker \
  --image carlosfarkas/oncotracer:fastq-variants-20260922
docker run --rm carlosfarkas/oncotracer:fastq-variants-20260922 provenance --json
```

`doctor` covers the core CNA programs. Selected variant callers and resources
are checked during their own setup/run preflight. A healthy core installation
alone does not establish compatible ONT models or complete ANNOVAR databases.

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
- explicit package specifications for all core and selected optional Conda prefixes, or the immutable container digest;
- native trace, state, run manifest, stage-specific version files, and result checksums.

## Frozen v1.1 comparator

The v2 release gate executes the immutable v1.1 workflow as an independent comparator. Its SAMURAI source, Nextflow distribution, containers, and inputs are pinned and audited. This comparator is not part of normal v2 analysis execution.

## Scientific responsibility

Each component has assumptions about genome build, coverage, tumor fraction, ploidy, mappability, bin size, and sample type. Reproducible execution does not make an unsuitable method valid. Predefine settings, retain QC, and confirm important findings with an appropriate orthogonal assay.
