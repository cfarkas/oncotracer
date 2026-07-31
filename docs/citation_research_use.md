# Citation and Research Use

## Cite the exact version

OncoTracer does not yet have a formal article DOI. Use the repository `CITATION.cff` and record the exact commit used for the analysis.

A current software citation is:

> Farkas, Carlos. (2026). *OncoTracer: reproducible LP-WGS CNA analysis for ONT and Illumina data* (version 0.1.0) [Computer software]. https://github.com/cfarkas/oncotracer

```bash
# Record the exact OncoTracer commit used for the analysis.
git rev-parse HEAD
```

Suggested methods text:

> Low-pass whole-genome sequencing copy-number analysis was performed with OncoTracer (version/commit: **replace with exact value**) using **Illumina qDNAseq at replace-kb bins** or **ONT ichorCNA at replace-kb bins**, followed by BAM-supported boundary refinement and CNA codification. The run used **replace container digest/runtime**, **replace reference build**, and the archived YAML and samplesheet.

Replace every placeholder. Do not cite only the mutable `latest` tag.

## Cite the methods and data

Also cite the relevant tools and data sources:

- [Nextflow](https://www.nextflow.io/) for workflow execution;
- [SAMURAI](https://github.com/dincalcilab/samurai) for the upstream LP-WGS workflow;
- [QDNAseq](https://bioconductor.org/packages/QDNAseq/) for Illumina CNA analysis;
- [ichorCNA](https://github.com/broadinstitute/ichorCNA) for the ONT route;
- public archive project, run accessions, checksums, retrieval date, and associated study.

For the HCC1143 example, cite PRJNA454331, the three run accessions in `examples/hcc1143_lpwgs/manifest.tsv`, and Ben-David et al., *Nature Communications* (2018).

## Keep a reproducibility record

```bash
# Record the OncoTracer commit.
git rev-parse HEAD

# Record the Nextflow version.
nextflow -version

# Read the runtime and container identity produced by --install.
cat .oncotracer/install/install_manifest.txt
```

Also preserve:

- the unedited run YAML and generated samplesheet or ONT mapping table;
- input checksums and source accessions;
- hg38 reference identity;
- caller, bin size, and refinement settings;
- stage-01 `pipeline_info` and workflow summary;
- hardware, executor, and runtime information;
- exclusions, reruns, QC tables, final CNA tables, and plots used in a report.

## Research-use scope

OncoTracer is a research workflow, not a standalone diagnostic system or medical device. Its output must not be used by itself to diagnose disease, select treatment, establish prognosis, or report a clinical result.

Low-pass read-depth analysis can support CNA and aneuploidy research. It does not reliably establish SNVs, small indels, balanced rearrangements, most fusions, methylation class, RNA or protein expression, copy-neutral LOH, clonality, or biallelic status. Sensitivity depends on coverage, bin size, tumor fraction, ploidy, contamination, library quality, reference, and caller assumptions.

Classifier, model, literature, and pathology-comparison outputs are research summaries. Review them against the primary CNA tables, morphology, IHC, cytogenetics, clinical-grade sequencing, and other appropriate assays.

## Data governance

- Use de-identified research identifiers.
- Include only fields required for the planned analysis.
- Do not send identifiable clinical text to public services or issue trackers.
- Confirm institutional approval, consent, data-use conditions, and computing policy.
- Treat public example results as software demonstrations, not clinical validation.

## Licensing

The repository currently has no standalone `LICENSE` file. Do not assume that public visibility grants unrestricted redistribution or commercial reuse. Contact the repository owner for licensing clarification and follow the licenses of all dependencies and datasets.
