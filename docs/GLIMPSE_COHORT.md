# GLIMPSE2 cohort VCF export

`oncotracer_cli.glimpse_joint.merge_cohort` merges the complete, annotated sample exports after evidence classification and FFPE review. It writes:

- `cohort.all_evidence.vcf.gz` and tabix index: all evidence categories, original sample genotypes and per-sample annotations.
- `cohort.germline_population.vcf.gz` and tabix index: germline-supported primary genotypes with somatic, unresolved and explicitly excluded sample genotypes masked.
- `cohort.manifest.json`: input identity, thresholds, per-sample counts, specimen roles, exclusions, commands and output/index SHA256 checksums.

This is a merge of independently called genotypes, not joint variant calling. An absent record stays missing. It never becomes `0/0` just because another sample has an alternate allele. Optional full GLIMPSE chromosome files can fill an absent record only at the exact chromosome, position, reference and alternate allele, with a valid called-genotype posterior of at least 0.90 and imputation INFO of at least 0.80. Existing unresolved, somatic and FFPE decisions are never overwritten. Off-panel sites remain missing.

All records are normalized against the same indexed reference. Multiallelic records are separated by alternate allele, with other alternate copies represented as missing. Thus a phased `1|2` at REF=A, ALT=G,T becomes `1|.` for A>G and `.|1` for A>T. These are allele-specific rows, not a reconstructed multiallelic genotype. Primary phase, carrier interpretation and evidence remain sample-specific FORMAT fields. ClinVar and available functional annotations stay in INFO. Conflicting common annotations, incompatible reference dictionaries, duplicate sample identities and duplicate normalized alleles fail explicitly.

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
