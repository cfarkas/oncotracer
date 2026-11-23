# CNA quality checks and interpretation

OncoTracer records whether each sample has sufficient coverage for CNA analysis
and which candidate events pass the read-support checks. The results dashboard
shows these decisions alongside the CNA plots and annotations.

## Sample coverage checks

**Not assessable** means that the sample failed the coverage or normalized-profile
checks at the selected bin size. Its CNA state is unknown: a missing assessment
cannot support a call of a normal profile, a deep deletion or a complex genome.
Tumors and controls undergo the same sample-level checks in Conda, Docker and
Nextflow runs.

New projects save `cna_profile: sensitive`. Existing YAML files without this field
use `standard`; resuming a project preserves that choice. Select the profile in
setup or pass `--cna-profile`:

```bash
oncotracer setup --cna-profile sensitive
oncotracer run --config project/config/run.yml --cna-profile sensitive
nextflow run . --analysis full --config project/config/run.yml --cna_profile sensitive
```

| Research criterion | Standard | Sensitive |
| --- | --- | --- |
| Median reads per usable bin | 20 | 5 |
| Maximum empty usable bins | 5% | 20% |
| Minimum usable bins | 100 | 100 |
| Additional minimum total usable reads | None | 5,000 |
| Finite normalized values | 99% | 99% |
| Final gain / loss log2 threshold | +0.25 / −0.30 | +0.20 / −0.20 |
| Minimum final event | 3 bins and 1 Mb | 3 bins and 0.5 Mb |

These are research filtering thresholds; their clinical detection limits have
not been established. Both profiles require a positive normalized baseline.
The standard profile preserves qDNAseq's raw cutoff calls; sensitive uses ±0.20 for gains and losses.
Each sample is analyzed independently, without pooling controls or subtracting
a control from a tumor. Passing these sample checks still leaves individual
events subject to the criteria below.

### Read support for candidate events

The sensitive profile checks the underlying read counts for:

- every candidate CNA in a control;
- candidates in study samples that fail standard coverage requirements;
- candidates admitted only by the sensitive size or amplitude thresholds,
  including those in study samples that pass standard coverage requirements.

A supported change in a control is retained. The recorded `tumor` or `normal`
role selects the event-level thresholds; missing or unknown roles are rejected.
Sample names and diagnoses do not determine this policy.

Expected counts come from GC/mappability-matched bins on at least eight other
autosomes. An event requires at least ten usable bins, 100 observed reads,
200 expected reads, 80% matched bins and 60% covered span. Its approximate
interval must extend entirely beyond ±0.15 log2 in the called direction.
Interleaved bin subsets must agree, and at least three of four consecutive
blocks must support that direction.

| Event evidence criterion | Study sample (`tumor`) | Control (`normal`) |
| --- | --- | --- |
| Approximate interval | 95% | 99% |
| Maximum approximate multiple-candidate q-value | 0.05 | 0.01 |

Sample coverage requirements, read-count requirements, the 0.15 log2 uncertainty
floor and empirical dispersion are the same for both roles. The audit records
the role, policy and interval level for every evaluated event.

Review `03_cna_codification/cna_evidence.html`, `cna_evidence_decisions.tsv` and
`cna_evidence_summary.json` for the decisions and supporting counts. Rejected
events retain their reasons and metrics. Original calls are kept in
`diagnostics/unfiltered/`; retained calls proceed to final notation, plots,
interpretation and GISTIC.

These checks reuse the reads used for calling. Their intervals and q-values
are approximate, and the q-values are not calibrated false-discovery rates
after candidate selection. The method assumes a mostly stable autosomal
baseline and requires independent confirmation of biological findings.

