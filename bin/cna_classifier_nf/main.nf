#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

// Package build: v9 - deep PubMed/Europe-PMC literature ranking and clinician driver reports

/*
 * Cancer-agnostic CNA classifier from SAMURAI/QDNAseq CNA codification outputs.
 * Required input: --input, --cna_input, or --cna_events pointing to a cna_events.tsv file or a folder.
 * Optional input: cna_cytogenomic_notation.tsv to preserve CNA-flat samples.
 *
 * GISTIC2 is included as a built-in workflow branch:
 *   - RUN_GISTIC uses envs/gistic2.yml when -profile conda is enabled.
 *   - --gistic_refgene auto downloads the hg38 refgene into the GISTIC work directory.
 *   - Missing GISTIC/refgene continues by default unless --gistic_required true.
 */

/*
 * Nextflow >=26.04 uses the stricter v2 parser by default.
 * Therefore all executable statements must live inside workflow/process/function
 * declarations. Also normalize command-line booleans, because values such as
 * --gistic_required false may arrive as strings with the v2 parser.
 */
def isBlankParam(value) {
  return value == null || value.toString().trim() == ''
}

def asBool(value) {
  if( value == null ) return false
  if( value instanceof Boolean ) return value
  def s = value.toString().trim().toLowerCase()
  return ['true', 't', '1', 'yes', 'y', 'on'].contains(s)
}


def builtinLymphomaSamples() {
  return 'V480,Y2119,U4333,O4789,E4904,X4999,A5465,B5924,K6537,A6566,S6922,Q7164,L7395,N7591,B8017,E9211,M9702,C10174,G11079,P11670,R13729'
}

def sampleSetRaw() {
  return isBlankParam(params.sample_set) ? 'broad_cancer' : params.sample_set.toString().trim()
}

def sampleSetKey() {
  def raw = sampleSetRaw()
  // Accept --sample_set breast:S1,S2 or --sample_set breast=S1,S2 as a compact way
  // to set both cancer context and the sample filter while keeping --sample_set as
  // the only sample-set-specific flag.
  def head = raw
  if( raw.contains(':') ) head = raw.split(':', 2)[0]
  if( raw.contains('=') ) head = raw.split('=', 2)[0]
  def key = head.toString().trim().toLowerCase().replaceAll('[^a-z0-9]+', '_').replaceAll('^_+|_+$', '')
  if( ['pan','pancancer','pan_cancer','broad','broad_cancer','all','all_cancers','generic','solid','solid_tumor','solid_tumours','tumor','tumour'].contains(key) ) return 'broad_cancer'
  if( ['lymphoma','lymphomas','dlbcl','b_cell_lymphoma','bcell_lymphoma','hematolymphoid'].contains(key) ) return 'lymphoma'
  if( ['brain','brain_cns','cns','glioma','glioblastoma','astrocytoma','meningioma','pediatric_glioma','low_grade_glioma'].contains(key) ) return 'brain_cns'
  if( ['breast','breast_cancer','mammary'].contains(key) ) return 'breast'
  if( ['pancreas','pancreatic','pancreatic_cancer','pancreatobiliary','cholangiocarcinoma','biliary'].contains(key) ) return 'pancreas'
  if( ['colon','colorectal','crc','rectal','rectum'].contains(key) ) return 'colorectal'
  if( ['leukemia','leukaemia','aml','all','mds','myeloid','myeloid_neoplasm','hematologic','haematologic'].contains(key) ) return 'leukemia'
  if( ['lung','nsclc','sclc','pulmonary'].contains(key) ) return 'lung'
  if( ['prostate','prostatic'].contains(key) ) return 'prostate'
  if( ['ovarian','ovary','fallopian_tube','peritoneal','hgsoc'].contains(key) ) return 'ovarian'
  if( ['endometrial','endometrium','uterine'].contains(key) ) return 'endometrial'
  if( ['gastric','stomach','gastroesophageal','gej','esophageal','oesophageal'].contains(key) ) return 'gastric_esophageal'
  if( ['sarcoma','soft_tissue','gist','liposarcoma','leiomyosarcoma','osteosarcoma'].contains(key) ) return 'sarcoma'
  if( ['kidney','renal','rcc','clear_cell_rcc','ccrcc'].contains(key) ) return 'renal'
  if( ['bladder','urothelial','urinary_tract'].contains(key) ) return 'urothelial'
  if( ['thyroid'].contains(key) ) return 'thyroid'
  if( ['melanoma'].contains(key) ) return 'melanoma'
  if( ['liver','hcc','hepatocellular'].contains(key) ) return 'liver'
  if( ['head_neck','hnscc','oral','oropharyngeal','laryngeal'].contains(key) ) return 'head_neck'
  if( ['germ_cell','testicular','seminoma','nonseminoma'].contains(key) ) return 'germ_cell'
  if( ['myeloma','multiple_myeloma','plasma_cell'].contains(key) ) return 'myeloma'
  if( ['neuroblastoma'].contains(key) ) return 'neuroblastoma'
  if( ['neuroendocrine','net','neuroendocrine_tumor'].contains(key) ) return 'neuroendocrine'
  if( ['pediatric','paediatric','pediatric_solid','paediatric_solid'].contains(key) ) return 'pediatric_solid'
  return key ?: 'broad_cancer'
}

