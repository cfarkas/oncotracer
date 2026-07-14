# Citation and research-use limitations

## Cite the exact version you used

OncoTracer does not yet have a formal article DOI. The repository's `CITATION.cff` is therefore the authoritative current citation metadata and explicitly marks itself as a placeholder until a formal citation is available.

A current software citation is:

> Farkas, Carlos. (2026). *OncoTracer: reproducible LP-WGS CNA analysis for ONT and Illumina data* (version 0.1.0) [Computer software]. https://github.com/cfarkas/oncotracer

Add the exact commit used in your analysis, for example:

```bash
git rev-parse HEAD
```

Suggested methods text:

> Low-pass whole-genome sequencing copy-number analysis was performed with OncoTracer (version/commit: **replace with exact value**) using **Illumina qDNAseq at replace-kb bins** or **ONT ichorCNA at replace-kb bins**, followed by BAM-supported boundary refinement and CNA codification. The run used **replace container digest/runtime**, **replace reference build**, and the archived YAML/samplesheet.

Replace every bold placeholder. Do not cite only `latest`, because that tag can change.

GitHub can render `CITATION.cff` through its “Cite this repository” interface. If that file and this page differ, report the discrepancy and use the repository metadata from the exact commit analyzed.

## Cite the methods you rely on

A reproducible report should also cite the relevant upstream methods/software, not only OncoTracer:

- [Nextflow](https://www.nextflow.io/) for workflow execution;
- [SAMURAI](https://github.com/dincalcilab/samurai) for the upstream LP-WGS workflow;
- [QDNAseq](https://bioconductor.org/packages/QDNAseq/) for the standard Illumina route;
- [ichorCNA](https://github.com/broadinstitute/ichorCNA) for the standard ONT/liquid-biopsy route;
- aligner and other major tools listed in [Programs](programs.md).

Use the citation/version recorded by the actual run's `pipeline_info`, because nested tool versions may differ between releases.

## Cite public example data separately

Software citation does not replace dataset attribution. For the HCC1143 six-FASTQ example, cite:

- public archive project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331);
- the exact run accessions in `examples/hcc1143_lpwgs/manifest.tsv`;
- [Ben-David et al., Nature Communications (2018)](https://doi.org/10.1038/s41467-018-05729-w).

For any other public data, record archive, project, sample/run accessions, retrieval date, checksums, and the associated study.

## Minimum reproducibility record

Archive with the result:

```bash
git rev-parse HEAD
nextflow -version
docker image inspect carlosfarkas/oncotracer:latest --format '{{index .RepoDigests 0}}'
```

Also preserve:

- unedited run YAML and samplesheet/ONT mapping table;
- input file checksums and source accessions;
- hg38 reference identity/checksum;
- caller, analysis type, bin size, and refinement parameters;
- workflow summary and stage-01 `pipeline_info`;
- hardware/executor/runtime information;
- any manual exclusions or reruns;
- primary stage-02/03 tables and QC reports.

## Research-use scope

OncoTracer is a research workflow for CNA analysis. It is not a standalone diagnostic system or a medical device. Its output must not be used by itself to diagnose disease, select treatment, establish prognosis, or report a clinical result.

Low-pass read-depth analysis can support broad/focal gain and loss detection and CNA-burden/aneuploidy research. It does not reliably establish SNVs, indels, balanced rearrangements, most fusions, methylation class, expression/protein state, copy-neutral LOH, clonality, or biallelic status. Sensitivity depends on coverage, bin size, tumor fraction, ploidy, normal contamination, library quality, reference/mappability, and caller assumptions.

Optional classifier, literature, model, and pathology-concordance outputs are hypotheses or compatibility summaries. They do not validate pathology and must be reviewed against primary CNA tables, morphology, IHC, cytogenetics, clinical-grade sequencing, and other appropriate assays.

## Data governance and privacy

- Use de-identified research identifiers in samplesheets and pathology tables.
- Include only pathology columns needed for the planned analysis.
- Do not send identifiable clinical text to network services or public issue trackers.
- Confirm institutional approval, consent/data-use conditions, and computing policy before analysis.
- Treat public cell-line/tutorial results as software demonstrations, not clinical validation.

## Licensing and reuse

The repository currently does not include a standalone `LICENSE` file. Do not assume that public visibility grants unrestricted redistribution or commercial reuse. Contact the repository owner for licensing clarification, and follow the licenses/citation requirements of every bundled or downloaded dependency and dataset.
