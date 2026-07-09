#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

def blank(value) {
  return value == null || value.toString().trim() == ''
}

def asBool(value) {
  if( value == null ) return false
  if( value instanceof Boolean ) return value
  return ['true', 't', '1', 'yes', 'y', 'on'].contains(value.toString().trim().toLowerCase())
}

def requireParam(name, value) {
  if( blank(value) ) {
    error "Missing required parameter: --${name}"
  }
}

def projectBinDir() {
  return "${projectDir}/bin"
}

def scriptPath(rel) {
  def bundled = file("${projectBinDir()}/${rel}")
  if( bundled.exists() ) {
    return bundled.toString()
  }
  return "${params.lpwgs_root}/${rel}"
}

def workflowOutdir() {
  requireParam('outdir', params.outdir)
  return params.outdir.toString()
}

def datasetName() {
  if( params.mode.toString() == 'ont' ) {
    return "ONT_ichorcna_${params.ont_binsize_kb}kb"
  }
  if( params.mode.toString() == 'illumina' ) {
    return "illumina_qdnaseq_${params.illumina_binsize_kb}kb"
  }
  error "Unsupported --mode '${params.mode}'. Use --mode ont or --mode illumina."
}

workflow {
  main:
  requireParam('mode', params.mode)
  def mode = params.mode.toString()
  if( !['ont', 'illumina'].contains(mode) ) {
    error "Unsupported --mode '${mode}'. Use --mode ont or --mode illumina."
  }

  def runtimeFlags = [asBool(params.docker), asBool(params.singularity), asBool(params.conda)].count { it }
  if( runtimeFlags > 1 ) {
    error "Use only one runtime flag: --docker, --singularity, or --conda."
  }

  def outRoot = workflowOutdir()
  def ds = datasetName()

  Channel.value([mode, outRoot, ds]).set { run_meta_ch }

  if( mode == 'illumina' ) {
    RUN_ILLUMINA_SAMURAI(run_meta_ch)
    RUN_BAM_REFINE(RUN_ILLUMINA_SAMURAI.out.run_meta)
  } else {
    RUN_ONT_SAMURAI(run_meta_ch)
    RUN_BAM_REFINE(RUN_ONT_SAMURAI.out.run_meta)
  }

  RUN_CNA_CODIFICATION(RUN_BAM_REFINE.out.run_meta)
  RUN_CNA_CUSTOM_PLOTS(RUN_CNA_CODIFICATION.out.run_meta)

  if( asBool(params.run_cna_classifier) ) {
    RUN_CNA_CLASSIFIER(RUN_CNA_CUSTOM_PLOTS.out.run_meta)
  }

  WRITE_SUMMARY(RUN_CNA_CUSTOM_PLOTS.out.run_meta)
}

process RUN_ILLUMINA_SAMURAI {
  tag 'illumina_samurai'
  container null
  publishDir { "${params.outdir}/01_samurai_illumina" }, mode: 'copy', overwrite: true

  input:
  val run_meta

  output:
  val run_meta, emit: run_meta
  path 'samurai_illumina_done.txt', emit: marker

  script:
  requireParam('illumina_samplesheet', params.illumina_samplesheet)
  requireParam('illumina_samurai_outdir', params.illumina_samurai_outdir)

  def samuraiScript = scriptPath('scripts/run_illumina_samurai_fastq.sh')
  def forceOpt = asBool(params.force) ? '--force' : ''

  """
  set -Eeuo pipefail
  bash '${samuraiScript}' \\
    --samplesheet '${params.illumina_samplesheet}' \\
    --outdir '${params.illumina_samurai_outdir}' \\
    --analysis_type '${params.illumina_analysis_type}' \\
    --caller '${params.illumina_caller}' \\
    --binsize '${params.illumina_binsize_kb}' \\
    --lpwgs-root '${params.lpwgs_root}' \\
    ${forceOpt}

  test -d '${params.illumina_samurai_outdir}/qdnaseq'
  test -d '${params.illumina_samurai_outdir}/alignment'
  echo "Illumina SAMURAI completed: ${params.illumina_samurai_outdir}" > samurai_illumina_done.txt
  """

  stub:
  """
  echo "STUB: Illumina SAMURAI" > samurai_illumina_done.txt
  """
}