def sampleSetEmbeddedSamples() {
  def raw = sampleSetRaw()
  if( raw.contains(':') ) return raw.split(':', 2)[1].trim()
  if( raw.contains('=') ) return raw.split('=', 2)[1].trim()
  return ''
}

def sampleFilterParam() {
  if( !isBlankParam(params.samples) ) return params.samples.toString().trim()
  if( !isBlankParam(params.sample) ) return params.sample.toString().trim()
  def embedded = sampleSetEmbeddedSamples()
  if( !isBlankParam(embedded) ) return embedded
  def key = sampleSetKey()
  if( key == 'lymphoma' ) return builtinLymphomaSamples()
  return ''
}

workflow {
  main:
  def cnaInput = !isBlankParam(params.input) ? params.input : (!isBlankParam(params.cna_input) ? params.cna_input : params.cna_events)

  if( isBlankParam(cnaInput) ) {
    error """
    Missing required CNA input. Use --input, --cna_input, or --cna_events.

    The input can be:
      1) a cohort-level cna_events.tsv file,
      2) a single-sample tumor folder containing cna_events.tsv,
      3) a cohort folder containing one or many cna_events.tsv files.

    Example:
      nextflow run main.nf -profile conda \
        --input /media/server/STORAGE/LPWGS_2025/CNA_analyses/illumina \
        --outdir /media/server/STORAGE/LPWGS_2025/CNA_analyses/illumina/cna_classifier_nf_results \
        --gistic_refgene auto
    """
  }

  def cna_events_ch = channel.fromPath(cnaInput, checkIfExists: true, type: 'any')

  def cna_notation_ch
  if( !isBlankParam(params.cna_notation) ) {
    cna_notation_ch = channel.fromPath(params.cna_notation, checkIfExists: true, type: 'any')
  } else {
    cna_notation_ch = channel.fromPath("${projectDir}/assets/empty_notation.tsv", checkIfExists: true, type: 'file')
  }

  def pathology_ch
  if( !isBlankParam(params.pathology) ) {
    pathology_ch = channel.fromPath(params.pathology, checkIfExists: true, type: 'file')
  } else {
    pathology_ch = channel.fromPath("${projectDir}/assets/empty_pathology.tsv", checkIfExists: true, type: 'file')
  }

  def sample_set_key = sampleSetKey()
  def default_region_catalog_file = (sample_set_key == 'lymphoma') ? "${projectDir}/assets/lymphoma_cna_regions.tsv" : "${projectDir}/assets/pancancer_cna_regions.tsv"
  def region_catalog_file = isBlankParam(params.region_catalog) ? default_region_catalog_file : params.region_catalog
  def region_catalog_ch = channel.fromPath(region_catalog_file, checkIfExists: true)
  def region_catalog_knowledge_ch = channel.fromPath(region_catalog_file, checkIfExists: true)
  def chrom_sizes_file = isBlankParam(params.chrom_sizes) ? "${projectDir}/assets/hg38_chrom_sizes.tsv" : params.chrom_sizes
  def chrom_sizes_ch = channel.fromPath(chrom_sizes_file, checkIfExists: true)

  PREPARE_CNA(cna_events_ch, cna_notation_ch, region_catalog_ch, chrom_sizes_ch)

  RUN_GISTIC(
    PREPARE_CNA.out.gistic_full_seg,
    PREPARE_CNA.out.gistic_event_seg,
    PREPARE_CNA.out.gistic_markers,
    PREPARE_CNA.out.prepare_metrics
  )

  PARSE_GISTIC(
    RUN_GISTIC.out.gistic2_out,
    RUN_GISTIC.out.gistic_status,
    RUN_GISTIC.out.gistic_command
  )

  CLASSIFY_CNA(
    PREPARE_CNA.out.clean_events,
    PREPARE_CNA.out.sample_summary,
    PREPARE_CNA.out.event_matrix,
    PREPARE_CNA.out.weighted_event_matrix,
    PREPARE_CNA.out.driver_matrix,
    PREPARE_CNA.out.recurrent_events,
    PREPARE_CNA.out.driver_hits,
    PARSE_GISTIC.out.gistic_matrix,
    PARSE_GISTIC.out.gistic_long,
    PARSE_GISTIC.out.gistic_summary
  )

  KNOWLEDGE_ENRICHMENT(
    CLASSIFY_CNA.out.classification,
    PREPARE_CNA.out.clean_events,
    PREPARE_CNA.out.driver_hits,
    region_catalog_knowledge_ch
  )

  PATHOLOGY_CONCORDANCE(
    pathology_ch,
    CLASSIFY_CNA.out.classification,
    PREPARE_CNA.out.clean_events,
    PREPARE_CNA.out.driver_hits,
    KNOWLEDGE_ENRICHMENT.out.sample_knowledge_summary
  )

  PLOT_REPORT(
    PREPARE_CNA.out.clean_events,
    PREPARE_CNA.out.sample_summary,
    PREPARE_CNA.out.event_matrix,
    PREPARE_CNA.out.driver_matrix,
    PREPARE_CNA.out.recurrent_events,
    PREPARE_CNA.out.driver_hits,
    PREPARE_CNA.out.gistic_full_seg,
    PREPARE_CNA.out.gistic_markers,
    RUN_GISTIC.out.gistic_status,
    RUN_GISTIC.out.gistic_command,
    PARSE_GISTIC.out.gistic_matrix,
    PARSE_GISTIC.out.gistic_long,
    PARSE_GISTIC.out.gistic_summary,
    CLASSIFY_CNA.out.classification,
    CLASSIFY_CNA.out.unsupervised_clusters,
    CLASSIFY_CNA.out.heatmap_matrix,
    CLASSIFY_CNA.out.pca_coordinates,
    PATHOLOGY_CONCORDANCE.out.pathology_concordance,
    PATHOLOGY_CONCORDANCE.out.pathology_records,
    PATHOLOGY_CONCORDANCE.out.pathology_status,
    PATHOLOGY_CONCORDANCE.out.pathology_model_trials
  )

  if( asBool(params.run_pdf_reports) ) {
    PDF_KNOWLEDGE_REPORTS(
      PREPARE_CNA.out.clean_events,
      PREPARE_CNA.out.sample_summary,
      PREPARE_CNA.out.driver_hits,
      PREPARE_CNA.out.driver_matrix,
      PARSE_GISTIC.out.gistic_matrix,
      PARSE_GISTIC.out.gistic_long,
      PARSE_GISTIC.out.gistic_summary,
      CLASSIFY_CNA.out.classification,
      KNOWLEDGE_ENRICHMENT.out.sample_knowledge,
      KNOWLEDGE_ENRICHMENT.out.sample_knowledge_summary,
      KNOWLEDGE_ENRICHMENT.out.knowledge_references,
      KNOWLEDGE_ENRICHMENT.out.sample_literature,
      KNOWLEDGE_ENRICHMENT.out.sample_literature_summary,
      PLOT_REPORT.out.figures,
      PATHOLOGY_CONCORDANCE.out.pathology_concordance,
      PATHOLOGY_CONCORDANCE.out.pathology_records
    )
  }

  if( asBool(params.run_clinician_reports) ) {
    CLINICIAN_DRIVER_REPORTS(
      CLASSIFY_CNA.out.classification,
      PREPARE_CNA.out.sample_summary,
      PREPARE_CNA.out.driver_hits,
      KNOWLEDGE_ENRICHMENT.out.sample_knowledge,
      KNOWLEDGE_ENRICHMENT.out.sample_knowledge_summary,
      KNOWLEDGE_ENRICHMENT.out.sample_literature,
      PATHOLOGY_CONCORDANCE.out.pathology_concordance,
      PATHOLOGY_CONCORDANCE.out.pathology_records
    )
  }
}

