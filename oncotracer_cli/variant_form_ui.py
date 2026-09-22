"""One ordered variant form for FASTQ setup and existing-BAM setup."""
from html import escape


def path_field(key, label, *, kind='folder', hint='', detect=True, placeholder='Autodetect or choose a path'):
    button = (f'<button type="button" data-variant-detect="{key}" aria-label="Autodetect {escape(label)}">Autodetect</button>' if detect else '')
    return f'''<div class="variant-path"><label for="{key}">{label}</label>
<div class="variant-path-row"><input id="{key}" spellcheck="false" autocomplete="off" placeholder="{placeholder}">
<div class="variant-path-actions">{button}<button type="button" data-browse="{key}" data-kind="{kind}">Browse</button></div></div>
<p id="{key}-status" class="variant-path-status hint" aria-live="polite"></p>{f'<p class="hint">{hint}</p>' if hint else ''}</div>'''


def variant_form(*, existing_bam=False):
    specimen = 'specimen' if existing_bam else 'variant'
    callers = 'callers' if existing_bam else 'variant-callers'
    clair3 = 'clair3-fields' if existing_bam else 'variant-clair3-field'
    clairsto = 'clairsto-fields' if existing_bam else 'variant-clairsto-field'
    ffpe = 'ffperase-section' if existing_bam else 'variant-ffperase-fields'
    ffpe_paths = 'ffperase-resource-fields' if existing_bam else 'variant-ffperase-resource-fields'
    varlo = 'varlociraptor-fields' if existing_bam else 'variant-varlociraptor-fields'
    annovar = 'annovar-fields' if existing_bam else 'variant-annovar-fields'
    note = 'caller-note' if existing_bam else 'variant-preservation-note'
    threads = '<div class="field variant-short"><label for="threads">CPU threads</label><input id="threads" type="number" min="1" step="1" value="4"></div>' if existing_bam else ''
    return f'''<div class="variant-form">
<nav class="variant-nav" aria-label="Variant settings"><button type="button" data-variant-section="variant-specimen-section">1 · Specimen &amp; callers</button><button type="button" data-variant-section="variant-tools-section">2 · Tools &amp; models</button><button type="button" data-variant-section="variant-filter-section">3 · Filtering</button><button type="button" data-variant-section="variant-annotation-section">4 · Annotation</button></nav>
<section class="variant-section" id="variant-specimen-section" aria-labelledby="variant-specimen-title">
<div class="variant-section-heading"><span class="variant-step">1</span><div><h3 id="variant-specimen-title">Specimen and callers</h3><p class="hint">Choose the preservation and the analyses to run.</p></div></div>
<div class="variant-specimen" role="group" aria-label="Sample preservation"><button type="button" class="platform" id="{specimen}-fresh" aria-pressed="false"><strong>Fresh</strong><span>Fresh or frozen material</span></button><button type="button" class="platform" id="{specimen}-ffpe" aria-pressed="false"><strong>FFPE</strong><span>Formalin-fixed tissue</span></button></div>
<p id="{note}" class="hint" role="status">Choose Fresh or FFPE from your specimen records.</p>
<h4>Variant callers</h4><div id="{callers}" class="variant-caller-list" role="group" aria-label="Variant callers"></div>
<details class="variant-explainer"><summary>How caller results are used</summary><p class="hint">Mutect2 and ClairS-TO produce tumor-only candidates. FreeBayes, bcftools and Clair3 use germline-style calling. Each sample is analyzed independently; Normal and Cancer assignments do not create matched pairs or establish somatic origin.</p></details>
</section>
<section class="variant-section" id="variant-tools-section" aria-labelledby="variant-tools-title">
<div class="variant-section-heading"><span class="variant-step">2</span><div><h3 id="variant-tools-title">Caller tools and models</h3><p class="hint">Start with Autodetect. Open paths only to review or change them.</p></div></div>
<!-- VARIANT_RESOURCE_PANEL -->
<p id="variant-tool-note" class="hint"></p>{threads}
<div id="variant-tool-prefix-fields"><details class="variant-path-details"><summary>Caller environment <span class="variant-path-summary" data-path-summary="variant_tool_prefix"></span></summary>
{path_field('variant_tool_prefix', 'Variant tool environment', hint='Leave blank to use tools already available to OncoTracer.')}</details></div>
<div id="{clair3}" class="variant-tool-block" hidden><div class="variant-block-heading"><h4>Clair3 model</h4><button type="button" data-variant-detect="variant_clair3_model">Autodetect model</button></div><p class="hint">Required for Clair3. Confirm the model matches your flow cell and basecaller.</p><p class="variant-required-summary" data-path-summary="variant_clair3_model" data-required-path="true"></p><details class="variant-path-details"><summary>Model path</summary>{path_field('variant_clair3_model','Clair3 model folder')}</details></div>
<div id="{clairsto}" class="variant-tool-block" hidden><h4>ClairS-TO model</h4><label for="variant_clairsto_platform">Sequencing / basecaller preset</label><div class="variant-path-row"><input id="variant_clairsto_platform" placeholder="Enter the preset supported by your ClairS-TO version"><button type="button" data-variant-detect="variant_clairsto_platform">Model guidance</button></div><p class="hint">Choose this from the sequencing records. Chemistry is not inferred from filenames.</p><div id="variant-clairsto-sif-fields"><details class="variant-path-details"><summary>Use an existing ClairS-TO container (optional) <span class="variant-path-summary" data-path-summary="variant_clairsto_sif"></span></summary>{path_field('variant_clairsto_sif','ClairS-TO SIF',kind='asset',hint='An alternative to the installed executable. Requires local Apptainer or Singularity.')}</details></div></div>
<details class="variant-path-details"><summary>Target regions (optional)</summary>{path_field('variant_targets_bed','Variant target BED',kind='asset',detect=False,placeholder='Empty = whole genome',hint='Choose the BED for your assay and reference build. Target regions are not guessed automatically.')}</details>
</section>
<section class="variant-section" id="variant-filter-section" aria-labelledby="variant-filter-title">
<div class="variant-section-heading"><span class="variant-step">3</span><div><h3 id="variant-filter-title">Filtering and FFPE</h3><p class="hint">Caller filters are retained. Add the assessments needed for this run.</p></div></div>
<div id="{ffpe}" class="variant-tool-block" hidden><div class="variant-block-heading"><h4>FFPERASE artifact assessment</h4><button type="button" data-variant-detect="ffperase">Autodetect FFPERASE</button></div><label for="variant_ffperase">FFPE assessment</label><select id="variant_ffperase"><option value="required">Run FFPERASE (required)</option><option value="off">Skip FFPERASE</option></select>
<div id="{ffpe_paths}"><p class="variant-required-summary" data-path-summary="variant_ffperase_root,variant_ffperase_models" data-required-path="true"></p><details class="variant-path-details"><summary>FFPERASE source and models</summary>{path_field('variant_ffperase_root','FFPERASE source folder')}{path_field('variant_ffperase_models','FFPERASE models folder')}</details>
<div id="variant-ffperase-runtime-fields"><details class="variant-path-details"><summary>FFPERASE runtime <span class="variant-path-summary" data-path-summary="variant_ffperase_prefix,variant_ffperase_sif"></span></summary><label for="variant-ffperase-runtime">Run FFPERASE with</label><select id="variant-ffperase-runtime"><option value="native">Native Python environment</option><option value="sif">Existing SIF container</option></select><div id="variant-ffperase-prefix-fields">{path_field('variant_ffperase_prefix','FFPERASE Python environment')}</div><div id="variant-ffperase-sif-fields">{path_field('variant_ffperase_sif','FFPERASE SIF',kind='asset')}</div></details></div></div></div>
<div class="variant-tool-block"><label for="variant_varlociraptor">Varlociraptor evidence assessment</label><select id="variant_varlociraptor"><option value="off">Skip Varlociraptor</option><option value="required">Run Varlociraptor and local FDR filtering</option></select><div id="{varlo}" hidden><p class="hint">Uses the caller tool environment above. The default model assesses variant presence.</p><button type="button" data-variant-detect="variant_tool_prefix">Check Varlociraptor tools</button><div class="field variant-short"><label for="variant_varlociraptor_fdr">Local false discovery rate</label><input id="variant_varlociraptor_fdr" type="number" min="0.000001" max="0.999999" step="any" value="0.05"></div><details class="variant-path-details"><summary>Custom scenario (advanced)</summary>{path_field('variant_varlociraptor_scenario','Varlociraptor scenario YAML',kind='file',detect=False,placeholder='Empty = default PRESENT model',hint='Choose a study-specific scenario explicitly. A scenario is not inferred from other files.')}<div class="grid"><div><label for="variant_varlociraptor_events">Scenario events</label><input id="variant_varlociraptor_events" value="PRESENT"></div><div><label for="variant_varlociraptor_sample">Scenario sample name</label><input id="variant_varlociraptor_sample" value="sample"></div></div></details></div></div>
</section>
<section class="variant-section" id="variant-annotation-section" aria-labelledby="variant-annotation-title">
<div class="variant-section-heading"><span class="variant-step">4</span><div><h3 id="variant-annotation-title">Annotation</h3><p class="hint">Use your local ANNOVAR installation and matching databases.</p></div></div>
<label for="variant_annovar">ANNOVAR annotation</label><select id="variant_annovar"><option value="auto">Use ANNOVAR when available</option><option value="off">Skip ANNOVAR annotation</option></select>
<div id="{annovar}"><div class="variant-block-heading"><p class="hint" data-path-summary="variant_annovar_dir,variant_annovar_db"></p><button type="button" data-variant-detect="annotation">Autodetect ANNOVAR</button></div><details class="variant-path-details"><summary>Installation and database paths</summary>{path_field('variant_annovar_dir','ANNOVAR installation')}{path_field('variant_annovar_db','ANNOVAR database folder',hint='Requires a matching reference build and an unpacked RefSeq TXT / Mrna FASTA pair. Optional ClinVar uses the same build.')}</details><p class="hint">If unavailable, calling results are retained and annotation is reported as unavailable. Installation help appears in resource details.</p></div>
</section></div>'''