process RUN_ONT_SAMURAI {
  tag 'ont_samurai'
  container null
  publishDir { "${params.outdir}/01_samurai_ont" }, mode: 'copy', overwrite: true

  input:
  val run_meta

  output:
  val run_meta, emit: run_meta
  path 'samurai_ont_done.txt', emit: marker

  script:
  requireParam('ont_folder', params.ont_folder)
  requireParam('ont_barcodes', params.ont_barcodes)
  requireParam('ont_samurai_outdir', params.ont_samurai_outdir)

  def samuraiScript = scriptPath('scripts/run_ont_samurai_barcodes.sh')
  def sampleNamesOpt = blank(params.ont_sample_names) ? '' : "--sample-names '${params.ont_sample_names}'"
  def refOpt = blank(params.ont_ref) ? '' : "--ref '${params.ont_ref}'"
  def normalOpt = ''
  if( !blank(params.ont_normal_folder) ) {
    normalOpt += " --normal-folder '${params.ont_normal_folder}'"
  }
  if( !blank(params.ont_normal_barcodes) ) {
    normalOpt += " --normal-barcodes '${params.ont_normal_barcodes}'"
  }
  if( !blank(params.ont_normal_sample_names) ) {
    normalOpt += " --normal-sample-names '${params.ont_normal_sample_names}'"
  }
  def ponOpt = asBool(params.ont_build_pon) ? '--build-pon' : ''
  def realignOpt = asBool(params.ont_force_realign) ? '--force-realign' : ''

  """
  set -Eeuo pipefail
  bash '${samuraiScript}' \\
    --folder '${params.ont_folder}' \\
    --barcodes '${params.ont_barcodes}' \\
    ${sampleNamesOpt} \\
    --outdir '${params.ont_samurai_outdir}' \\
    --analysis_type '${params.ont_analysis_type}' \\
    --caller '${params.ont_caller}' \\
    --binsize '${params.ont_binsize_kb}' \\
    --min-age-minutes '${params.ont_min_age_minutes}' \\
    ${refOpt} \\
    ${normalOpt} \\
    ${ponOpt} \\
    ${realignOpt}

  test -d '${params.ont_samurai_outdir}'
  echo "ONT SAMURAI completed: ${params.ont_samurai_outdir}" > samurai_ont_done.txt
  """

  stub:
  """
  echo "STUB: ONT SAMURAI" > samurai_ont_done.txt
  """
}