process PREPARE_CNA {
  tag "prepare_cna"
  publishDir "${params.outdir}/01_prepared", mode: 'copy'
  conda "${projectDir}/envs/cna_classifier.yml"

  input:
    path cna_events
    path cna_notation
    path region_catalog
    path chrom_sizes

  output:
    path "clean_events.tsv", emit: clean_events
    path "samples.tsv", emit: samples
    path "sample_cna_summary.tsv", emit: sample_summary
    path "samurai_events.seg", emit: samurai_seg
    path "gistic_events.seg", emit: gistic_event_seg
    path "gistic_full.seg", emit: gistic_full_seg
    path "gistic_markers.tsv", emit: gistic_markers
    path "event_matrix_binary.tsv", emit: event_matrix
    path "event_matrix_weighted.tsv", emit: weighted_event_matrix
    path "driver_region_matrix.tsv", emit: driver_matrix
    path "driver_region_hits.tsv", emit: driver_hits
    path "recurrent_events.tsv", emit: recurrent_events
    path "dlbclass_cna_gsm_like.tsv", emit: dlbclass_cna_gsm_like
    path "prepare_metrics.json", emit: prepare_metrics

  script:
    """
    01_prepare_cna_inputs.py \
      --cna-events '${cna_events}' \
      --cna-notation '${cna_notation}' \
      --region-catalog '${region_catalog}' \
      --chrom-sizes '${chrom_sizes}' \
      --gistic-window-bp ${params.gistic_window_bp} \
      --min-bins ${params.min_bins} \
      --min-size-mb ${params.min_size_mb} \
      --min-abs-log2 ${params.min_abs_log2} \
      --focal-mb ${params.focal_mb} \
      --broad-mb ${params.broad_mb} \
      ${isBlankParam(sampleFilterParam()) ? '' : "--samples '${sampleFilterParam()}'"} \
      ${asBool(params.include_sex) ? '--include-sex' : ''}
    """
}

