# CNA evidence and diagnostic uncertainty

Stage 05 describes molecular CNA patterns and evidence limits. It does not infer
a validated tumor diagnosis from a catalog overlap. Recurrent CNA regions are
shared across cancer lineages, and a broad alteration can contain many genes.
These findings motivate preserving molecular descriptions rather than selecting
a tissue from the first matching rule. [Zack et al., Nature Genetics](https://www.nature.com/articles/ng.2760).

## Coverage comes before interpretation

**Not assessable** means the sample lacks reliable CNA evidence at the selected
bin size. It must not be described as CNA-flat, normal, ultra-complex or a deep
loss. The same checks apply to tumor and control samples, with native Conda,
Docker and the Nextflow launcher.

The current conservative research policy requires at least 100 usable autosomal
bins, a median of at least 20 reads per usable bin, and no more than 5% empty
usable bins. QDNAseq additionally requires a positive normalized baseline and
finite normalized values in at least 99% of usable bins. ichorCNA checks read
counts in matching GC/mappability reference bins before modeling. These are
engineering safeguards at the chosen resolution, not validated clinical
sensitivity thresholds. Passing them does not guarantee detection of every CNA.

Sparse read counts formerly became extreme negative log2 values after empty-bin
transformation. QDNAseq now fits with its supported square-root transform;
zero-valued exports are missing evidence instead of extreme loss values. Genuine
supported focal losses remain eligible for calling. QDNAseq describes its
transforms in the [upstream segmentation implementation](https://github.com/ccagc/QDNAseq/blob/master/R/segmentBins.R)
and the coverage/noise relationship in the
[QDNAseq publication](https://pmc.ncbi.nlm.nih.gov/articles/PMC4248318/).

Open **CNA coverage and assessability** in the results dashboard, or read:

```text
results/06_workflow_summary/
├── cna_assessment.html
├── cna_assessment.tsv
└── cna_assessment.json
```

Each sample has its status, bin size, median read count, empty-bin fraction and
reason. Unassessable samples are excluded from CNA oncoprints, clustering,
GISTIC, driver interpretation and pathology-concordance scoring. Their requested
HTML/PDF reports explicitly say **CNA NOT ASSESSABLE**. Variant calling and
methylation retain their own outcomes; a successful variant call does not make
an unavailable CNA assessment complete.

Consider a coarser bin size and recheck coverage, or obtain more usable reads.
OncoTracer does not silently change resolution or turn a control into a normal
reference. When resuming older projects, it rechecks CNA evidence and archives
superseded stages 02–05 under `.oncotracer-native/superseded-cna/` before rebuilding
them. Alignments and independent variant results are retained. Old CNA reports
cannot bypass the new check by requesting reports alone: resume the analysis
first. An all-unassessable cohort produces an explicit status report without
inventing CNA calls.

## Experimental fallback for very sparse samples

OncoTracer also produces an **experimental broad read-depth screen** for samples
that fail the primary CNA assessment. It runs automatically from reusable raw
counts when you run or continue CNA analysis; there is no separate launcher and
no realignment for this screen. Its calculation is independent of QDNAseq and
ichorCNA normalization, segmentation and tumor-fraction modeling. It shares
their raw read-counting outputs and reference annotations.

```text
results/06_workflow_summary/experimental_broad_cna/
├── index.html    # per-sample candidates, uncertainty and unavailable regions
├── regions.tsv
└── screen.json   # method version, thresholds and input hashes
```

This is a new **experimental implementation**, not a claim of a new validated
scientific method. Read-depth aggregation and GC correction are established
approaches; see [Yoon et al.](https://pmc.ncbi.nlm.nih.gov/articles/PMC2752127/).
Independent biological benchmarking remains necessary before sensitivity,
specificity or clinical-use claims.

The screen uses autosomal bins with adequate mappability and defined sequence,
matches GC/mappability strata, and estimates each chromosome's expected read
count from the median rates of at least eight *other* chromosomes. Sample
disease labels and existing CNA calls do not enter the calculation. It requires
at least 5,000 usable reads overall. Each reported region needs at least 200
observed and expected reads, ten bins and 10 Mb of usable sequence. Arms are
tested when both have sufficient support; otherwise the whole chromosome is
tested. Sparse or absent coverage remains **not assessable**, including a
chromosome with no reads.

Approximate 99% intervals include counting uncertainty, empirical block
dispersion and variation in the reference rates, with a conservative 0.15 log2
uncertainty floor. Candidate gains/losses require an interval entirely beyond
±0.3 log2 and an approximate Benjamini–Hochberg q-value at most 0.01. These
heuristics and intervals are **not clinically calibrated**. Too few testable
regions or changes in more than a third of them make the relative baseline
unassessable. The JSON records the exact policy.

The result is **candidate broad gain**, **candidate broad loss**, **no supported
broad change**, or **not assessable**. No supported change does not establish a
normal genome. The screen assumes a predominantly stable autosomal baseline;
it cannot resolve absolute ploidy, tumor fraction, small focal changes or
germline versus somatic origin. FFPE bias, uneven sampling and widespread
aneuploidy can invalidate that assumption. Confirm candidates independently.

Experimental results never overwrite primary CNA status or enter oncoprints,
GISTIC, driver interpretation, tumor classification or pathology scores. The
standard report still says **CNA NOT ASSESSABLE** when primary coverage fails.
Tests cover sparse neutral genomes, broad gains/losses, GC/mappability bias,
empty chromosomes, insufficient reads and resumed evidence replacement.

## Read the evidence fields

The classification table includes:

| Field | Meaning |
| --- | --- |
| `matched_cna_patterns` | All matched molecular rules; simultaneous matches remain visible |
| `cna_evidence_status` | Detected CNA, catalog overlap, absent CNA or inconsistent inputs |
| `n_distinct_driver_supporting_segments` | Distinct chromosome/start/end/state combinations supporting catalog hits |
| `driver_segment_support_status` | Whether supporting coordinates are complete, partial or absent |
| `n_shared_driver_supporting_segments` | Segments overlapping multiple catalog regions |
| `n_partial_catalog_region_overlaps` | Catalog regions with a partial overlap in at least one hit |
| `cna_uncertainty_flags` | Shared segments, partial overlap and other interpretation limits |

A distinct segment is not necessarily an independent biological event. Overlap
counts do not establish focal amplification, gene activation, sequence mutation
or biallelic loss. For example, EGFR-region gain alone does not establish an
integrated glioma diagnosis; CNS classification uses histology and defined
molecular criteria. [WHO CNS5 summary](https://pmc.ncbi.nlm.nih.gov/articles/PMC8328013/).

Broad-cancer mode leaves tissue origin unresolved. A restricted sample set is a
supplied study context, explicitly recorded as a prior. It is not a tissue
prediction. Pathology assessment requires integration with morphology,
immunophenotype and molecular findings.
[International Consensus Classification](https://doi.org/10.1182/blood.2022015851).

## Scores and probabilities

The evidence score is a hand-weighted research summary. Shared catalog hits are
capped by available supporting-segment counts. Retrieved literature supplies
background and adds no points. By default, probability fields are empty and
marked `not_estimated_no_reference_labels`.

An optional binary-labelled table can fit a logistic mapping for its matching
score target. `agreement_score` calibration is not reused for
`probable_cna_score`; generic `score` columns target agreement. Estimates from a
user table are labeled as fitted with external validation not established.

Before clinical probability claims, an unchanged model and endpoint require
independent validation in the intended population, including calibration,
discrimination and uncertainty. Fitting a supplied table alone does not provide
that evidence. [TRIPOD statement](https://www.bmj.com/content/350/bmj.g7594).

The clinician HTML/PDF reports expose these limits alongside the underlying CNA
findings. CNA-flat or missing results do not establish absence of cancer.
