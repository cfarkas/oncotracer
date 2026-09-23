# Results gallery

The images below show saved OncoTracer output. A plot can exist before the whole
workflow finishes; use the run summary to confirm completion. These examples
show CNA profiles, not validated diagnoses.

## One-sample Illumina public test

**Provenance:** ENA run `ERR12341627`, processed by the public Illumina branch in QuickStart Example 1 with qDNAseq at 100 kb. See [QuickStart Example 1](quick_start.md) for the reproducible command and generated YAML.

**Terminal / headless reproduction:** after the [QuickStart 1 download/checks](quick_start.md#1-download-and-verify-the-reads), configure a new project and run:

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --non-interactive \
  --project "$PWD/oncotracer-quickstart1/illumina" --mode illumina --analysis cna \
  --sample-name ERR12341627 --status tumor \
  --fastq-1 "$PWD/oncotracer-quickstart1/input/illumina/ERR12341627_1.fastq.gz" \
  --fastq-2 "$PWD/oncotracer-quickstart1/input/illumina/ERR12341627_2.fastq.gz" \
  --backend conda --threads 4
oncotracer check --config "$PWD/oncotracer-quickstart1/illumina/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/illumina/config/run.yml"
```

[Open the original qDNAseq fitted-segment plot PDF](assets/gallery/illumina_samurai_qdnaseq_segment_plot.pdf).

![Public Illumina ERR12341627 SAMURAI qDNAseq profile with fitted copy-number segments](assets/gallery/illumina_samurai_qdnaseq_segment_plot.png)

**How to read it:** black points are normalized qDNAseq bins; orange horizontal lines are the fitted segment means produced by SAMURAI. Genomic position runs across the chromosomes. Final refined segments and event calls remain available in stages 02 and 03.

## ONT ichorCNA-derived test result

**Provenance:** public ONT run `DRR165691`, processed by the ONT branch in QuickStart Example 1 with ichorCNA-derived 500 kb inputs.

The run produces ichorCNA depth and segment tables. OncoTracer renders the profile from those tables even when an upstream plotting helper encounters missing depth values.

**Terminal / headless reproduction**, using the verified ONT download from QuickStart 1:

```bash
cd /path/to/my/analyses_dir/
oncotracer setup --non-interactive \
  --project "$PWD/oncotracer-quickstart1/ont" --mode ont --analysis cna \
  --reads-folder "$PWD/oncotracer-quickstart1/input/fastq_pass" \
  --barcodes barcode01 --sample-names DRR165691 --backend conda --threads 4
oncotracer check --config "$PWD/oncotracer-quickstart1/ont/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart1/ont/config/run.yml"
```

[Open the original ichorCNA-derived profile PDF](assets/gallery/ont_ichorcna_derived_profile.pdf).

![Public ONT DRR165691 ichorCNA-derived copy-number profile](assets/gallery/ont_ichorcna_derived_profile.png)

**How to read it:** black points are bin-level log2 ratios; colored horizontal lines are the fitted ichorCNA-derived segment means on the same log2 scale. The chromosome axis shows only chromosomes represented in the sample, so empty X/Y panels are omitted. Broad changes are more defensible than isolated noisy points in low-pass data. Review the used/skipped FASTQ logs, coverage, segment table, and tumor fraction before biological interpretation.

## Final OncoTracer Illumina visualizations

These are presentation views derived from `03_cna_codification/cna_events.tsv` and the refined bin table.

![Illumina CNA genome overview](assets/gallery/illumina_cna_genome_overview.png)

![Illumina CNA event counts by sample](assets/gallery/illumina_cna_event_counts_by_sample.png)

![Illumina recurrent cytobands](assets/gallery/illumina_cna_recurrent_cytobands.png)

## Final OncoTracer ONT visualizations

![ONT CNA genome overview](assets/gallery/ont_cna_genome_overview.png)

![ONT CNA event counts by sample](assets/gallery/ont_cna_event_counts_by_sample.png)

![ONT recurrent cytobands](assets/gallery/ont_cna_recurrent_cytobands.png)

## HCC1143 three-library, six-FASTQ public cohort

!!! warning "Verified gallery artifact pending"
    The complete checksum-validated native command is documented, but
    this section intentionally does not claim a cohort result until the
    complete run, output checks, provenance record, and gallery export have
    all been verified. The maintainer will replace this notice with the actual
    plot and measured result summary after that run.

The example contains three paired-end LP-WGS libraries (six physical FASTQ files) from the HCC1143 triple-negative breast-cancer cell line.

| Provenance field | Value |
| --- | --- |
| Public project | [PRJNA454331](https://www.ebi.ac.uk/ena/browser/view/PRJNA454331) |
| Associated study | [Ben-David et al., Nature Communications (2018)](https://doi.org/10.1038/s41467-018-05729-w) |
| Libraries/runs | DMSO `SRR7085656`; BEZ235 `SRR7085655`; Trametinib `SRR7085657` |
| Physical FASTQs | 6: one R1/R2 pair for each of 3 libraries |
| Experimental status | All are `TUMOR`; DMSO is a treatment control, not a normal genome |
| Download validation | Exact ENA byte count, ENA MD5, and `gzip -t`; values stored in `examples/hcc1143_lpwgs/manifest.tsv` |
| Complete reproduction guide | [QuickStart Example 2](public_cohort.md) |
| Analysis command | Follow the download, samplesheet, setup and check steps in [QuickStart 2](public_cohort.md), then run `oncotracer run --backend conda --config /absolute/path/oncotracer-quickstart2/analysis/config/run.yml` |
| Expected result source | `/path/to/my/analyses_dir/oncotracer-quickstart2/analysis/results/04_cna_custom_plots/cna_log2_ratio_profiles_all_samples.pdf` |
| OncoTracer commit | _to be recorded after verified run_ |
| Container digest | _to be recorded after verified run_ |
| Reference/caller/bin size | _to be recorded after verified run_ |
| Run completion and checks | _to be recorded after verified run_ |
| Biological interpretation | _not reported before QC and verified tables are available_ |

**Terminal / headless reproduction:** after the [six downloads and checksum checks](public_cohort.md#1-download-and-verify-the-reads), run from your analysis directory:

```bash
cd /path/to/my/analyses_dir/
mkdir -p "$PWD/oncotracer-quickstart2/input"
cat > "$PWD/oncotracer-quickstart2/input/samplesheet.csv" <<CSV
sample,fastq_1,fastq_2,status
HCC1143_DMSO,"$PWD/oncotracer-quickstart2/input/HCC1143_DMSO_R1.fastq.gz","$PWD/oncotracer-quickstart2/input/HCC1143_DMSO_R2.fastq.gz",tumor
HCC1143_BEZ235,"$PWD/oncotracer-quickstart2/input/HCC1143_BEZ235_R1.fastq.gz","$PWD/oncotracer-quickstart2/input/HCC1143_BEZ235_R2.fastq.gz",tumor
HCC1143_TRAMETINIB,"$PWD/oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R1.fastq.gz","$PWD/oncotracer-quickstart2/input/HCC1143_TRAMETINIB_R2.fastq.gz",tumor
CSV
oncotracer setup --non-interactive \
  --project "$PWD/oncotracer-quickstart2/analysis" \
  --mode illumina --analysis cna --backend conda \
  --samplesheet "$PWD/oncotracer-quickstart2/input/samplesheet.csv" \
  --hg38_build --threads 4
oncotracer check --config "$PWD/oncotracer-quickstart2/analysis/config/run.yml"
oncotracer run --backend conda \
  --config "$PWD/oncotracer-quickstart2/analysis/config/run.yml"
```

These are execution instructions, not a claim that the pending gallery artifacts
have been validated. Skip setup when resuming an already configured project.

When populated, this gallery entry must distinguish observed signal from inference: report sample names, caller/bin size, QC warnings, number of CNA events, broad profile similarities/differences, and important limitations. Treatment-associated causality must not be inferred from this three-library demonstration alone.

See the example's [provenance and resource notes](https://github.com/cfarkas/oncotracer/tree/main/examples/hcc1143_lpwgs) and [Output files](outputs.md) for the tables behind every plot.