process RUN_GISTIC {
  tag "gistic2_included"
  publishDir "${params.outdir}/04_gistic2", mode: 'copy'
  cpus params.gistic_cpus
  memory params.gistic_memory
  time params.gistic_time
  conda "${projectDir}/envs/gistic2.yml"

  input:
    path gistic_full_seg
    path gistic_event_seg
    path gistic_markers
    path prepare_metrics

  output:
    path "gistic2_out", emit: gistic2_out
    path "gistic2_status.tsv", emit: gistic_status
    path "gistic2_command.txt", emit: gistic_command
    path "gistic2_input_files", emit: gistic_input_files
    path "gistic2_refgene", emit: gistic_refgene_dir
    path "gistic2_versions.txt", emit: gistic_versions

  script:
    def broadFlag = asBool(params.gistic_broad) ? 1 : 0
    def runFlag = asBool(params.run_gistic) ? 'true' : 'false'
    def requiredFlag = asBool(params.gistic_required) ? 'true' : 'false'
    def markerFlag = asBool(params.gistic_use_markers) ? 'true' : 'false'
    def autoDownloadRefgene = asBool(params.gistic_auto_download_refgene) ? 'true' : 'false'
    def refgene = params.gistic_refgene ?: 'auto'
    def exe = params.gistic_exe ?: 'auto'
    def cnvFile = params.gistic_cnv_file ?: ''
    def segToUse = params.gistic_seg_type.toString() == 'events' ? gistic_event_seg : gistic_full_seg
    """
    set -euo pipefail
    mkdir -p gistic2_out gistic2_input_files gistic2_refgene
    cp '${gistic_full_seg}' gistic2_input_files/gistic_full.seg
    cp '${gistic_event_seg}' gistic2_input_files/gistic_events.seg
    cp '${gistic_markers}' gistic2_input_files/gistic_markers.tsv
    cp '${prepare_metrics}' gistic2_input_files/prepare_metrics.json

    printf 'status\treason\tsegmentation\tcommand\texecutable\trefgene\n' > gistic2_status.tsv
    : > gistic2_command.txt
    : > gistic2_versions.txt

    if [ '${runFlag}' != 'true' ]; then
      printf 'skipped	--run_gistic false	${params.gistic_seg_type}	NA	NA	NA
' >> gistic2_status.tsv
      echo 'GISTIC2 was skipped because --run_gistic false was supplied.' > gistic2_out/GISTIC_NOT_RUN.txt
      exit 0
    fi

    # GISTIC2 is a cohort recurrence method. In single-sample mode, keep the
    # CNA classification/report but skip GISTIC unless the user deliberately lowers
    # --gistic_min_samples.
    N_SAMPLES="\$(python3 - <<'PY_GISTIC_N'
import json
from pathlib import Path
try:
    print(int(json.loads(Path('${prepare_metrics}').read_text()).get('samples_total', 0)))
except Exception:
    print(0)
PY_GISTIC_N
)"
    if [ "\${N_SAMPLES}" -lt ${params.gistic_min_samples} ]; then
      printf 'skipped	not_enough_samples_for_gistic_n=%s_min=%s	${params.gistic_seg_type}	NA	NA	NA
' "\${N_SAMPLES}" '${params.gistic_min_samples}' >> gistic2_status.tsv
      echo "GISTIC2 was skipped because only \${N_SAMPLES} sample(s) were available; default --gistic_min_samples is ${params.gistic_min_samples}. CNA-only classification still ran." > gistic2_out/GISTIC_NOT_RUN.txt
      if [ '${requiredFlag}' = 'true' ]; then
        echo "ERROR: --gistic_required true but only \${N_SAMPLES} sample(s) were available for GISTIC2." >&2
        exit 1
      fi
      exit 0
    fi

    # Resolve the GISTIC executable. With -profile conda, envs/gistic2.yml should provide gistic2.
    GISTIC_EXE_INPUT='${exe}'
    if [ -z "\$GISTIC_EXE_INPUT" ] || [ "\$GISTIC_EXE_INPUT" = 'auto' ]; then
      GISTIC_EXE_RESOLVED="\$(command -v gistic2 || true)"
    else
      GISTIC_EXE_RESOLVED="\$GISTIC_EXE_INPUT"
    fi

    if [ -z "\$GISTIC_EXE_RESOLVED" ]; then
      printf 'skipped\tgistic2 executable not found; use -profile conda or --gistic_exe /path/to/gistic2\t${params.gistic_seg_type}\tNA\tNA\tNA\n' >> gistic2_status.tsv
      echo 'GISTIC2 was not run because no executable was found.' > gistic2_out/GISTIC_NOT_RUN.txt
      if [ '${requiredFlag}' = 'true' ]; then
        echo 'ERROR: --gistic_required true but gistic2 was not found. Try -profile conda or set --gistic_exe.' >&2
        exit 1
      fi
      exit 0
    fi

    if [ ! -x "\$GISTIC_EXE_RESOLVED" ] && ! command -v "\$GISTIC_EXE_RESOLVED" >/dev/null 2>&1; then
      printf 'skipped\tgistic executable is not runnable\t${params.gistic_seg_type}\t%s\t%s\tNA\n' "\$GISTIC_EXE_RESOLVED" "\$GISTIC_EXE_RESOLVED" >> gistic2_status.tsv
      echo "GISTIC2 was not run because executable is not runnable: \$GISTIC_EXE_RESOLVED" > gistic2_out/GISTIC_NOT_RUN.txt
      if [ '${requiredFlag}' = 'true' ]; then
        echo "ERROR: --gistic_required true but executable is not runnable: \$GISTIC_EXE_RESOLVED" >&2
        exit 1
      fi
      exit 0
    fi

    {
      echo 'Resolved GISTIC executable:'
      echo "\$GISTIC_EXE_RESOLVED"
      echo
      echo 'gistic2 help/version probe:'
      "\$GISTIC_EXE_RESOLVED" -h 2>&1 | head -80 || true
    } > gistic2_versions.txt

    # Resolve the hg38 refgene. Default is auto-download into the process work directory.
    REFGENE_INPUT='${refgene}'
    REFGENE_RESOLVED="\$REFGENE_INPUT"
    if [ -z "\$REFGENE_INPUT" ] || [ "\$REFGENE_INPUT" = 'auto' ]; then
      REFGENE_RESOLVED="gistic2_refgene/hg38.UCSC.add_miR.160920.refgene.mat"
      if [ ! -s "\$REFGENE_RESOLVED" ] && [ '${autoDownloadRefgene}' = 'true' ]; then
        echo 'Auto-downloading hg38 GISTIC2 refgene into gistic2_refgene/ ...'
        URLS=(
          'https://gdac.broadinstitute.org/runs/CPTAC3_LSCC_DWG/CPTAC3-LSCC-v1/GISTIC2/gistic2.refgene.hg38.UCSC.add_miR.160920.mat'
          'https://gdac.broadinstitute.org/runs/awg_cptac-luad-v3.0/G5/GISTIC2/gistic2.refgene.hg38.UCSC.add_miR.160920.mat'
        )
        downloaded='false'
        for url in "\${URLS[@]}"; do
          echo "Trying: \$url"
          if command -v curl >/dev/null 2>&1; then
            if curl -L --fail --retry 3 --connect-timeout 30 -o "\$REFGENE_RESOLVED.tmp" "\$url"; then
              mv "\$REFGENE_RESOLVED.tmp" "\$REFGENE_RESOLVED"
              downloaded='true'
              break
            fi
          elif command -v wget >/dev/null 2>&1; then
            if wget -O "\$REFGENE_RESOLVED.tmp" "\$url"; then
              mv "\$REFGENE_RESOLVED.tmp" "\$REFGENE_RESOLVED"
              downloaded='true'
              break
            fi
          fi
        done
        rm -f "\$REFGENE_RESOLVED.tmp" || true
        if [ "\$downloaded" != 'true' ]; then
          rm -f "\$REFGENE_RESOLVED" || true
        fi
      fi
    fi

    if [ ! -s "\$REFGENE_RESOLVED" ]; then
      printf 'skipped\tmissing hg38 refgene; set --gistic_refgene /path/to/*.mat or allow auto download\t${params.gistic_seg_type}\tNA\t%s\t%s\n' "\$GISTIC_EXE_RESOLVED" "\$REFGENE_RESOLVED" >> gistic2_status.tsv
      echo "GISTIC2 was not run because the refgene file is missing or empty: \$REFGENE_RESOLVED" > gistic2_out/GISTIC_NOT_RUN.txt
      if [ '${requiredFlag}' = 'true' ]; then
        echo "ERROR: --gistic_required true but refgene file is missing/empty: \$REFGENE_RESOLVED" >&2
        exit 1
      fi
      exit 0
    fi

    # Build optional argument arrays in the process shell.
    MK_ARGS=()
    if [ '${markerFlag}' = 'true' ]; then
      MK_ARGS=(-mk '${gistic_markers}')
    fi

    CNV_ARGS=()
    CNV_FILE='${cnvFile}'
    if [ -n "\$CNV_FILE" ]; then
      if [ -s "\$CNV_FILE" ]; then
        CNV_ARGS=(-cnv "\$CNV_FILE")
      else
        echo "WARNING: --gistic_cnv_file was supplied but is missing/empty: \$CNV_FILE" >&2
      fi
    fi

    CMD=(
      "\$GISTIC_EXE_RESOLVED"
      -b gistic2_out
      -seg '${segToUse}'
      "\${MK_ARGS[@]}"
      "\${CNV_ARGS[@]}"
      -refgene "\$REFGENE_RESOLVED"
      -genegistic 1
      -broad ${broadFlag}
      -brlen ${params.gistic_brlen}
      -conf ${params.gistic_conf}
      -qvt ${params.gistic_qvt}
      -ta ${params.gistic_ta}
      -td ${params.gistic_td}
      -cap ${params.gistic_cap}
      -rx ${params.gistic_rx}
      -js ${params.gistic_join_segment_size}
      -maxseg ${params.gistic_maxseg}
      -scent ${params.gistic_scent}
      -smallmem ${params.gistic_smallmem}
      -savegene ${params.gistic_savegene}
      -armpeel ${params.gistic_armpeel}
      -smalldisk ${params.gistic_smalldisk}
      -v ${params.gistic_verbose}
    )

    {
      echo '#!/usr/bin/env bash'
      echo 'set -euo pipefail'
      echo 'unset DISPLAY || true'
      printf '%q ' "\${CMD[@]}"
      echo
    } > gistic2_command.txt
    chmod +x gistic2_command.txt

    set +e
    unset DISPLAY || true
    "\${CMD[@]}" > gistic2_out/gistic2.stdout.log 2> gistic2_out/gistic2.stderr.log
    rc=\$?
    set -e

    if [ \$rc -eq 0 ]; then
      printf 'completed\tNA\t${params.gistic_seg_type}\tgistic2_command.txt\t%s\t%s\n' "\$GISTIC_EXE_RESOLVED" "\$REFGENE_RESOLVED" >> gistic2_status.tsv
    else
      printf 'failed\texit_code_%s\t${params.gistic_seg_type}\tgistic2_command.txt\t%s\t%s\n' "\${rc}" "\$GISTIC_EXE_RESOLVED" "\$REFGENE_RESOLVED" >> gistic2_status.tsv
      if [ '${requiredFlag}' = 'true' ]; then
        echo "ERROR: GISTIC2 failed with exit code \${rc}. See gistic2_out/gistic2.stderr.log" >&2
        exit \$rc
      fi
    fi
    """
}

