# Citation and research-use limitations

## Cite the exact version you used

OncoTracer does not yet have a formal article DOI. The repository's `CITATION.cff` is therefore the authoritative current citation metadata and explicitly marks itself as a placeholder until a formal citation is available.

A current software citation is:

> Farkas, Carlos. (2026). *OncoTracer: reproducible LP-WGS CNA analysis for ONT and Illumina data* (version 2.1.0) [Computer software]. https://github.com/cfarkas/oncotracer

Record the embedded source identity used in your analysis:

```bash
oncotracer provenance --json
```

Suggested methods text:

> Low-pass whole-genome sequencing copy-number analysis was performed with OncoTracer (version/commit: **replace with exact value**) using **Illumina qDNAseq at replace-kb bins** or **ONT ichorCNA at replace-kb bins**, followed by BAM-supported boundary refinement and CNA codification. The run used **replace container digest/runtime**, **replace reference build**, and the archived YAML/samplesheet.

Replace every bold placeholder. Do not cite only `latest`, because that tag can change.
A dated image is also a tag: record its digest and embedded source commit. Current
integration builds can retain software version `2.1.0` while containing newer
code than the stable release; the version alone does not identify that code.

If variants were enabled, also state the caller/version, tumor-only versus
matched-normal design, preservation, ONT model/preset where applicable, target
intervals, native filters, extra assessment settings and annotation databases.
Do not describe default Varlociraptor presence scoring as a somatic classifier.

GitHub can render `CITATION.cff` through its “Cite this repository” interface. If that file and this page differ, report the discrepancy and use the repository metadata from the exact commit analyzed.

## Cite the methods you rely on

A reproducible report should also cite the relevant upstream methods/software, not only OncoTracer:

- [SAMURAI](https://github.com/dincalcilab/samurai) for the upstream LP-WGS workflow;
- [QDNAseq](https://bioconductor.org/packages/QDNAseq/) for the standard Illumina route;
- [ichorCNA](https://github.com/broadinstitute/ichorCNA) for the standard ONT/liquid-biopsy route;
- aligner and other native dependencies recorded by the [selected backend and optional environments](containers.md).

For small-variant analysis, cite only the components actually used:

- [GATK Mutect2](https://gatk.broadinstitute.org/hc/en-us/articles/360035531132--How-to-Call-somatic-mutations-using-GATK4-Mutect2), [FreeBayes](https://github.com/freebayes/freebayes), [bcftools](https://samtools.github.io/bcftools/), or [Strelka2](https://github.com/Illumina/strelka) for Illumina;
- [Clair3](https://github.com/HKU-BAL/Clair3) or [ClairS-TO](https://github.com/HKU-BAL/ClairS-TO) for ONT;
- [FFPERASE](https://github.com/papaemmelab/nf-ffperase), [Varlociraptor](https://varlociraptor.github.io/) and [ANNOVAR](https://annovar.openbioinformatics.org/) when their assessment/annotation stages ran.

Follow each project's citation instructions and record the actual version/model
revision. For optional methylation, also cite the basecaller, modification tools
and classifier used; see [methylation configuration](configuration/methylation.md).

Use the citation/version recorded by the actual run manifest and stage-01 provenance, because tool versions may differ between releases. Cite Nextflow only when reproducing a historical v1.1 analysis; native v2 does not invoke it.

## Cite public example data separately

Software citation does not replace dataset attribution. For the HCC1143 six-FASTQ example, cite:

- public archive project [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331);
- the exact run accessions in `examples/hcc1143_lpwgs/manifest.tsv`;
- [Ben-David et al., Nature Communications (2018)](https://doi.org/10.1038/s41467-018-05729-w).

For any other public data, record archive, project, sample/run accessions, retrieval date, checksums, and the associated study.

## Minimum reproducibility record

Archive with the result:

```bash
oncotracer --version
oncotracer provenance --json
oncotracer doctor --backend conda
```

For Conda, preserve explicit package specifications for all five core prefixes
and every selected optional environment, including separately installed ONT tools.
For containers, preserve the immutable image digest and provenance emitted from
inside that exact image, in addition to the host launcher's provenance.

For a stable release, retain its `release-provenance.json`, which binds its source,
binary, image and complete public-data CNA parity audits. For a source checkout or
dated integration image, retain that build's own provenance and validation evidence;
do not attach an older stable release's parity bundle as validation of newer code.

Also preserve:

- unedited run YAML and samplesheet/ONT mapping table;
- input file checksums and source accessions;
- reference assembly and identity/checksum (normally hg38; matching hg19 is supported for standalone BAM variants);
- caller, analysis type, bin size, and refinement parameters;
- workflow summary, native run manifest and `.oncotracer-native/trace.tsv`;
- hardware/executor/runtime information;
- any manual exclusions or reruns;
- primary stage-02/03 tables and QC reports;
- if variants ran, `08_variants/variant_status.json`, `variant_provenance.json`,
  original/final caller VCFs, assessment tables and annotation database identities;
- explicit matched-normal sample mapping for Strelka2 somatic, and reasons for
  skipped or unevaluated assessments.

## Research-use scope

OncoTracer is a research workflow for CNA analysis with optional small-variant and methylation branches. It is not a standalone diagnostic system or a medical device. Its output must not be used by itself to diagnose disease, select treatment, establish prognosis, or report a clinical result.

Low-pass read-depth CNA analysis can support broad/focal gain and loss detection
and CNA-burden/aneuploidy research. CNA output alone does not establish SNVs,
indels, balanced rearrangements, most fusions, methylation class, expression/protein
state, copy-neutral LOH, clonality, or biallelic status. Separate optional variant
or methylation modules analyze their own evidence; their successful execution is
not a sensitivity benchmark or clinical validation. Sensitivity depends on coverage, bin size, tumor fraction, ploidy, normal contamination, library quality, reference/mappability, and caller assumptions.

Optional classifier, literature, model, and pathology-concordance outputs are hypotheses or compatibility summaries. They do not validate pathology and must be reviewed against primary CNA tables, morphology, IHC, cytogenetics, clinical-grade sequencing, and other appropriate assays.

## Data governance and privacy

- Use de-identified research identifiers in samplesheets and pathology tables.
- Include only pathology columns needed for the planned analysis.
- Do not send identifiable clinical text to network services or public issue trackers.
- Confirm institutional approval, consent/data-use conditions, and computing policy before analysis.
- Treat public cell-line/tutorial results as software demonstrations, not clinical validation.

## Licensing and reuse

The repository currently does not include a standalone `LICENSE` file. Do not assume that public visibility grants unrestricted redistribution or commercial reuse. Contact the repository owner for licensing clarification, and follow the licenses/citation requirements of every bundled or downloaded dependency and dataset.

FFPERASE has additional conditions: its
[pinned license](https://github.com/papaemmelab/nf-ffperase/blob/b0dd56cbd0a939896a966b9ce30c4d719b158170/LICENSE)
requires MSK's express written permission to publish research results. OncoTracer's
download acknowledgment does not grant that permission. ANNOVAR software/databases
remain separately obtained under their own terms.