process RUN_BAM_REFINE {
  tag { run_meta[2] }
  publishDir { "${params.outdir}/02_bam_refinement" }, mode: 'copy', overwrite: true

  input:
  val run_meta

  output:
  val run_meta, emit: run_meta
  path 'bam_refinement_done.txt', emit: marker

  script:
  def mode = run_meta[0]
  def outRoot = run_meta[1]
  def refineOut = "${outRoot}/02_bam_refinement"
  def refineScript = scriptPath('scripts/bam_cnv_boundary_refine.sh')
  def forceOpt = asBool(params.force) ? '--force' : ''
  def skipInstallOpt = asBool(params.refine_skip_install) ? '--skip-install' : ''

  def modeArgs
  if( mode == 'ont' ) {
    def ichorDir = "${params.ont_samurai_outdir}/results/ichorcna"
    def bamDir = "${params.ont_samurai_outdir}/bam"
    def priorSeg = "${ichorDir}/segments_logR_corrected_gistic.seg"
    modeArgs = """--mode ont \\
      --ont-ichor-dir '${ichorDir}' \\
      --ont-bam-dir '${bamDir}' \\
      --ont-prior-seg '${priorSeg}' \\
      --ont-binsize-kb '${params.ont_binsize_kb}' \\
      --fine-bin-kb-ont '${params.fine_bin_kb_ont}' \\
      --coverage-mode-ont bases \\
      --normal-samples auto \\
      --pon-mode auto \\
      --min-local-log2-diff '${params.min_local_log2_diff_ont}' \
    """
  } else {
    def qdnaseqDir = "${params.illumina_samurai_outdir}/qdnaseq"
    def bamDir = "${params.illumina_samurai_outdir}/alignment"
    def priorSeg = "${qdnaseqDir}/all_segments.seg"
    modeArgs = """--mode illumina \\
      --illumina-qdnaseq-dir '${qdnaseqDir}' \\
      --illumina-bam-dir '${bamDir}' \\
      --illumina-prior-seg '${priorSeg}' \\
      --illumina-binsize-kb '${params.illumina_binsize_kb}' \\
      --fine-bin-kb-illumina '${params.fine_bin_kb_illumina}' \\
      --coverage-mode-illumina starts \\
      --normal-samples none \\
      --pon-mode off \\
      --min-local-log2-diff '${params.min_local_log2_diff_illumina}' \
    """
  }

  """
  set -Eeuo pipefail
  mkdir -p '${refineOut}'
  bash '${refineScript}' \\
    ${modeArgs} \\
    --outdir '${refineOut}' \\
    --search-radius-bins '${params.search_radius_bins}' \\
    --min-mapq '${params.min_mapq}' \\
    --min-adjacent-seg-delta '${params.min_adjacent_seg_delta}' \\
    --min-bic-gain '${params.min_bic_gain}' \\
    --permutations '${params.permutations}' \\
    --permutation-p '${params.permutation_p}' \\
    --accept-rule '${params.accept_rule}' \\
    --max-ci-fraction-of-coarse '${params.max_ci_fraction_of_coarse}' \\
    --zipcnv-mode '${params.zipcnv_mode}' \\
    --zipcnv-window-bins '${params.zipcnv_window_bins}' \\
    --zipcnv-k '${params.zipcnv_k}' \\
    --zipcnv-min-segment-bins '${params.zipcnv_min_segment_bins}' \\
    --zipcnv-min-abs-log2 '${params.zipcnv_min_abs_log2}' \\
    --zipcnv-compare-min-overlap '${params.zipcnv_compare_min_overlap}' \\
    ${forceOpt}

  test -s '${refineOut}/${run_meta[2]}/04_final_results/final_segments.tsv'
  test -d '${refineOut}/${run_meta[2]}/04_final_results/cna_cytogenomic_input/qdnaseq_bins'
  echo "BAM refinement completed: ${refineOut}/${run_meta[2]}" > bam_refinement_done.txt
  """

  stub:
  """
  echo "STUB: BAM refinement" > bam_refinement_done.txt
  """
}

process RUN_CNA_CODIFICATION {
  tag { run_meta[2] }
  publishDir { "${params.outdir}/03_cna_codification" }, mode: 'copy', overwrite: true

  input:
  val run_meta

  output:
  val run_meta, emit: run_meta
  path 'cna_codification_done.txt', emit: marker

  script:
  def outRoot = run_meta[1]
  def ds = run_meta[2]
  def codifyScript = scriptPath('cna_codification/scripts/cna_to_cytogenomic_notation.py')
  def cytoband = scriptPath('cna_codification/resources/hg38.cytoBand.txt.gz')
  def codifyOut = "${outRoot}/03_cna_codification"
  def inputBins = "${outRoot}/02_bam_refinement/${ds}/04_final_results/cna_cytogenomic_input/qdnaseq_bins"

  """
  set -Eeuo pipefail
  mkdir -p '${codifyOut}'

  python '${codifyScript}' \\
    --qdnaseq \\
    --input-dir '${inputBins}' \\
    --cytoband '${cytoband}' \\
    --outdir '${codifyOut}' \\
    --prefix cna

  test -s '${codifyOut}/cna_events.tsv'
  test -s '${codifyOut}/cna_cytogenomic_notation.tsv'
  echo "CNA codification completed: ${codifyOut}" > cna_codification_done.txt
  """

  stub:
  """
  echo "STUB: CNA codification" > cna_codification_done.txt
  """
}