process PARSE_GISTIC {
  tag "parse_gistic2"
  publishDir "${params.outdir}/05_gistic2_parsed", mode: 'copy'
  conda "${projectDir}/envs/cna_classifier.yml"

  input:
    path gistic2_out
    path gistic_status
    path gistic_command

  output:
    path "gistic_lesions_matrix.tsv", emit: gistic_matrix
    path "gistic_lesions_long.tsv", emit: gistic_long
    path "gistic_lesions_summary.tsv", emit: gistic_summary
    path "gistic_parse_metrics.json", emit: gistic_parse_metrics

  script:
    """
    04_parse_gistic_results.py \
      --gistic-dir '${gistic2_out}' \
      --gistic-status '${gistic_status}' \
      --gistic-command '${gistic_command}'
    """
}

process CLASSIFY_CNA {
  tag "classify_cna"
  publishDir "${params.outdir}/02_classification", mode: 'copy'
  conda "${projectDir}/envs/cna_classifier.yml"

  input:
    path clean_events
    path sample_summary
    path event_matrix
    path weighted_event_matrix
    path driver_matrix
    path recurrent_events
    path driver_hits
    path gistic_matrix
    path gistic_long
    path gistic_summary

  output:
    path "cna_patient_classification.tsv", emit: classification
    path "unsupervised_clusters.tsv", emit: unsupervised_clusters
    path "heatmap_matrix.tsv", emit: heatmap_matrix
    path "pca_coordinates.tsv", emit: pca_coordinates
    path "classification_metrics.json", emit: classification_metrics

  script:
    """
    02_classify_cna.py \
      --clean-events '${clean_events}' \
      --sample-summary '${sample_summary}' \
      --event-matrix '${event_matrix}' \
      --weighted-event-matrix '${weighted_event_matrix}' \
      --driver-matrix '${driver_matrix}' \
      --recurrent-events '${recurrent_events}' \
      --driver-hits '${driver_hits}' \
      --gistic-matrix '${gistic_matrix}' \
      --gistic-long '${gistic_long}' \
      --gistic-summary '${gistic_summary}' \
      --low-events ${params.low_events} \
      --high-events ${params.high_events} \
      --ultra-events ${params.ultra_events} \
      --high-chromosomes ${params.high_chromosomes} \
      --high-altered-mb ${params.high_altered_mb} \
      --ultra-altered-mb ${params.ultra_altered_mb} \
      --nmf-clusters ${params.nmf_clusters} \
      --top-regions ${params.top_regions}
    """
}


