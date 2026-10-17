nextflow.enable.dsl=2


// Run local preflight directly from Nextflow. Argument arrays and JSON avoid shell
// interpolation, and the shared helper never starts a second Nextflow instance.
def prepareWorkflow(parameters, extraArgs, project, launch, commandLine) {
    def python = parameters.python == null ? 'python3' : parameters.python
    if (!(python instanceof CharSequence) || !python.toString().trim() || python.toString().find(/[\r\n\t']/)) {
        error '--python must be an executable name or path without tabs, newlines, or single quotes.'
    }
    def command = [python.toString(), '-B', "${project}/bin/nextflow_entry.py".toString()]
    def proc = new ProcessBuilder(command)
        .directory(new File(launch.toString()))
        .redirectError(ProcessBuilder.Redirect.INHERIT)
        .start()
    proc.outputStream.withWriter('UTF-8') { writer ->
        writer.write(groovy.json.JsonOutput.toJson([parameters: parameters, extra_args: extraArgs, command_line: commandLine]))
    }
    def response = proc.inputStream.getText('UTF-8')
    if (proc.waitFor() != 0) error 'Input/dependency validation failed; see the message above.'
    return new groovy.json.JsonSlurper().parseText(response)
}

process IMPORT_BAM {
    tag sample
    label 'heavy'
    publishDir { "${output_dir}/alignment" }, mode: 'symlink'
    input:
    tuple val(sample), path(source_bam, stageAs: 'input/source.bam')
    path runtime
    path code
    val output_dir
    output:
    tuple val('patient'), val(sample), path("${sample}.bam"), path("${sample}.bam.bai"), emit: bam
    tuple val('patient'), val(sample), path("${sample}.qc.json"), emit: qc
    tuple val('patient'), val(sample), path("${sample}.coverage.txt"), emit: coverage
    script:
    """
    '${params.python}' ${code}/import_alignment.py bam --input '${source_bam}' --sample '${sample}' --config '${runtime}' --threads ${task.cpus}
    """
}
process IMPORT_CALLS {
    tag "${sample}:${kind}"
    publishDir { "${output_dir}/variants/imported" }, mode: 'copy'
    input:
    tuple val(sample), val(kind), path(source_vcf, stageAs: 'input/source.vcf.gz')
    path runtime
    path code
    val output_dir
    output:
    tuple val(sample), val(kind), path("${sample}.${kind}.vcf.gz"), path("${sample}.${kind}.vcf.gz.tbi"), emit: vcf
    path '*.json'
    script:
    """
    '${params.python}' ${code}/import_alignment.py calls --input '${source_vcf}' --sample '${sample}' --kind '${kind}' --config '${runtime}'
    """
}
process ALIGN {
    tag "${sample}:${lane}"
    label 'heavy'
    publishDir { role == 'normal' ? "${output_dir}/panel_of_normals/qc/${sample}" : "${output_dir}/qc/lanes" }, mode: 'copy', pattern: '*.json'
    publishDir { role == 'normal' ? "${output_dir}/panel_of_normals/qc/${sample}" : "${output_dir}/qc/lanes" }, mode: 'copy', pattern: '*.html'
    input:
    tuple val(role), val(sample), val(lane), val(library), path(reads)
    path runtime
    path code
    val output_dir
    output:
    tuple val(role), val(sample), path("${lane}.bam"), emit: bam
    path '*.json', emit: qc
    script:
    """
    '${params.python}' ${code}/pipeline.py align --config ${runtime} --sample '${sample}' --lane '${lane}' --library '${library}' --reads ${reads.collect { "'${it}'" }.join(' ')} --threads ${task.cpus}
    """
}
process MERGE_MARKDUP {
    tag sample
    label 'heavy'
    publishDir { role == 'normal' ? "${output_dir}/panel_of_normals/alignment" : "${output_dir}/alignment" }, mode: 'copy'
    input:
    tuple val(role), val(sample), path(bams)
    path runtime
    path code
    val output_dir
    output:
    tuple val(role), val(sample), path("${sample}.bam"), path("${sample}.bam.bai"), emit: bam
    tuple val(role), val(sample), path("${sample}.qc.json"), emit: qc
    tuple val(role), val(sample), path("${sample}.coverage.txt"), emit: coverage
    path '*.txt'
    script:
    """
    '${params.python}' ${code}/pipeline.py merge --config ${runtime} --sample '${sample}' --bams ${bams.collect { "'${it}'" }.join(' ')} --threads ${task.cpus}
    """
}
process GERMLINE {
    tag sample
    label 'heavy'
    publishDir { "${output_dir}/variants/germline" }, mode: 'copy'
    input:
    tuple val(sample), path(bam), path(bai)
    path runtime
    path code
    val output_dir
    output:
    tuple val(sample), val('germline'), path("${sample}.germline.vcf.gz"), path("${sample}.germline.vcf.gz.tbi"), emit: vcf
    path '*.stats.txt'
    script:
    """
    '${params.python}' ${code}/pipeline.py germline --config ${runtime} --sample '${sample}' --bam ${bam} --threads ${task.cpus}
    """
}
process NORMAL_MUTECT {
    tag sample
    label 'heavy'
    publishDir { "${output_dir}/panel_of_normals/calls" }, mode: 'copy'
    input:
    tuple val(sample), path(bam), path(bai)
    path runtime
    path code
    val output_dir
    output:
    tuple val(sample), path("${sample}.pon.unfiltered.vcf.gz"), path("${sample}.pon.unfiltered.vcf.gz.tbi"), emit: vcf
    script:
    """
    '${params.python}' ${code}/pipeline.py normal_mutect --config ${runtime} --sample '${sample}' --bam ${bam} --threads ${task.cpus}
    """
}
process CREATE_PON {
    label 'heavy'
    publishDir { "${output_dir}/panel_of_normals" }, mode: 'copy'
    input:
    tuple path(vcfs), path(indexes)
    path runtime
    path code
    val output_dir
    output:
    tuple path('somatic_pon.hg38.vcf.gz'), path('somatic_pon.hg38.vcf.gz.tbi'), emit: pon
    path 'somatic_pon.provenance.json'
    script:
    """
    '${params.python}' ${code}/pipeline.py create_pon --config ${runtime} --normal-vcfs ${vcfs.collect { "'${it}'" }.join(' ')} --threads ${task.cpus}
    """
}
process SOMATIC {
    tag sample
    label 'heavy'
    publishDir { "${output_dir}/variants/somatic_candidates" }, mode: 'copy'
    input:
    tuple val(sample), path(bam), path(bai)
    tuple path(pon), path(pon_index)
    path runtime
    path code
    val output_dir
    output:
    tuple val(sample), val('somatic'), path("${sample}.somatic.vcf.gz"), path("${sample}.somatic.vcf.gz.tbi"), emit: vcf
    path '*.json'
    path '*.stats', optional: true
    path '*.tar.gz', optional: true
    script:
    """
    '${params.python}' ${code}/pipeline.py somatic --config ${runtime} --sample '${sample}' --bam ${bam} --threads ${task.cpus} ${pon ? "--pon '${pon}'" : ''}
    """
}
process PREPARE_CLINVAR {
    label 'heavy'
    publishDir { "${output_dir}/pipeline_info" }, mode: 'copy', pattern: '*.json'
    input:
    path runtime
    path code
    val output_dir
    output:
    tuple path('clinvar.norm.vcf.gz'), path('clinvar.norm.vcf.gz.tbi'), emit: database
    path 'clinvar.provenance.json'
    script:
    """
    '${params.python}' ${code}/pipeline.py prepare_clinvar --config ${runtime}
    """
}
process ANNOTATE {
    tag "${sample}:${kind}"
    label 'heavy'
    publishDir { "${output_dir}/annotation/${kind}" }, mode: 'copy'
    input:
    tuple val(sample), val(kind), path(vcf), path(tbi)
    tuple path(clinvar), path(clinvar_index)
    path runtime
    path code
    val output_dir
    output:
    tuple val(sample), val(kind), path("${sample}.${kind}.annotation.json"), path("${sample}.${kind}.pathogenic_candidates.tsv"), emit: summary
    tuple val(sample), val(kind), path("${sample}.${kind}.annotated.vcf.gz"), path("${sample}.${kind}.annotated.vcf.gz.tbi"), emit: annotated_vcf
    tuple val(sample), val(kind), path("${sample}.${kind}.all_variants.tsv"), emit: table
    path '*multianno*', optional: true
    script:
    """
    '${params.python}' ${code}/pipeline.py annotate --config ${runtime} --sample '${sample}' --kind '${kind}' --vcf ${vcf} --clinvar ${clinvar} --threads ${task.cpus}
    """
}
process TRAITS_GWAS {
    tag sample
    label 'heavy'
    publishDir { "${output_dir}/traits_gwas" }, mode: 'copy'
    input:
    tuple val(sample), path(bam), path(bai)
    path imputed
    path cached_genotypes, stageAs: 'cached_traits/*'
    path runtime
    path code
    val output_dir
    output:
    tuple val(sample), path("${sample}.traits_gwas.json"), emit: summary
    path '*.html'
    path '*.tsv', emit: tables
    path '*.vcf.gz*', optional: true
    script:
    """
    '${params.python}' ${code}/pipeline.py traits --config ${runtime} --sample '${sample}' --bam ${bam} --threads ${task.cpus} ${cached_genotypes ? '--cached-trait-data cached_traits' : ''} ${imputed ? "--imputed-vcfs " + imputed.findAll { it.toString().endsWith('.bcf') }.collect { "'${it}'" }.join(' ') : ''}
    """
}
process VARIANT_EVIDENCE {
    tag sample
    label 'imputation'
    publishDir { "${output_dir}/variant_evidence" }, mode: 'copy'
    input:
    tuple val(sample), path(germline_table), path(somatic_table)
    path imputed
    path runtime
    path code
    val output_dir
    output:
    path '*.variant_evidence.json', emit: summary
    path '*.variant_evidence.html'
    path '*.variant_review.xlsx'
    path "${sample}.*.tsv.gz", emit: tables
    script:
    """
    '${params.python}' ${code}/variant_evidence.py --config ${runtime} --sample '${sample}' --germline '${germline_table}' --somatic '${somatic_table}' ${imputed ? "--imputed " + imputed.findAll { it.toString().endsWith('.bcf') }.collect { "'${it}'" }.join(' ') : ''}
    """
}
process ANCESTRY {
    tag sample
    label 'heavy'
    publishDir { "${output_dir}/ancestry" }, mode: 'copy'
    input:
    tuple val(sample), path(bam), path(bai)
    path runtime
    path code
    val output_dir
    output:
    tuple val(sample), path("${sample}.ancestry.json"), emit: summary
    path '*.tsv', optional: true
    path '*.html', optional: true
    script:
    """
    '${params.python}' ${code}/pipeline.py ancestry --config ${runtime} --sample '${sample}' --bam ${bam} --threads ${task.cpus}
    """
}
process ANCESTRY_EXPLORER {
    tag sample
    label 'imputation'
    publishDir { "${output_dir}/ancestry_explorer" }, mode: 'copy'
    input:
    tuple val(sample), path(bam), path(bai)
    path imputed
    path references
    path qc
    path ancestry
    path runtime
    path code
    val output_dir
    output:
    path "${sample}.ancestry_explorer", emit: report
    script:
    """
    OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 '${params.python}' ${code}/ancestry_explorer.py --config ${runtime} --sample '${sample}' --bam '${bam}' --qc '${qc}' --ancestry '${ancestry}' --references ${references.findAll { it.toString().endsWith('.bcf') }.collect { "'${it}'" }.join(' ')} --imputed ${imputed.findAll { it.toString().endsWith('.bcf') }.collect { "'${it}'" }.join(' ')}
    """
}

process GLIMPSE_REFERENCE {
    tag chrom
    label 'reference_download'
    publishDir { "${output_dir}/imputation/reference_provenance" }, mode: 'copy', pattern: '*.json'
    input:
    val chrom
    path runtime
    path code
    val output_dir
    output:
    tuple val(chrom), path("chunks.${chrom}.tsv"), path("reference.${chrom}.bcf"), path("reference.${chrom}.bcf.csi"), path("map.${chrom}.txt"), emit: chunks
    path '*.provenance.json'
    script:
    """
    '${params.python}' ${code}/glimpse2.py reference --config ${runtime} --chrom '${chrom}' --threads ${task.cpus}
    """
}
process GLIMPSE_PHASE {
    tag "${sample}:${chrom}:${chunk}"
    label 'imputation'
    input:
    tuple val(chrom), val(chunk), val(input_region), val(output_region), path(ref), path(ref_index), path(map), val(sample), path(bam), path(bai)
    path runtime
    path code
    output:
    tuple val(chrom), val(chunk), path("${sample}.${chrom}.${chunk}.bcf"), path("${sample}.${chrom}.${chunk}.bcf.csi"), emit: chunks
    script:
    """
    '${params.python}' ${code}/glimpse2.py phase --config ${runtime} --chrom '${chrom}' --chunk '${chunk}' --input-region '${input_region}' --output-region '${output_region}' --reference ${ref} --map ${map} --bam ${bam} --threads ${task.cpus}
    """
}
process GLIMPSE_LIGATE {
    tag chrom
    label 'imputation'
    publishDir { "${output_dir}/imputation" }, mode: 'copy'
    input:
    tuple val(chrom), path(chunks), path(indexes)
    path runtime
    path code
    val sample
    val output_dir
    output:
    tuple val(chrom), path("${sample}.${chrom}.imputed.bcf"), path("${sample}.${chrom}.imputed.bcf.csi"), emit: vcf
    script:
    """
    '${params.python}' ${code}/glimpse2.py ligate --config ${runtime} --chrom '${chrom}' --inputs ${chunks.collect { "'${it}'" }.join(' ')} --threads ${task.cpus}
    """
}
process IMPUTED_CLINVAR {
    tag chrom
    label 'imputation'
    publishDir { "${output_dir}/imputed_clinvar/chromosomes" }, mode: 'copy'
    input:
    tuple val(chrom), path(bcf), path(csi)
    tuple path(clinvar), path(clinvar_index)
    path runtime
    path code
    val sample
    val output_dir
    output:
    path "${sample}.${chrom}.imputed_clinvar.json", emit: summary
    path "${sample}.${chrom}.clinvar_all_sites.tsv.gz", emit: table
    path '*.clinvar.bcf*'
    path '*.all_nonreference.tsv.gz', emit: nonreference
    path '*.clinvar_nonreference.tsv.gz'
    path '*.pathogenic_labels.tsv.gz', emit: pathogenic
    script:
    """
    '${params.python}' ${code}/imputed_clinvar.py annotate --config ${runtime} --sample '${sample}' --chrom '${chrom}' --imputed '${bcf}' --clinvar '${clinvar}' --threads ${task.cpus}
    """
}
process IMPUTED_CLINVAR_REPORT {
    label 'imputation'
    publishDir { "${output_dir}/imputed_clinvar" }, mode: 'copy'
    input:
    path summaries
    path tables
    path runtime
    path code
    val sample
    val output_dir
    output:
    path '*.imputed_clinvar.json'
    path '*.imputed_clinvar.xlsx'
    path '*.imputed_clinvar.html'
    script:
    """
    '${params.python}' ${code}/imputed_clinvar.py report --config ${runtime} --sample '${sample}' --summaries ${summaries.collect { "'${it}'" }.join(' ')} --tables ${tables.collect { "'${it}'" }.join(' ')}
    """
}
process PGS_PREPARE {
    tag pgs_id
    label 'pgs_download'
    publishDir { "${output_dir}/polygenic/models" }, mode: 'copy'
    input:
    val pgs_id
    path runtime
    path code
    val output_dir
    output:
    tuple val(pgs_id), path("${pgs_id}.weights.tsv.gz"), path("${pgs_id}.model.json"), emit: model
    script:
    """
    '${params.python}' ${code}/polygenic.py prepare --config ${runtime} --pgs-id '${pgs_id}'
    """
}
process PGS_SCORE {
    tag pgs_id
    label 'imputation'
    publishDir { "${output_dir}/polygenic/scores" }, mode: 'copy'
    input:
    tuple val(pgs_id), path(weights), path(model)
    path imputed
    path ancestry_json
    path runtime
    path code
    val sample
    val output_dir
    output:
    path '*.score.json', emit: score
    path '*.variant_audit.tsv.gz', emit: audit
    script:
    """
    '${params.python}' ${code}/polygenic.py score --config ${runtime} --sample '${sample}' --pgs-id '${pgs_id}' --weights '${weights}' --model '${model}' --ancestry '${ancestry_json}' --imputed ${imputed.findAll { it.toString().endsWith('.bcf') }.collect { "'${it}'" }.join(' ')}
    """
}
process PGS_REFERENCE_STATS {
    tag sample
    label 'imputation'
    cpus { Math.min(16, params.threads.toString().toInteger()) }
    publishDir { "${output_dir}/polygenic/reference_statistics" }, mode: 'copy'
    input:
    path references
    path scores
    path audits
    path ancestry_json
    path runtime
    path code
    val sample
    val output_dir
    output:
    path '*.pgs_reference_stats.json', emit: statistics
    path '*.reference_scores.tsv.gz'
    script:
    """
    '${params.python}' ${code}/pgs_reference_stats.py --config ${runtime} --threads ${task.cpus} --sample '${sample}' --ancestry '${ancestry_json}' --references ${references.collect { "'${it}'" }.join(' ')} --scores ${scores.collect { "'${it}'" }.join(' ')} --audits ${audits.collect { "'${it}'" }.join(' ')}
    """
}
process SPECIAL_ANNOTATIONS {
    tag sample
    label 'imputation'
    publishDir { "${output_dir}/special_annotations" }, mode: 'copy'
    input:
    tuple val(sample), path(germline), path(germline_index), path(somatic), path(somatic_index)
    tuple path(clinvar), path(clinvar_index)
    path evidence_tables
    path ancestry
    path runtime
    path code
    val output_dir
    output:
    path "${sample}.special_annotations", emit: report
    script:
    """
    '${params.python}' ${code}/special_annotations.py --config ${runtime} --sample '${sample}' --somatic '${somatic}' --clinvar '${clinvar}' --evidence-tables ${evidence_tables.collect { "'${it}'" }.join(' ')} --ancestry ${ancestry.collect { "'${it}'" }.join(' ')}
    """
}
process HOMOZYGOSITY_REPORT {
    label 'imputation'
    publishDir { "${output_dir}/homozygosity" }, mode: 'copy'
    input:
    path imputed
    path runtime
    path code
    val sample
    val output_dir
    output:
    path "${sample}.homozygosity", emit: report
    script:
    """
    '${params.python}' ${code}/homozygosity_report.py --config ${runtime} --sample '${sample}' --imputed ${imputed.findAll { it.toString().endsWith('.bcf') }.collect { "'${it}'" }.join(' ')} --threads ${task.cpus}
    """
}

process CARDIOMYOPATHY_REPORT {
    label 'imputation'
    publishDir { "${output_dir}/cardiomyopathy" }, mode: 'copy'
    input:
    path interpretation
    path runtime
    path code
    val sample
    val output_dir
    output:
    path "${sample}.cardiomyopathy", emit: report
    script:
    """
    '${params.python}' ${code}/cardiomyopathy_report.py --config ${runtime} --sample '${sample}' --interpretation '${interpretation}'
    """
}

process CANCER_REPORT {
    tag sample
    label 'imputation'
    publishDir { "${output_dir}/cancer_review" }, mode: 'copy'
    input:
    path interpretation
    path special
    path runtime
    path code
    val sample
    val output_dir
    output:
    path "${sample}.cancer_review", emit: report
    script:
    """
    '${params.python}' ${code}/cancer_report.py --config ${runtime} --sample '${sample}' --interpretation '${interpretation}' --special '${special}'
    """
}

process UNIFIED_VCF {
    tag sample
    label 'imputation'
    publishDir { "${output_dir}/cohort_vcf" }, mode: 'copy'
    input:
    tuple val(sample), path(germline), path(germline_index), path(somatic), path(somatic_index)
    tuple path(clinvar), path(clinvar_index)
    path evidence_tables
    path imputed_tables
    path imputed
    path interpretation
    path special
    path runtime
    path code
    val output_dir
    output:
    path "${sample}.cohort_vcf", emit: bundle
    script:
    """
    '${params.python}' ${code}/unified_vcf.py --config ${runtime} --sample '${sample}' --germline '${germline}' --somatic '${somatic}' --clinvar '${clinvar}' --evidence-tables ${evidence_tables.collect { "'${it}'" }.join(' ')} --imputed-tables ${imputed_tables.collect { "'${it}'" }.join(' ')} --imputed ${imputed.findAll { it.toString().endsWith('.bcf') }.collect { "'${it}'" }.join(' ')} --interpretation ${interpretation.collect { "'${it}'" }.join(' ')} --special ${special.collect { "'${it}'" }.join(' ')} --threads ${task.cpus}
    """
}
process PGS_REPORT {
    label 'imputation'
    publishDir { "${output_dir}/polygenic" }, mode: 'copy'
    input:
    path scores
    path reference_stats
    path coverage
    path runtime
    path code
    val sample
    val output_dir
    output:
    path '*.polygenic.json'
    path '*.polygenic.html'
    path '*.polygenic_scores.tsv'
    path '*.polygenic_scores.xlsx'
    script:
    """
    '${params.python}' ${code}/polygenic.py report --config ${runtime} --sample '${sample}' --coverage '${coverage}' --reference-stats ${reference_stats.collect { "'${it}'" }.join(' ')} --scores ${scores.collect { "'${it}'" }.join(' ')}
    """
}
process FINDING_EXPLANATIONS {
    tag sample
    label 'imputation'
    publishDir { "${output_dir}/explanations" }, mode: 'copy'
    input:
    path trait_tables
    path evidence_tables
    path imputed_labels
    path scores
    path audits
    tuple path(clinvar), path(clinvar_index)
    path runtime
    path code
    val sample
    val output_dir
    output:
    path "${sample}.explanations", emit: report
    script:
    def clinical = (evidence_tables.findAll { it.toString().endsWith('.pathogenic_labels.tsv.gz') } + imputed_labels).sort { it.getFileName().toString() }
    """
    '${params.python}' ${code}/finding_explanations.py --config ${runtime} --sample '${sample}' --traits '${sample}.traits.tsv' --gwas '${sample}.gwas.tsv' --clinvar '${clinvar}' --clinical ${clinical.collect { "'${it}'" }.join(' ')} --scores ${scores.collect { "'${it}'" }.join(' ')} --audits ${audits.collect { "'${it}'" }.join(' ')}
    """
}
process INTERPRETATION_REPORT {
    tag sample
    cpus { report_threads }
    memory params.memory_gb != null ? "${Math.min(32.0, params.memory_gb as double)} GB" : "${Math.min(32, nextflow.util.MemoryUnit.of(params.memory.toString().trim() ==~ /[0-9]+(?:\.[0-9]+)?/ ? params.memory.toString()+'GB' : params.memory.toString().trim().replaceAll(/(?i)([GMT])I?B?$/, '$1B')).toGiga())} GB"
    publishDir { "${output_dir}/interpretation" }, mode: 'copy'
    input:
    path explanations
    tuple val(sample), path(bam), path(bai)
    path qc
    path evidence_summary
    path evidence_tables
    path imputed_clinvar_tables
    path ancestry_reports
    path scores
    path reference_stats
    path coverage
    path runtime
    path code
    val output_dir
    val report_threads
    output:
    path "${sample}.interpretation", emit: report
    script:
    """
    '${params.python}' ${code}/interpretation_report.py --config ${runtime} --sample '${sample}' --coverage '${coverage}' --reference-stats ${reference_stats.collect { "'${it}'" }.join(' ')} --explanations '${explanations}' --bam '${bam}' --qc '${qc}' --evidence-summary '${evidence_summary}' --evidence-tables ${evidence_tables.collect { "'${it}'" }.join(' ')} --imputed-clinvar-tables ${imputed_clinvar_tables.collect { "'${it}'" }.join(' ')} --ancestry ${ancestry_reports.collect { "'${it}'" }.join(' ')} --scores ${scores.collect { "'${it}'" }.join(' ')}
    """
}
workflow {
    def prepared = prepareWorkflow(params, args, projectDir, launchDir, workflow.commandLine)
    if (prepared.help) log.info prepared.help
    if (prepared.run) {
        params.python = prepared.python
        log.info "Output: ${prepared.output}; budget: ${prepared.threads} CPUs / ${prepared.memory_gb} GB"
        def reused = prepared.reused ?: [:]
        def analyses = prepared.analysis_reused
        def reuseFiles = { group, tag -> (analyses[group][tag]?.files ?: []).collect { file(it,checkIfExists:true) } }
        log.info "--continue: reusing ${reused.size()} verified completed core tasks"
        def saved = { key -> (reused[key] ?: []).collect { file(it, checkIfExists: true) } }
        runtime = Channel.value(file(prepared.runtime, checkIfExists: true))
        code = Channel.value(file(prepared.code, checkIfExists: true))
        def rows = prepared.lane_rows
        def externalBam = prepared.bam ? [tuple(prepared.sample,file(prepared.bam,checkIfExists:true))] : []
        IMPORT_BAM(Channel.fromList(reused["merge:${prepared.sample}"] ? [] : externalBam),runtime,code,prepared.output)
        def alignReuse = rows.findAll { !reused["merge:${it.sample}"] && reused["align:${it.sample}:${it.lane}"] }.collect { r ->
            tuple(r.role, r.sample, saved.call("align:${r.sample}:${r.lane}")[0])
        }
        lanes = Channel.fromList(rows.findAll { !(prepared.bam && it.role=='patient') && !reused["merge:${it.sample}"] && !reused["align:${it.sample}:${it.lane}"] })
            .map { r -> tuple(r.role, r.sample, r.lane, r.library, [r.fastq_1, r.fastq_2].findAll { it }.collect { file(it, checkIfExists: true) }) }
        ALIGN(lanes, runtime, code, prepared.output)
        aligned = ALIGN.out.bam.mix(Channel.fromList(alignReuse))
        MERGE_MARKDUP(aligned.groupTuple(by: [0, 1]), runtime, code, prepared.output)
        def samples = rows.collect { [role:it.role, sample:it.sample] }.unique { it.sample }
        def mergeReuse = samples.findAll { reused["merge:${it.sample}"] }.collect { r ->
            def x=saved.call("merge:${r.sample}"); tuple(r.role,r.sample,x[0],x[1])
        }
        def qcReuse = samples.findAll { reused["merge:${it.sample}"] }.collect { r ->
            tuple(r.role,r.sample,saved.call("merge:${r.sample}")[2])
        }
        all_bams = MERGE_MARKDUP.out.bam.mix(IMPORT_BAM.out.bam).mix(Channel.fromList(mergeReuse))
        all_qc = MERGE_MARKDUP.out.qc.mix(IMPORT_BAM.out.qc).mix(Channel.fromList(qcReuse))
        patient_bams = all_bams.filter { role,s,bam,bai -> role=='patient' }.map { role,s,bam,bai -> tuple(s,bam,bai) }
        patient_qc = all_qc.filter { role,s,qc -> role=='patient' }.map { role,s,qc -> tuple(s,qc) }
        def coverageReuse = samples.findAll { reused["merge:${it.sample}"] }.collect { r -> tuple(r.role,r.sample,file("${saved.call('merge:'+r.sample)[0].parent}/${r.sample}.coverage.txt",checkIfExists:true)) }
        patient_coverage = MERGE_MARKDUP.out.coverage.mix(IMPORT_BAM.out.coverage).mix(Channel.fromList(coverageReuse)).filter { role,s,p -> role=='patient' }.map { role,s,p -> p }.first()
        // Imputation starts from the aligned sample alongside direct calling.
        reference_panels=Channel.value([])
        imputed=Channel.value([])
        imputed_chromosomes=Channel.empty()
        if (prepared.glimpse2) {
            def imputationReuse=prepared.imputation_reused
            def referenceReuse=imputationReuse.references.collect { ch,paths ->
                def x=paths.collect { file(it,checkIfExists:true) }
                tuple(ch,x[0],x[1],x[2],x[3])
            }
            GLIMPSE_REFERENCE(Channel.fromList(prepared.glimpse_chromosomes.findAll { !imputationReuse.references[it] }),runtime,code,prepared.output)
            references=GLIMPSE_REFERENCE.out.chunks.mix(Channel.fromList(referenceReuse))
            reference_panels=references.map { chrom,table,ref,index,map -> [ref,index] }.collect()
            chunk_inputs=references.flatMap { chrom,table,ref,index,map ->
                table.readLines().findAll { it.trim() && !it.startsWith('#') }.collect { line ->
                    def fields=line.trim().split(/\s+/)
                    tuple(chrom,fields[0],fields[2],fields[3],ref,index,map)
                }
            }.combine(patient_bams)
            GLIMPSE_PHASE(chunk_inputs.filter { ch,id,inputRegion,outputRegion,ref,idx,map,s,bam,bai -> !imputationReuse.phases["${ch}:${id}"] },runtime,code)
            def phaseReuse=imputationReuse.phases.collect { key,paths ->
                def parts=key.split(':')
                tuple(parts[0],parts[1],file(paths[0],checkIfExists:true),file(paths[1],checkIfExists:true))
            }
            phased=GLIMPSE_PHASE.out.chunks.mix(Channel.fromList(phaseReuse))
            GLIMPSE_LIGATE(phased.filter { ch,id,bcf,csi -> !imputationReuse.ligated[ch] }.map { ch,id,bcf,csi -> tuple(ch,bcf,csi) }.groupTuple(),runtime,code,prepared.sample,prepared.output)
            def ligatedReuse=imputationReuse.ligated.collect { ch,paths ->
                tuple(ch,file(paths[0],checkIfExists:true),file(paths[1],checkIfExists:true))
            }
            imputed_chromosomes=GLIMPSE_LIGATE.out.vcf.mix(Channel.fromList(ligatedReuse))
            imputed=imputed_chromosomes.map { ch,bcf,csi -> [bcf,csi] }.collect().map { paths -> paths.sort { it.getFileName().toString() } }
        }
        pon_resource = Channel.value(tuple([], []))
        if (prepared.pon_mode == 'build') {
            normal_bams=all_bams.filter { role,s,bam,bai -> role=='normal' && !reused["normal_mutect:${s}"] && !reused['create_pon'] }
                .map { role,s,bam,bai -> tuple(s,bam,bai) }
            NORMAL_MUTECT(normal_bams,runtime,code,prepared.output)
            def normalReuse=samples.findAll { it.role=='normal' && reused["normal_mutect:${it.sample}"] }.collect { r ->
                def x=saved.call("normal_mutect:${r.sample}"); tuple(r.sample,x[0],x[1])
            }
            normal_calls=NORMAL_MUTECT.out.vcf.mix(Channel.fromList(normalReuse)).collect(flat:false)
                .filter { !reused['create_pon'] }.map { calls ->
                    def ordered=calls.sort { it[0] }; tuple(ordered.collect { it[1] },ordered.collect { it[2] })
                }
            CREATE_PON(normal_calls,runtime,code,prepared.output)
            def ponReuse=reused['create_pon'] ? [tuple(saved.call('create_pon')[0],saved.call('create_pon')[1])] : []
            pon_resource=CREATE_PON.out.pon.mix(Channel.fromList(ponReuse))
        } else if (prepared.pon_mode=='vcf') {
            pon_resource=Channel.value(tuple(file(prepared.pon_vcf,checkIfExists:true),file(prepared.pon_index,checkIfExists:true)))
        }
        GERMLINE(patient_bams.filter { s,bam,bai -> !prepared.germline_vcf && !reused["germline:${s}"] },runtime,code,prepared.output)
        SOMATIC(patient_bams.filter { s,bam,bai -> !prepared.somatic_vcf && !reused["somatic:${s}"] },pon_resource,runtime,code,prepared.output)
        PREPARE_CLINVAR(reused['prepare_clinvar'] ? Channel.empty() : runtime,code,prepared.output)
        def clinicalReuse=reused['prepare_clinvar'] ? [tuple(saved.call('prepare_clinvar')[0],saved.call('prepare_clinvar')[1])] : []
        clinical=PREPARE_CLINVAR.out.database.mix(Channel.fromList(clinicalReuse)).first()
        def variantReuse=['germline','somatic'].findAll { reused["${it}:${prepared.sample}"] }.collect { kind ->
            def x=saved.call("${kind}:${prepared.sample}");tuple(prepared.sample,kind,x[0],x[1])
        }
        def importedCalls=['germline','somatic'].findAll { prepared[it+'_vcf'] && !reused["${it}:${prepared.sample}"] }.collect { kind -> tuple(prepared.sample,kind,file(prepared[kind+'_vcf'],checkIfExists:true)) }
        IMPORT_CALLS(Channel.fromList(importedCalls),runtime,code,prepared.output)
        variants=GERMLINE.out.vcf.mix(SOMATIC.out.vcf).mix(IMPORT_CALLS.out.vcf).mix(Channel.fromList(variantReuse))
        ANNOTATE(variants.filter { s,k,v,i -> !reused["annotate:${s}:${k}"] },clinical,runtime,code,prepared.output)
        ANCESTRY(patient_bams.filter { s,bam,bai -> !reused["ancestry:${s}"] },runtime,code,prepared.output)
        def ancReuse=reused["ancestry:${prepared.sample}"] ? [tuple(prepared.sample,saved.call("ancestry:${prepared.sample}")[0])] : []
        ancestry=ANCESTRY.out.summary.mix(Channel.fromList(ancReuse))
        ancestry_reports = Channel.value([])
        if (prepared.ancestry_explorer) {
            ANCESTRY_EXPLORER(patient_bams,imputed,reference_panels,patient_qc.map { s,q -> q }.first(),ancestry.map { s,a -> a }.first(),runtime,code,prepared.output)
            ancestry_reports = ANCESTRY_EXPLORER.out.report.collect()
        }
        def annotationReuse=['germline','somatic'].findAll { reused["annotate:${prepared.sample}:${it}"] }.collect { kind ->
            def work=saved.call("annotate:${prepared.sample}:${kind}")[0].parent
            tuple(prepared.sample,kind,file("${work}/${prepared.sample}.${kind}.all_variants.tsv",checkIfExists:true))
        }
        def annotationVcfReuse=['germline','somatic'].findAll { reused["annotate:${prepared.sample}:${it}"] }.collect { kind ->
            def work=saved.call("annotate:${prepared.sample}:${kind}")[0].parent
            tuple(prepared.sample,kind,file("${work}/${prepared.sample}.${kind}.annotated.vcf.gz",checkIfExists:true),file("${work}/${prepared.sample}.${kind}.annotated.vcf.gz.tbi",checkIfExists:true))
        }
        annotated_vcfs=ANNOTATE.out.annotated_vcf.mix(Channel.fromList(annotationVcfReuse)).groupTuple().map { sample,kinds,vcfs,indexes ->
            if (kinds.size()!=2 || !kinds.containsAll(['germline','somatic'])) error "Both annotated VCFs are required for ${sample}"
            def g=kinds.indexOf('germline'); def s=kinds.indexOf('somatic')
            tuple(sample,vcfs[g],indexes[g],vcfs[s],indexes[s])
        }
        annotation_tables=ANNOTATE.out.table.mix(Channel.fromList(annotationReuse)).groupTuple().map { sample,kinds,tables ->
            if (kinds.size()!=2 || !kinds.containsAll(['germline','somatic'])) error "Both annotation tables are required for ${sample}"
            tuple(sample,tables[kinds.indexOf('germline')],tables[kinds.indexOf('somatic')])
        }
        VARIANT_EVIDENCE(annotation_tables.filter { s,g,t -> !analyses.evidence[s] },imputed,runtime,code,prepared.output)
        TRAITS_GWAS(patient_bams.filter { s,bam,bai -> !analyses.traits[s] },imputed,Channel.value(prepared.cached_trait_files.collect { file(it,checkIfExists:true) }),runtime,code,prepared.output)
        def oldEvidence = analyses.evidence[prepared.sample] ? [reuseFiles.call('evidence',prepared.sample).findAll { it.toString().endsWith('.tsv.gz') }] : []
        def oldTraits = analyses.traits[prepared.sample] ? [reuseFiles.call('traits',prepared.sample).findAll { it.toString().endsWith('.tsv') }] : []
        evidence_tables = VARIANT_EVIDENCE.out.tables.mix(Channel.fromList(oldEvidence)).first()
        trait_tables = TRAITS_GWAS.out.tables.mix(Channel.fromList(oldTraits)).first()
        def oldEvidenceSummary = analyses.evidence[prepared.sample] ? reuseFiles.call('evidence',prepared.sample).findAll { it.toString().endsWith('.variant_evidence.json') } : []
        evidence_summary = VARIANT_EVIDENCE.out.summary.mix(Channel.fromList(oldEvidenceSummary)).first()
        imputed_clinvar_tables = Channel.value([])
        imputed_labels = Channel.value([])
        imputed_nonreference = Channel.value([])
        reference_stats = Channel.value([])
        interpretation_bundle = Channel.value([])
        scores_for_explanation = Channel.value([])
        audits_for_explanation = Channel.value([])
        if (prepared.imputed_clinvar) {
            IMPUTED_CLINVAR(imputed_chromosomes.filter { ch,bcf,csi -> !analyses.imputed_clinvar[ch] },clinical,runtime,code,prepared.sample,prepared.output)
            def oldIC = analyses.imputed_clinvar.collect { ch,item -> reuseFiles.call('imputed_clinvar',ch) }.flatten()
            imputed_nonreference = IMPUTED_CLINVAR.out.nonreference.mix(Channel.fromList(oldIC.findAll { it.toString().endsWith('.all_nonreference.tsv.gz') })).collect()
            ic_summaries = IMPUTED_CLINVAR.out.summary.mix(Channel.fromList(oldIC.findAll { it.toString().endsWith('.imputed_clinvar.json') }))
            ic_tables = IMPUTED_CLINVAR.out.table.mix(Channel.fromList(oldIC.findAll { it.toString().endsWith('.clinvar_all_sites.tsv.gz') }))
            imputed_labels = IMPUTED_CLINVAR.out.pathogenic.mix(Channel.fromList(oldIC.findAll { it.toString().endsWith('.pathogenic_labels.tsv.gz') })).collect().map { files -> files.sort { it.getFileName().toString() } }
            imputed_clinvar_tables = ic_tables.collect().map { files -> files.sort { it.getFileName().toString() } }
            IMPUTED_CLINVAR_REPORT(ic_summaries.collect().map { files -> files.sort { it.getFileName().toString() } },imputed_clinvar_tables,runtime,code,prepared.sample,prepared.output)
        }
        if (prepared.polygenic) {
            PGS_PREPARE(Channel.fromList(prepared.pgs_ids.findAll { !analyses.pgs_prepare[it] }),runtime,code,prepared.output)
            def oldModels = analyses.pgs_prepare.collect { id,item -> def f=reuseFiles.call('pgs_prepare',id); tuple(id,f[0],f[1]) }
            models = PGS_PREPARE.out.model.mix(Channel.fromList(oldModels))
            PGS_SCORE(models.filter { id,weights,model -> !analyses.pgs_score[id] },imputed,ancestry.map { sample,summary -> summary }.first(),runtime,code,prepared.sample,prepared.output)
            def oldScores = analyses.pgs_score.collect { id,item -> reuseFiles.call('pgs_score',id) }.flatten()
            scores_for_explanation = PGS_SCORE.out.score.mix(Channel.fromList(oldScores.findAll { it.toString().endsWith('.score.json') })).collect().map { files -> files.sort { it.getFileName().toString() } }
            audits_for_explanation = PGS_SCORE.out.audit.mix(Channel.fromList(oldScores.findAll { it.toString().endsWith('.variant_audit.tsv.gz') })).collect().map { files -> files.sort { it.getFileName().toString() } }
            if (prepared.pgs_reference_stats) {
                PGS_REFERENCE_STATS(reference_panels,scores_for_explanation,audits_for_explanation,ancestry.map { s,a -> a }.first(),runtime,code,prepared.sample,prepared.output)
                reference_stats=PGS_REFERENCE_STATS.out.statistics.collect()
            }
            PGS_REPORT(scores_for_explanation,reference_stats,patient_coverage,runtime,code,prepared.sample,prepared.output)
        }
        if (prepared.explain_findings) {
            FINDING_EXPLANATIONS(trait_tables,evidence_tables,imputed_labels,scores_for_explanation,audits_for_explanation,clinical,runtime,code,prepared.sample,prepared.output)
        }
        if (prepared.interpretation_report) {
            INTERPRETATION_REPORT(FINDING_EXPLANATIONS.out.report,patient_bams,patient_qc.map { s,q -> q }.first(),evidence_summary,evidence_tables,imputed_clinvar_tables,ancestry_reports,scores_for_explanation,reference_stats,patient_coverage,runtime,code,prepared.output,prepared.llm_threads)
            interpretation_bundle=INTERPRETATION_REPORT.out.report.collect()
            if (prepared.cardiomyopathy_report) {
                CARDIOMYOPATHY_REPORT(INTERPRETATION_REPORT.out.report,runtime,code,prepared.sample,prepared.output)
            }
        }
        SPECIAL_ANNOTATIONS(annotated_vcfs,clinical,evidence_tables,ancestry_reports,runtime,code,prepared.output)
        if (prepared.interpretation_report && prepared.cancer_report) {
            CANCER_REPORT(INTERPRETATION_REPORT.out.report,SPECIAL_ANNOTATIONS.out.report,runtime,code,prepared.sample,prepared.output)
        }
        if (prepared.homozygosity_report && prepared.glimpse2) {
            HOMOZYGOSITY_REPORT(imputed,runtime,code,prepared.sample,prepared.output)
        }
        if (prepared.unified_vcf) {
            UNIFIED_VCF(annotated_vcfs,clinical,evidence_tables,imputed_nonreference,imputed,interpretation_bundle,SPECIAL_ANNOTATIONS.out.report.collect(),runtime,code,prepared.output)
        }
        def finalWorkflowMetadata = workflow
        workflow.onComplete {
            def command = [prepared.python.toString(), '-B', "${prepared.code}/reporting.py".toString(),
                '--output', prepared.output.toString(),
                '--workflow-status', finalWorkflowMetadata.success ? 'completed' : 'incomplete',
                '--run-name', finalWorkflowMetadata.runName.toString()]
            def result = new ProcessBuilder(command).inheritIO().start().waitFor()
            if (result != 0) {
                error "Detailed report generation failed (exit ${result}); analysis files remain available in ${prepared.output}."
            }
            log.info "Open ${prepared.output}/START_HERE.html for the organized outputs and full disease report."
        }


    }
}
