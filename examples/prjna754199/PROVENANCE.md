# PRJNA754199 archive provenance

The manifest was frozen from the ENA Portal API on **2026-07-15** by joining the read-run report with the sample-alias report.

Read-run report:

```text
https://www.ebi.ac.uk/ena/portal/api/filereport?accession=PRJNA754199&result=read_run&fields=study_accession,sample_accession,secondary_sample_accession,experiment_accession,run_accession,instrument_platform,instrument_model,library_name,library_layout,library_strategy,library_source,library_selection,read_count,base_count,fastq_bytes,fastq_ftp,fastq_md5,sample_title,first_public,last_updated&format=tsv&download=true
```

Sample-alias report:

```text
https://www.ebi.ac.uk/ena/portal/api/search?result=sample&query=study_accession%3D%22PRJNA754199%22&fields=sample_accession,secondary_sample_accession,sample_alias,sample_title,sample_description,scientific_name,sex,age,tissue_type,disease,isolation_source,first_public,last_updated&format=tsv&limit=0
```

Primary records:

- [NCBI BioProject PRJNA754199](https://www.ncbi.nlm.nih.gov/bioproject/754199)
- [ENA PRJNA754199 browser record](https://www.ebi.ac.uk/ena/browser/view/PRJNA754199)
- [Associated PLOS ONE article](https://doi.org/10.1371/journal.pone.0262272)

Inventory invariants checked by `run_example.sh`:

| Field | Pinned value |
| --- | ---: |
| Public runs | 12 |
| Library layout | single-end for every run |
| Bases per deposited read | 36 for every run |
| Total reads | 266,097,582 |
| Total bases | 9,579,512,952 |
| Compressed FASTQ bytes | 6,171,900,300 (5.75 GiB) |

The article's study cohort and the current archive inventory are not equivalent. The
article reports 41 plasma specimens from 15 patients, whereas the public API returns 12
runs whose submitter aliases are all `DDLPS_*` or `WDLPS_*`. The example neither invents
the missing 29 FASTQs nor labels the 12 aliases as 12 independent patients.

The manifest preserves public sample aliases but does not treat them as independently
verified diagnoses. Public sex/age metadata are not needed for this software tutorial
and are deliberately omitted from the generated analysis samplesheet. The generated
`tumor` status is a workflow condition label for patient-cohort libraries, not a claim
about tumor presence, tumor fraction, or MDM2 amplification in a particular specimen.

For a shareable result, retain the generated `run_provenance.tsv`, unedited YAML and
samplesheet, Nextflow report/trace, SAMURAI `pipeline_info`, reference identity, container
digest, OncoTracer commit, and source-file checksums. Static gallery exports should point
back to those files and identify the exact source table or PDF used to generate each
image.