process KNOWLEDGE_ENRICHMENT {
  tag "knowledge_enrichment"
  publishDir "${params.outdir}/06_knowledge", mode: 'copy'
  conda "${projectDir}/envs/cna_classifier.yml"

  input:
    path classification
    path clean_events
    path driver_hits
    path region_catalog

  output:
    path "knowledge_base.tsv", emit: knowledge_base
    path "sample_knowledge.tsv", emit: sample_knowledge
    path "sample_knowledge_summary.tsv", emit: sample_knowledge_summary
    path "knowledge_references.tsv", emit: knowledge_references
    path "sample_literature.tsv", emit: sample_literature
    path "sample_literature_summary.tsv", emit: sample_literature_summary
    path "sample_literature_references.tsv", optional: true, emit: sample_literature_references
    path "knowledge_llm_trials.tsv", emit: knowledge_llm_trials
    path "knowledge_literature_ranker_trials.tsv", emit: knowledge_literature_ranker_trials
    path "knowledge_metrics.json", emit: knowledge_metrics
    path "knowledge_cache.json", emit: knowledge_cache
    path "knowledge_http_cache", optional: true, emit: knowledge_http_cache

  script:
    def webFlag = asBool(params.knowledge_web) ? 'true' : 'false'
    def allowFailFlag = asBool(params.knowledge_allow_fail) ? 'true' : 'false'
    def hfFlag = asBool(params.knowledge_hf_ner) ? 'true' : 'false'
    """
    05_scrape_cna_knowledge.py \
      --classification '${classification}' \
      --clean-events '${clean_events}' \
      --driver-hits '${driver_hits}' \
      --region-catalog '${region_catalog}' \
      --enable-web '${webFlag}' \
      --allow-fail '${allowFailFlag}' \
      --cache-dir knowledge_http_cache \
      --max-papers ${params.knowledge_max_papers} \
      --timeout ${params.knowledge_timeout} \
      --sleep ${params.knowledge_sleep} \
      --lymphoma-terms '${params.knowledge_lymphoma_terms}' \
      --cancer-terms '${params.knowledge_cancer_terms}' \
      --cancer-type '${sampleSetKey()}' \
      --user-agent '${params.knowledge_user_agent}' \
      --enable-hf-ner '${hfFlag}' \
      --hf-model '${params.knowledge_hf_model}' \
      --enable-literature-llm '${asBool(params.knowledge_literature_llm) ? 'true' : 'false'}' \
      --literature-llm-models '${params.knowledge_literature_llm_models}' \
      --literature-llm-local-files-only '${asBool(params.knowledge_literature_llm_local_files_only) ? 'true' : 'false'}' \
      --literature-llm-max-features ${params.knowledge_literature_llm_max_features} \
      --literature-llm-max-input-chars ${params.knowledge_literature_llm_max_input_chars} \
      --literature-llm-max-new-tokens ${params.knowledge_literature_llm_max_new_tokens} \
      --deep-literature '${asBool(params.knowledge_deep_literature) ? 'true' : 'false'}' \
      --deep-max-papers-per-feature ${params.knowledge_deep_max_papers_per_feature} \
      --deep-top-papers-per-sample ${params.knowledge_deep_top_papers_per_sample} \
      --deep-enable-llm-ranker '${asBool(params.knowledge_deep_enable_llm_ranker) ? 'true' : 'false'}' \
      --deep-llm-ranker-models '${params.knowledge_deep_llm_ranker_models}' \
      --deep-llm-ranker-local-files-only '${asBool(params.knowledge_deep_llm_ranker_local_files_only) ? 'true' : 'false'}' \
      --deep-llm-ranker-max-candidates-per-sample ${params.knowledge_deep_llm_ranker_max_candidates_per_sample} \
      --literature-reference-llm-selection '${asBool(params.knowledge_literature_reference_llm_selection) ? 'true' : 'false'}' \
      --literature-top-references ${params.knowledge_literature_top_references}
    """
}

