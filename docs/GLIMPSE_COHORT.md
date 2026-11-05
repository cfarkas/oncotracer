# GLIMPSE2 cohort VCF export

`oncotracer_cli.glimpse_joint.merge_cohort` merges the complete, annotated sample exports after evidence classification and FFPE review. It writes:

- `cohort.all_evidence.vcf.gz` and tabix index: all evidence categories, original sample genotypes and per-sample annotations.
- `cohort.germline_population.vcf.gz` and tabix index: germline-supported primary genotypes with somatic, unresolved and explicitly excluded sample genotypes masked.
- `cohort.manifest.json`: input identity, thresholds, per-sample counts, specimen roles, exclusions, commands and output/index SHA256 checksums.

This is a merge of independently called genotypes, not joint variant calling. An absent record stays missing. It never becomes `0/0` just because another sample has an alternate allele. Optional full GLIMPSE chromosome files can fill an absent record only at the exact chromosome, position, reference and alternate allele, with a valid called-genotype posterior of at least 0.90 and imputation INFO of at least 0.80. Existing unresolved, somatic and FFPE decisions are never overwritten. Off-panel sites remain missing.

All records are normalized against the same indexed reference. Multiallelic records are separated by alternate allele, with other alternate copies represented as missing. Thus a phased `1|2` at REF=A, ALT=G,T becomes `1|.` for A>G and `.|1` for A>T. These are allele-specific rows, not a reconstructed multiallelic genotype. Primary phase, carrier interpretation and evidence remain sample-specific FORMAT fields. Shared ClinVar annotations stay in INFO. The available caller or imputation annotations in `ANN_*` remain sample-specific FORMAT fields: one sample can have a caller gene symbol while another has a ClinVar gene/ID fallback for the same allele. Neither value replaces the other. Conflicting common annotations, incompatible reference dictionaries, duplicate sample identities and duplicate normalized alleles fail explicitly.

Since 2.3.1rc2, cohort construction can verify equivalent ClinVar normalization
files whose compressed-file hashes differ because bcftools recorded different
command timestamps or paths. The original `LPWGSClinVarSHA256` in each sample
VCF must match the actual retained normalization file. A separate content hash
then compares all decompressed bytes except `##bcftools_*Command=` header lines.
Release metadata, field definitions, tool versions and every annotation record
remain part of this comparison. A different release, changed annotation, missing
proof or checksum mismatch still stops the merge.

The cohort header records `LPWGSClinVarContentSHA256`, its versioned policy, and
each sample's original raw checksum in `OncoTracerClinVarSource`. The manifest
retains source paths, raw/content hashes, record counts and excluded command
headers. Sample VCFs and their original provenance are never rewritten. The
controller locates normalization files from completed chromosome annotation
receipts. Direct Python callers can provide `clinvar_by_sample={sample: path}`;
without this evidence the strict raw-checksum comparison remains in force.

Maintenance continuation retains an old bundled workflow path only when its
complete code/data inventory is byte-identical to the newly installed bundle.
It may retain an earlier smaller memory cap within the current allocation.
Previously completed sample workflows can bypass rerunning Nextflow only after
normal resume preflight passes, the full runtime is unchanged, and the final VCF
checksum and runtime-bound completion summary validate. Interrupted workflows
continue through the usual per-stage checks. A cohort error is now explicitly
reported as `cohort_status: failed` with overall `partial_failure`.

The population VCF requires `EVCLASS=germline_supported` and `FT=PASS`. `GFACCEPT=0`, `GFRESCUE=1` or `GFPOP=0` also masks the sample genotype. FFPE/imputation rescue candidates remain in the complete audit VCF and require confirmation before population inclusion. `AC`, `AN`, `AF`, `NS` and `N_MISSING` are recalculated from the retained primary genotypes. `AF` describes called alleles in this selected cohort, not an ancestry-calibrated population frequency. Missing and partial genotypes require appropriate downstream QC.

Specimen roles are preserved in `##SAMPLE` metadata, `FORMAT/ROLE` and the manifest. A tumor role alone does not exclude its germline-supported calls. When an explicit tumor-to-matched-normal mapping identifies two specimens from the same person, the backend passes a population exclusion for the tumor to avoid counting that individual twice. The tumor's original genotype remains in the all-evidence VCF; the population genotype becomes missing with an explicit `MASKREASON`. Other within-person duplicates or relatedness require study metadata and downstream checks.

```python
from pathlib import Path
from oncotracer_cli.glimpse_joint import merge_cohort

manifest = merge_cohort(
    samples={"T": Path("T.annotated.vcf.gz"), "N": Path("N.annotated.vcf.gz")},
    output_dir=Path("cohort"),
    bcftools="bcftools",
    reference=Path("GRCh38.fa"),
    threads=4,
    imputed_by_sample={"T": [Path("T.chr1.imputed.bcf")],
                       "N": [Path("N.chr1.imputed.bcf")]},
    sample_roles={"T": "tumor", "N": "normal"},
    population_excluded_samples={"T": "matched_normal_from_same_individual_included"},
)
```

The full workflow supplies the completed chromosome files automatically. Parsing streams by chromosome and coordinate without a genome-wide genotype cache. Output publication occurs only after parsing, reference validation and index creation; reuse checks output and index hashes. All original inputs remain unchanged.