process RUN_CNA_CUSTOM_PLOTS {
  tag { run_meta[2] }
  publishDir { "${params.outdir}/04_cna_custom_plots" }, mode: 'copy', overwrite: true

  input:
  val run_meta

  output:
  val run_meta, emit: run_meta
  path 'cna_custom_plots_done.txt', emit: marker

  script:
  def outRoot = run_meta[1]
  def ds = run_meta[2]
  def plotScript = scriptPath('cna_codification/scripts/plot_cna_events.py')
  def cytoband = scriptPath('cna_codification/resources/hg38.cytoBand.txt.gz')
  def events = "${outRoot}/03_cna_codification/cna_events.tsv"
  def plotsOut = "${outRoot}/04_cna_custom_plots"
  def binsTable = "${outRoot}/02_bam_refinement/${ds}/01_tables/refined_bins.tsv.gz"

  """
  set -Eeuo pipefail
  mkdir -p '${plotsOut}'

  python '${plotScript}' \\
    --events '${events}' \\
    --cytoband '${cytoband}' \\
    --outdir '${plotsOut}' \\
    --bins '${binsTable}' \\
    --profile-sample all

  test -s '${plotsOut}/cna_per_sample_pages.pdf'
  test -s '${plotsOut}/cna_log2_ratio_profiles_all_samples.pdf'
  echo "CNA custom plots completed: ${plotsOut}" > cna_custom_plots_done.txt
  """

  stub:
  """
  echo "STUB: CNA custom plots" > cna_custom_plots_done.txt
  """
}

process RUN_CNA_CLASSIFIER {
  tag 'cna_classifier'
  publishDir { "${params.outdir}/05_cna_classifier" }, mode: 'copy', overwrite: true

  input:
  val run_meta

  output:
  path 'cna_classifier_done.txt', emit: marker

  script:
  def outRoot = run_meta[1]
  def classifierDir = scriptPath('cna_classifier_nf')
  def pathologyCsv = blank(params.pathology_csv) ? '' : params.pathology_csv.toString()

  """
  set -Eeuo pipefail
  EXTRA_ARGS=()
  PATHOLOGY_CSV='${pathologyCsv}'
  if [ -n "\$PATHOLOGY_CSV" ]; then
    EXTRA_ARGS+=(--pathology "\$PATHOLOGY_CSV")
    EXTRA_ARGS+=(--pathology_sample_col '${params.pathology_sample_col}')
    EXTRA_ARGS+=(--pathology_case_col '${params.pathology_case_col}')
    EXTRA_ARGS+=(--pathology_diagnosis_col '${params.pathology_diagnosis_col}')
    EXTRA_ARGS+=(--pathology_use_biomed_models '${asBool(params.pathology_use_biomed_models) ? 'true' : 'false'}')
    EXTRA_ARGS+=(--pathology_biomed_local_files_only '${asBool(params.pathology_biomed_local_files_only) ? 'true' : 'false'}')
  fi

  nextflow run '${classifierDir}/main.nf' -profile '${params.cna_classifier_profile}' \\
    --input '${outRoot}/03_cna_codification' \\
    --outdir '${outRoot}/05_cna_classifier' \\
    --sample_set '${params.cna_classifier_sample_set}' \
    "\${EXTRA_ARGS[@]}"

  echo "CNA classifier completed: ${outRoot}/05_cna_classifier" > cna_classifier_done.txt
  """

  stub:
  """
  echo "STUB: CNA classifier" > cna_classifier_done.txt
  """
}

process WRITE_SUMMARY {
  tag 'summary'
  publishDir { "${params.outdir}/06_workflow_summary" }, mode: 'copy', overwrite: true

  input:
  val run_meta

  output:
  path 'workflow_summary.txt'

  script:
  def outRoot = run_meta[1]
  def ds = run_meta[2]

  """
  set -Eeuo pipefail
  mkdir -p '${outRoot}/06_workflow_summary'
  cat > workflow_summary.txt <<EOF
mode=${run_meta[0]}
dataset=${ds}
outdir=${outRoot}
bam_refinement=${outRoot}/02_bam_refinement/${ds}
cna_codification=${outRoot}/03_cna_codification
cna_events=${outRoot}/03_cna_codification/cna_events.tsv
cna_custom_plots=${outRoot}/04_cna_custom_plots
cna_notation=${outRoot}/03_cna_codification/cna_cytogenomic_notation.tsv
EOF
  cp workflow_summary.txt '${outRoot}/06_workflow_summary/workflow_summary.txt'
  """
}