process PATHOLOGY_CONCORDANCE {
  tag "pathology_concordance"
  publishDir "${params.outdir}/07_pathology", mode: 'copy'
  conda "${projectDir}/envs/cna_classifier.yml"

  input:
    path pathology
    path classification
    path clean_events
    path driver_hits
    path sample_knowledge_summary

  output:
    path "pathology_concordance.tsv", emit: pathology_concordance
    path "pathology_records_matched.tsv", emit: pathology_records
    path "pathology_concordance_metrics.json", emit: pathology_metrics
    path "pathology_status.txt", emit: pathology_status
    path "pathology_model_trials.tsv", emit: pathology_model_trials

  script:
    """
    set -euo pipefail

    EXTRA_ARGS=()
    PATHOLOGY_SAMPLE_COL='${params.pathology_sample_col ?: ''}'
    PATHOLOGY_CASE_COL='${params.pathology_case_col ?: ''}'
    PATHOLOGY_DIAGNOSIS_COL='${params.pathology_diagnosis_col ?: ''}'
    SCORE_CALIBRATION_TABLE='${params.score_calibration_table ?: ''}'

    if [ -n "\$PATHOLOGY_SAMPLE_COL" ]; then
      EXTRA_ARGS+=(--pathology-sample-col "\$PATHOLOGY_SAMPLE_COL")
    fi
    if [ -n "\$PATHOLOGY_CASE_COL" ]; then
      EXTRA_ARGS+=(--pathology-case-col "\$PATHOLOGY_CASE_COL")
    fi
    if [ -n "\$PATHOLOGY_DIAGNOSIS_COL" ]; then
      EXTRA_ARGS+=(--pathology-diagnosis-col "\$PATHOLOGY_DIAGNOSIS_COL")
    fi
    if [ -n "\$SCORE_CALIBRATION_TABLE" ]; then
      EXTRA_ARGS+=(--score-calibration-table "\$SCORE_CALIBRATION_TABLE")
    fi

    07_pathology_concordance.py \
      --pathology '${pathology}' \
      --classification '${classification}' \
      --clean-events '${clean_events}' \
      --driver-hits '${driver_hits}' \
      --sample-knowledge-summary '${sample_knowledge_summary}' \
      --sample-set '${sampleSetKey()}' \
      --enable-biomed-models '${asBool(params.pathology_use_biomed_models) ? 'true' : 'false'}' \
      --biomed-models '${params.pathology_biomed_models}' \
      --biomed-local-files-only '${asBool(params.pathology_biomed_local_files_only) ? 'true' : 'false'}' \
      --biomed-max-tokens ${params.pathology_biomed_max_tokens} \
      --score-calibration-score-col '${params.score_calibration_score_col ?: ''}' \
      --score-calibration-label-col '${params.score_calibration_label_col ?: ''}' \
      "\${EXTRA_ARGS[@]}"
    """
}