QDNAseq segmentation uses its supported square-root transform. Zero-valued
exports are treated as missing evidence, preventing empty bins from appearing
as extreme deletions. Supported focal losses remain eligible for calling.
See the [segmentation implementation](https://github.com/ccagc/QDNAseq/blob/master/R/segmentBins.R)
and the [QDNAseq coverage and noise study](https://pmc.ncbi.nlm.nih.gov/articles/PMC4248318/).

### Assessment files and reassessment

Open **CNA coverage and assessability** in the dashboard, or inspect:

```text
results/06_workflow_summary/
├── cna_assessment.html
├── cna_assessment.tsv
└── cna_assessment.json
```

Each sample has a status, bin size, median read count, empty-bin fraction and
reason. Samples marked **CNA NOT ASSESSABLE** are excluded from CNA oncoprints,
clustering, GISTIC, driver interpretation and pathology-concordance scoring.
Variant calling and methylation have separate outcomes.

For insufficient coverage, try a coarser bin size or obtain more usable reads,
then reassess the sample. Bin size changes require an explicit choice in the
configuration. When an older project is resumed, OncoTracer rechecks CNA
evidence and archives replaced stages 02–05 under
`.oncotracer-native/superseded-cna/`. Completed alignments and independent
variant results are retained. Regenerating reports alone does not refresh this
assessment. A cohort with no assessable samples receives a status report.

## ichorCNA fallback

When a primary QDNAseq result fails quality checks, OncoTracer runs unpaired
ichorCNA at 500 kb and 1 Mb. Samples that pass the primary checks keep their
QDNAseq results, including profiles with no accepted events. Tumors and controls
use the same fallback rules.

The fallback is enabled by default. Set `ichorcna_fallback: false` to disable it,
or use `ichorcna_fallback_workers` to control concurrency within the run's
thread budget.

A sample requires at least 5,000 primary autosomal MAPQ-20 reads after excluding
unmapped, secondary, supplementary, duplicate and QC-failed records. Both
resolutions must have adequate read support and finite, sufficiently smooth
normalized profiles. An accepted relative event requires:

- a span of at least 10 Mb and 20 finite 500-kb bins;
- at least 80% coverage and an absolute median log2 ≥0.2;
- agreement between odd/even bins and with the 1-Mb result;
- a five-bin block-bootstrap interval that excludes zero.

`08_ichorcna_fallback/` contains the versioned rules, native fits, rejected
segments and output checksums. The selected method for each library is recorded
in `06_workflow_summary/selected_cna_assessment.json`. Reports show the selected
fallback result together with the original QDNAseq quality assessment.

These coarse relative profiles are kept separate from primary classifier and
GISTIC inputs. The two resolutions use the same reads. A successful HMM fit
alone does not validate absolute ploidy, tumor fraction or the biological
accuracy of a CNA call.

To reassess existing alignments in a new output directory:

```bash
oncotracer rescue-cna \
  --primary-status /path/to/qdnaseq/qdnaseq_sample_status.json \
  --bam-manifest /path/to/input/native.bam.samplesheet.csv \
  --outdir /path/to/new-rescue-results \
  --cna-profile sensitive --binsize 50 --threads 8 --workers 2
```

Use the primary run's actual CNA profile and bin size. The command reads the
source BAMs and status without modifying them. The ichorCNA environment must
contain the pinned hg38 500/1000-kb reference assets; use `--assets` to specify
an alternative read-only location. Missing tools or required evidence stop
the reassessment.

## Experimental fallback for very sparse samples

The **experimental broad read-depth screen** runs automatically from reusable
raw counts when the primary CNA assessment fails. Sensitive mode also screens
assessable samples. The screen does not require realignment. It uses the same
read-counting outputs and reference annotations as the primary methods, but
performs its own normalization and regional assessment.

```text
results/06_workflow_summary/experimental_broad_cna/
├── index.html    # candidates, uncertainty and unavailable regions
├── regions.tsv
└── screen.json   # method version, thresholds and input hashes
```

The screen uses established read-depth aggregation and GC correction methods;
see [Yoon et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC2752127/). This
implementation has not undergone independent biological validation.

Autosomal bins must have adequate mappability and defined sequence. After
matching GC/mappability strata, the screen estimates expected counts from the
median rates on at least eight other chromosomes. Disease labels and existing
CNA calls are excluded from this calculation.

The standard profile requires 5,000 usable reads overall. Each reported region
needs at least 200 observed and expected reads, ten bins and 10 Mb of usable
sequence. Chromosome arms are tested when both have sufficient support;
otherwise the whole chromosome is tested. A chromosome without sufficient
reads remains **not assessable**.

Approximate 99% intervals include counting uncertainty, empirical block
dispersion and variation in the reference rates, with a 0.15 log2 uncertainty
floor. Candidate gains or losses require an interval entirely beyond ±0.3 log2
and an approximate Benjamini–Hochberg q-value ≤0.01. Too few testable regions,
or changes in more than one third of them, invalidate the relative baseline.
The JSON records the exact policy used.

Sensitive mode uses 100 reads per region, 5 Mb of usable sequence, approximate
95% intervals, a ±0.15 log2 margin and q ≤0.05. Control candidates must also pass
the stricter read-support check above. Both profiles retain the 5,000-read
sample requirement and stable-baseline checks.

Results are labelled **candidate broad gain**, **candidate broad loss**,
**no supported broad change**, or **not assessable**. They remain separate from
primary CNA status, oncoprints, GISTIC, driver interpretation, tumor classification
and pathology scores. A failed primary assessment therefore remains labelled
**CNA NOT ASSESSABLE** in the main report.

This screen estimates relative broad changes. It cannot resolve absolute
ploidy, tumor fraction, small focal changes or germline versus somatic origin.
FFPE bias, uneven sampling and widespread aneuploidy can invalidate its baseline.
Its intervals and thresholds are research criteria, not calibrated clinical
detection limits. Tests cover sparse neutral genomes, broad gains/losses,
GC/mappability bias, empty chromosomes, insufficient reads and resumed analyses.

## Interpretation fields

Stage 05 describes retained CNA patterns and their overlap with catalog regions.
A broad event may span several genes, and recurrent CNA regions occur across
cancer lineages. OncoTracer therefore retains all matching molecular patterns.
See [Zack et al., Nature Genetics](https://www.nature.com/articles/ng.2760).

| Field | Meaning |
| --- | --- |
| `matched_cna_patterns` | All matched molecular rules; simultaneous matches remain visible |
| `cna_evidence_status` | Detected CNA, catalog overlap, absent CNA or inconsistent inputs |
| `n_distinct_driver_supporting_segments` | Distinct chromosome/start/end/state combinations supporting catalog hits |
| `driver_segment_support_status` | Whether supporting coordinates are complete, partial or absent |
| `n_shared_driver_supporting_segments` | Segments overlapping multiple catalog regions |
| `n_partial_catalog_region_overlaps` | Catalog regions with a partial overlap in at least one hit |
| `cna_uncertainty_flags` | Shared segments, partial overlap and other interpretation limits |

Several catalog hits can come from one segment. Segment counts therefore do
not necessarily represent independent biological events. A catalog overlap alone
cannot establish focal amplification, gene activation, a sequence mutation or
biallelic loss. EGFR-region gain, for example, is insufficient for an integrated
glioma diagnosis. See the [WHO CNS5 summary](https://pmc.ncbi.nlm.nih.gov/articles/PMC8328013/).

Broad-cancer mode leaves tissue origin unresolved. A restricted study setting
records the supplied tissue context as a prior; it is not a tissue prediction.
Diagnostic interpretation combines these findings with morphology,
immunophenotype and other molecular evidence, as described in the
[International Consensus Classification](https://doi.org/10.1182/blood.2022015851).

## Evidence scores and fitted probabilities

The evidence score combines predefined weights for the recorded findings.
Shared catalog hits are capped by the number of supporting segments. Retrieved
literature provides background and contributes no points. Probability fields
are empty by default, with status `not_estimated_no_reference_labels`.

A user-supplied table with binary labels can fit a logistic mapping for the
corresponding score. `agreement_score` calibration applies only to agreement;
`probable_cna_score` requires its own mapping. A generic `score` column targets
agreement. The output identifies these probabilities as fitted estimates
without established external validation.

Clinical use would require independent validation of the fixed model and
endpoint in the intended population, including calibration, discrimination and
uncertainty; see [TRIPOD](https://www.bmj.com/content/350/bmj.g7594). HTML/PDF
reports include these limitations with the underlying findings. A CNA-flat or
unassessable result does not exclude cancer.
