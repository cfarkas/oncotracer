# CNA evidence and diagnostic uncertainty

Stage 05 describes molecular CNA patterns and evidence limits. It does not infer
a validated tumor diagnosis from a catalog overlap. Recurrent CNA regions are
shared across cancer lineages, and a broad alteration can contain many genes.
These findings motivate preserving molecular descriptions rather than selecting
a tissue from the first matching rule. [Zack et al., Nature Genetics](https://www.nature.com/articles/ng.2760).

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