process PDF_KNOWLEDGE_REPORTS {
  tag "pdf_knowledge_reports"
  publishDir "${params.outdir}/03_report", mode: 'copy'
  conda "${projectDir}/envs/cna_classifier.yml"

  input:
    path clean_events
    path sample_summary
    path driver_hits
    path driver_matrix
    path gistic_matrix
    path gistic_long
    path gistic_summary
    path classification
    path sample_knowledge
    path sample_knowledge_summary
    path knowledge_references
    path sample_literature
    path sample_literature_summary
    path figures
    path pathology_concordance
    path pathology_records

  output:
    path "pdf_reports", emit: pdf_reports

  script:
    def includeFullEventsFlag = asBool(params.pdf_include_full_events) ? 'true' : 'false'
    """
    06_pdf_knowledge_reports.py \
      --classification '${classification}' \
      --sample-summary '${sample_summary}' \
      --clean-events '${clean_events}' \
      --driver-hits '${driver_hits}' \
      --driver-matrix '${driver_matrix}' \
      --gistic-matrix '${gistic_matrix}' \
      --gistic-long '${gistic_long}' \
      --gistic-summary '${gistic_summary}' \
      --sample-knowledge '${sample_knowledge}' \
      --sample-knowledge-summary '${sample_knowledge_summary}' \
      --knowledge-references '${knowledge_references}' \
      --sample-literature '${sample_literature}' \
      --sample-literature-summary '${sample_literature_summary}' \
      --figures '${figures}' \
      --pathology-concordance '${pathology_concordance}' \
      --pathology-records '${pathology_records}' \
      --outdir pdf_reports \
      --max-events ${params.pdf_max_events} \
      --include-full-events '${includeFullEventsFlag}'
    """
}


process CLINICIAN_DRIVER_REPORTS {
  tag "clinician_driver_reports"
  publishDir "${params.outdir}/03_report", mode: 'copy'
  conda "${projectDir}/envs/cna_classifier.yml"

  input:
    path classification
    path sample_summary
    path driver_hits
    path sample_knowledge
    path sample_knowledge_summary
    path sample_literature
    path pathology_concordance
    path pathology_records

  output:
    path "clinician_reports", emit: clinician_reports

  script:
    """
    08_clinician_driver_reports.py \
      --classification '${classification}' \
      --sample-summary '${sample_summary}' \
      --driver-hits '${driver_hits}' \
      --sample-knowledge '${sample_knowledge}' \
      --sample-knowledge-summary '${sample_knowledge_summary}' \
      --sample-literature '${sample_literature}' \
      --pathology-concordance '${pathology_concordance}' \
      --pathology-records '${pathology_records}' \
      --outdir clinician_reports \
      --max-drivers ${params.clinician_max_drivers}
    """
}

process PLOT_REPORT {
  tag "plot_report"
  publishDir "${params.outdir}/03_report", mode: 'copy'
  conda "${projectDir}/envs/cna_classifier.yml"

  input:
    path clean_events
    path sample_summary
    path event_matrix
    path driver_matrix
    path recurrent_events
    path driver_hits
    path gistic_full_seg
    path gistic_markers
    path gistic_status
    path gistic_command
    path gistic_matrix
    path gistic_long
    path gistic_summary
    path classification
    path unsupervised_clusters
    path heatmap_matrix
    path pca_coordinates
    path pathology_concordance
    path pathology_records
    path pathology_status
    path pathology_model_trials

  output:
    path "figures", emit: figures
    path "cna_classifier_report.html", emit: html_report
    path "report_tables", emit: report_tables
    path "sample_reports", emit: sample_reports

  script:
    """
    03_plot_report.py \
      --clean-events '${clean_events}' \
      --sample-summary '${sample_summary}' \
      --event-matrix '${event_matrix}' \
      --driver-matrix '${driver_matrix}' \
      --recurrent-events '${recurrent_events}' \
      --driver-hits '${driver_hits}' \
      --gistic-full-seg '${gistic_full_seg}' \
      --gistic-markers '${gistic_markers}' \
      --gistic-status '${gistic_status}' \
      --gistic-command '${gistic_command}' \
      --gistic-matrix '${gistic_matrix}' \
      --gistic-long '${gistic_long}' \
      --gistic-summary '${gistic_summary}' \
      --classification '${classification}' \
      --unsupervised-clusters '${unsupervised_clusters}' \
      --heatmap-matrix '${heatmap_matrix}' \
      --pca-coordinates '${pca_coordinates}' \
      --plot-top-features ${params.plot_top_features} \
      --pathology-concordance '${pathology_concordance}' \
      --pathology-records '${pathology_records}'

    # Keep pathology outputs visible in 03_report/report_tables as well as 07_pathology.
    mkdir -p report_tables
    cp '${pathology_concordance}' report_tables/pathology_concordance.tsv || true
    cp '${pathology_records}' report_tables/pathology_records_matched.tsv || true
    cp '${pathology_status}' report_tables/pathology_status.txt || true
    cp '${pathology_model_trials}' report_tables/pathology_model_trials.tsv || true
    """
}
