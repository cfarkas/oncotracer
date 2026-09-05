"""Native CNA classifier/report orchestration for OncoTracer v2."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .runtime import (
    CommandRunner,
    OncoTracerError,
    StageLedger,
    atomic_write_json,
    atomic_write_text,
    atomic_write_workflow_summary,
    require_directory,
    require_file,
    utc_now,
)


class ToolchainLike(Protocol):
    classifier_prefix: Path | None
    gistic_prefix: Path | None

    def wrap(self, group: str, command: Sequence[str | Path]) -> list[str]: ...

    def environment(self, group: str) -> dict[str, str | None]: ...


DEFAULTS: dict[str, object] = {
    "cna_classifier_sample_set": "broad_cancer",
    "gistic_window_bp": 100000,
    "min_bins": 3,
    "min_size_mb": 0.5,
    "min_abs_log2": 0.25,
    "include_sex": False,
    "focal_mb": 30,
    "broad_mb": 30,
    "low_events": 10,
    "high_events": 50,
    "ultra_events": 100,
    "high_chromosomes": 8,
    "high_altered_mb": 500,
    "ultra_altered_mb": 1000,
    "nmf_clusters": 3,
    "top_regions": 80,
    "plot_top_features": 60,
    "knowledge_web": True,
    "knowledge_allow_fail": True,
    "knowledge_max_papers": 20,
    "knowledge_timeout": 20,
    "knowledge_sleep": 0.25,
    "knowledge_user_agent": "OncoTracerAI-CNA-knowledge-enrichment/1.0",
    "knowledge_lymphoma_terms": 'lymphoma OR DLBCL OR "diffuse large B-cell lymphoma" OR "large B-cell lymphoma" OR "B-cell lymphoma"',
    "knowledge_cancer_terms": 'cancer OR tumor OR tumour OR carcinoma OR leukemia OR leukaemia OR lymphoma OR sarcoma OR glioma OR "copy number alteration" OR CNA',
    "knowledge_hf_ner": False,
    "knowledge_hf_model": "d4data/biomedical-ner-all",
    "knowledge_literature_llm": True,
    "knowledge_literature_llm_models": "google/flan-t5-small,google/flan-t5-base,Falconsai/medical_summarization",
    "knowledge_literature_llm_local_files_only": False,
    "knowledge_literature_llm_max_features": 24,
    "knowledge_literature_llm_max_input_chars": 2800,
    "knowledge_literature_llm_max_new_tokens": 96,
    "knowledge_llm_threads": 4,
    "knowledge_deep_literature": True,
    "knowledge_deep_max_papers_per_feature": 50,
    "knowledge_deep_top_papers_per_sample": 12,
    "knowledge_deep_enable_llm_ranker": True,
    "knowledge_deep_llm_ranker_models": "google/flan-t5-small,google/flan-t5-base,Falconsai/medical_summarization",
    "knowledge_deep_llm_ranker_local_files_only": False,
    "knowledge_deep_llm_ranker_max_candidates_per_sample": 18,
    "knowledge_literature_reference_llm_selection": True,
    "knowledge_literature_top_references": 8,
    "pathology_use_biomed_models": True,
    "pathology_biomed_models": "microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract,dmis-lab/biobert-base-cased-v1.1,emilyalsentzer/Bio_ClinicalBERT",
    "pathology_biomed_local_files_only": False,
    "pathology_biomed_max_tokens": 256,
    "run_pdf_reports": True,
    "run_clinician_reports": True,
    "clinician_max_drivers": 14,
    "pdf_include_full_events": True,
    "pdf_max_events": 0,
    "run_gistic": True,
    "gistic_required": False,
    "gistic_min_samples": 2,
    "gistic_seg_type": "full",
    "gistic_use_markers": True,
    "gistic_broad": True,
    "gistic_conf": 0.90,
    "gistic_qvt": 0.25,
    "gistic_ta": 0.10,
    "gistic_td": 0.10,
    "gistic_cap": 1.5,
    "gistic_rx": 0,
    "gistic_brlen": 0.70,
    "gistic_join_segment_size": 4,
    "gistic_maxseg": 2500,
    "gistic_scent": "median",
    "gistic_smallmem": 1,
    "gistic_savegene": 1,
    "gistic_armpeel": 1,
    "gistic_smalldisk": 0,
    "gistic_verbose": 20,
}


def _value(config: Mapping[str, object], key: str) -> object:
    return config.get(key, DEFAULTS.get(key))


def _bool(config: Mapping[str, object], key: str) -> bool:
    value = _value(config, key)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def _string(config: Mapping[str, object], key: str) -> str:
    value = _value(config, key)
    return "" if value is None else str(value)


def _sample_set_raw(config: Mapping[str, object]) -> str:
    raw = str(config.get("cna_classifier_sample_set") or config.get("sample_set") or "broad_cancer").strip()
    return raw or "broad_cancer"


def sample_set_key(config: Mapping[str, object]) -> str:
    raw = _sample_set_raw(config)
    head = raw.split(":", 1)[0].split("=", 1)[0]
    key = "_".join(filter(None, __import__("re").split(r"[^a-z0-9]+", head.lower())))
    aliases = {
        "broad_cancer": {"pan", "pancancer", "pan_cancer", "broad", "broad_cancer", "all", "all_cancers", "generic", "solid", "solid_tumor", "solid_tumours", "tumor", "tumour"},
        "lymphoma": {"lymphoma", "lymphomas", "dlbcl", "b_cell_lymphoma", "bcell_lymphoma", "hematolymphoid"},
        "brain_cns": {"brain", "brain_cns", "cns", "glioma", "glioblastoma", "astrocytoma", "meningioma", "pediatric_glioma", "low_grade_glioma"},
        "breast": {"breast", "breast_cancer", "mammary"},
        "pancreas": {"pancreas", "pancreatic", "pancreatic_cancer", "pancreatobiliary", "cholangiocarcinoma", "biliary"},
        "colorectal": {"colon", "colorectal", "crc", "rectal", "rectum"},
        "leukemia": {"leukemia", "leukaemia", "aml", "all", "mds", "myeloid", "myeloid_neoplasm", "hematologic", "haematologic"},
        "lung": {"lung", "nsclc", "sclc", "pulmonary"},
        "prostate": {"prostate", "prostatic"},
        "ovarian": {"ovarian", "ovary", "fallopian_tube", "peritoneal", "hgsoc"},
        "gastric_esophageal": {"gastric", "stomach", "gastroesophageal", "gej", "esophageal", "oesophageal"},
        "sarcoma": {"sarcoma", "soft_tissue", "gist", "liposarcoma", "leiomyosarcoma", "osteosarcoma"},
        "renal": {"kidney", "renal", "rcc", "clear_cell_rcc", "ccrcc"},
        "urothelial": {"bladder", "urothelial", "urinary_tract"},
        "thyroid": {"thyroid"},
        "melanoma": {"melanoma"},
        "liver": {"liver", "hcc", "hepatocellular"},
        "head_neck": {"head_neck", "hnscc", "oral", "oropharyngeal", "laryngeal"},
        "germ_cell": {"germ_cell", "testicular", "seminoma", "nonseminoma"},
        "myeloma": {"myeloma", "multiple_myeloma", "plasma_cell"},
        "neuroblastoma": {"neuroblastoma"},
        "neuroendocrine": {"neuroendocrine", "net", "neuroendocrine_tumor"},
        "pediatric_solid": {"pediatric", "paediatric", "pediatric_solid", "paediatric_solid"},
    }
    for canonical, values in aliases.items():
        if key in values:
            return canonical
    return key or "broad_cancer"


def sample_filter(config: Mapping[str, object]) -> str:
    explicit = config.get("cna_classifier_samples") or config.get("samples") or config.get("sample")
    if explicit:
        return str(explicit).strip()
    raw = _sample_set_raw(config)
    for separator in (":", "="):
        if separator in raw:
            return raw.split(separator, 1)[1].strip()
    if sample_set_key(config) == "lymphoma":
        return "V480,Y2119,U4333,O4789,E4904,X4999,A5465,B5924,K6537,A6566,S6922,Q7164,L7395,N7591,B8017,E9211,M9702,C10174,G11079,P11670,R13729"
    return ""


def _stage(
    name: str,
    command: list[str],
    inputs: list[Path],
    outputs: list[Path],
    *,
    cwd: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    force: bool,
    containment: Mapping[str, str | None] | None = None,
) -> None:
    signature = ledger.signature(name, command, inputs)
    if force or not ledger.reusable(name, signature, outputs):
        cwd.mkdir(parents=True, exist_ok=True)
        runner.run(name, command, cwd=cwd, containment=containment)
        for output in outputs:
            require_file(output, f"{name} output")
        ledger.complete(name, signature, outputs)


def _write_gistic_skip(directory: Path, reason: str, segmentation: str) -> tuple[Path, Path, Path]:
    output = directory / "gistic2_out"
    output.mkdir(parents=True, exist_ok=True)
    status = directory / "gistic2_status.tsv"
    command = directory / "gistic2_command.txt"
    versions = directory / "gistic2_versions.txt"
    atomic_write_text(
        status,
        "status\treason\tsegmentation\tcommand\texecutable\trefgene\n"
        f"skipped\t{reason}\t{segmentation}\tNA\tNA\tNA\n",
    )
    atomic_write_text(command, "")
    atomic_write_text(versions, "")
    atomic_write_text(output / "GISTIC_NOT_RUN.txt", f"GISTIC2 was skipped: {reason}.\n")
    return output, status, command


def _run_gistic(
    root: Path,
    config: Mapping[str, object],
    lpwgs_root: Path,
    prepared: Path,
    output: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: ToolchainLike,
    *,
    force: bool,
) -> tuple[Path, Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    gistic_out = output / "gistic2_out"
    inputs_dir = output / "gistic2_input_files"
    ref_dir = lpwgs_root / ".oncotracer" / "classifier" / "gistic2_refgene"
    gistic_out.mkdir(parents=True, exist_ok=True)
    inputs_dir.mkdir(parents=True, exist_ok=True)
    ref_dir.mkdir(parents=True, exist_ok=True)

    prepared_files = {
        "full": require_file(prepared / "gistic_full.seg", "GISTIC full SEG"),
        "events": require_file(prepared / "gistic_events.seg", "GISTIC events SEG"),
        "markers": require_file(prepared / "gistic_markers.tsv", "GISTIC markers"),
        "metrics": require_file(prepared / "prepare_metrics.json", "classifier prepare metrics"),
    }
    for key, source in prepared_files.items():
        target_name = {
            "full": "gistic_full.seg",
            "events": "gistic_events.seg",
            "markers": "gistic_markers.tsv",
            "metrics": "prepare_metrics.json",
        }[key]
        shutil.copy2(source, inputs_dir / target_name)

    segmentation = _string(config, "gistic_seg_type") or "full"
    required = _bool(config, "gistic_required")
    if not _bool(config, "run_gistic"):
        return _write_gistic_skip(output, "--run_gistic false", segmentation)

    try:
        metrics = json.loads(prepared_files["metrics"].read_text(encoding="utf-8"))
        sample_count = int(metrics.get("samples_total", 0))
    except (OSError, ValueError, json.JSONDecodeError):
        sample_count = 0
    minimum_samples = int(_value(config, "gistic_min_samples") or 2)
    if sample_count < minimum_samples:
        reason = f"not_enough_samples_for_gistic_n={sample_count}_min={minimum_samples}"
        if required:
            raise OncoTracerError(reason)
        return _write_gistic_skip(output, reason, segmentation)

    if toolchain.gistic_prefix is not None:
        executable_available = (toolchain.gistic_prefix / "bin" / "gistic2").is_file()
    else:
        executable_available = shutil.which("gistic2") is not None
    if not executable_available:
        reason = "gistic2 executable not found"
        if required:
            raise OncoTracerError(reason)
        return _write_gistic_skip(output, reason, segmentation)
    gistic_environment = toolchain.environment("gistic")

    ref_value = str(config.get("gistic_refgene") or "auto")
    if ref_value and ref_value != "auto":
        refgene = require_file(Path(ref_value), "GISTIC hg38 refgene")
    else:
        refgene = ref_dir / "hg38.UCSC.add_miR.160920.refgene.mat"
        if not refgene.is_file() or refgene.stat().st_size == 0:
            helper = require_file(
                root / "bin" / "cna_classifier_nf" / "bin" / "download_gistic_hg38_refgene.sh",
                "GISTIC hg38 refgene helper",
            )
            result = runner.run(
                "classifier-gistic-refgene",
                ["bash", helper, ref_dir],
                cwd=root,
                containment=gistic_environment,
                check=False,
            )
            if result.returncode != 0 or not refgene.is_file() or refgene.stat().st_size == 0:
                reason = "missing hg38 GISTIC refgene"
                if required:
                    raise OncoTracerError(reason)
                return _write_gistic_skip(output, reason, segmentation)

    status = output / "gistic2_status.tsv"
    command_file = output / "gistic2_command.txt"
    versions = output / "gistic2_versions.txt"
    seg = prepared_files["events"] if segmentation == "events" else prepared_files["full"]
    command: list[str | Path] = [
        "gistic2",
        "-b",
        gistic_out,
        "-seg",
        seg,
    ]
    if _bool(config, "gistic_use_markers"):
        command.extend(["-mk", prepared_files["markers"]])
    cnv = config.get("gistic_cnv_file")
    if cnv:
        command.extend(["-cnv", require_file(Path(str(cnv)), "GISTIC CNV exclusion file")])
    command.extend(
        [
            "-refgene", refgene,
            "-genegistic", "1",
            "-broad", "1" if _bool(config, "gistic_broad") else "0",
            "-brlen", _string(config, "gistic_brlen"),
            "-conf", _string(config, "gistic_conf"),
            "-qvt", _string(config, "gistic_qvt"),
            "-ta", _string(config, "gistic_ta"),
            "-td", _string(config, "gistic_td"),
            "-cap", _string(config, "gistic_cap"),
            "-rx", _string(config, "gistic_rx"),
            "-js", _string(config, "gistic_join_segment_size"),
            "-maxseg", _string(config, "gistic_maxseg"),
            "-scent", _string(config, "gistic_scent"),
            "-smallmem", _string(config, "gistic_smallmem"),
            "-savegene", _string(config, "gistic_savegene"),
            "-armpeel", _string(config, "gistic_armpeel"),
            "-smalldisk", _string(config, "gistic_smalldisk"),
            "-v", _string(config, "gistic_verbose"),
        ]
    )
    wrapped = toolchain.wrap("gistic", command)
    atomic_write_text(command_file, " ".join(__import__("shlex").quote(item) for item in wrapped) + "\n")
    version_probe = toolchain.wrap("gistic", ["gistic2", "-h"])
    version_result = runner.run(
        "classifier-gistic-version",
        version_probe,
        cwd=output,
        containment=gistic_environment,
        check=False,
    )
    atomic_write_text(versions, f"returncode={version_result.returncode}\n")

    signature = ledger.signature("classifier-gistic", wrapped, list(prepared_files.values()) + [refgene])
    sentinel = gistic_out / ".oncotracer-complete"
    if force or not ledger.reusable("classifier-gistic", signature, [status, sentinel]):
        result = runner.run(
            "classifier-gistic",
            wrapped,
            cwd=output,
            containment=gistic_environment,
            check=False,
        )
        if result.returncode == 0:
            atomic_write_text(
                status,
                "status\treason\tsegmentation\tcommand\texecutable\trefgene\n"
                f"completed\tNA\t{segmentation}\tgistic2_command.txt\tgistic2\t{refgene}\n",
            )
            atomic_write_text(sentinel, "completed\n")
            ledger.complete("classifier-gistic", signature, [status, sentinel])
        else:
            atomic_write_text(
                status,
                "status\treason\tsegmentation\tcommand\texecutable\trefgene\n"
                f"failed\texit_code_{result.returncode}\t{segmentation}\tgistic2_command.txt\tgistic2\t{refgene}\n",
            )
            atomic_write_text(gistic_out / "GISTIC_FAILED.txt", f"exit_code={result.returncode}\n")
            if required:
                raise OncoTracerError(f"GISTIC2 failed with exit code {result.returncode}")
    return gistic_out, require_file(status, "GISTIC status"), require_file(command_file, "GISTIC command")


def _update_summary(analysis_outdir: Path, classifier_out: Path) -> None:
    summary_dir = require_directory(analysis_outdir / "06_workflow_summary", "workflow summary")
    json_path = require_file(summary_dir / "workflow_summary.json", "workflow summary JSON")
    value = json.loads(json_path.read_text(encoding="utf-8"))
    value["cna_classifier"] = str(classifier_out)
    value["cna_classifier_completed"] = True
    value["completed_at"] = utc_now()
    atomic_write_workflow_summary(summary_dir, value)


def run_native_classifier(
    root: Path,
    config: Mapping[str, object],
    analysis_outdir: Path,
    lpwgs_root: Path,
    runner: CommandRunner,
    ledger: StageLedger,
    toolchain: ToolchainLike,
    *,
    force: bool,
) -> Path:
    """Run the complete optional v1.1 classifier/report graph natively."""
    package = require_directory(root / "bin" / "cna_classifier_nf", "CNA classifier payload")
    scripts = require_directory(package / "bin", "CNA classifier scripts")
    assets = require_directory(package / "assets", "CNA classifier assets")
    classifier_out = analysis_outdir / "05_cna_classifier"
    prepared = classifier_out / "01_prepared"
    classification = classifier_out / "02_classification"
    report = classifier_out / "03_report"
    gistic = classifier_out / "04_gistic2"
    parsed = classifier_out / "05_gistic2_parsed"
    knowledge = classifier_out / "06_knowledge"
    pathology_out = classifier_out / "07_pathology"
    for directory in (prepared, classification, report, gistic, parsed, knowledge, pathology_out):
        directory.mkdir(parents=True, exist_ok=True)
    classifier_environment = toolchain.environment("classifier")

    cna_input = require_directory(analysis_outdir / "03_cna_codification", "CNA classifier input")
    cna_events = require_file(cna_input / "cna_events.tsv", "CNA events")
    cna_notation = require_file(cna_input / "cna_cytogenomic_notation.tsv", "CNA notation")
    context = sample_set_key(config)
    region_default = assets / ("lymphoma_cna_regions.tsv" if context == "lymphoma" else "pancancer_cna_regions.tsv")
    region_catalog = require_file(Path(str(config.get("region_catalog") or region_default)), "CNA region catalog")
    chrom_sizes = require_file(Path(str(config.get("chrom_sizes") or assets / "hg38_chrom_sizes.tsv")), "hg38 chromosome sizes")

    prepare_command: list[str | Path] = [
        "python", scripts / "01_prepare_cna_inputs.py",
        "--cna-events", cna_events,
        "--cna-notation", cna_notation,
        "--region-catalog", region_catalog,
        "--chrom-sizes", chrom_sizes,
        "--gistic-window-bp", _string(config, "gistic_window_bp"),
        "--min-bins", _string(config, "min_bins"),
        "--min-size-mb", _string(config, "min_size_mb"),
        "--min-abs-log2", _string(config, "min_abs_log2"),
        "--focal-mb", _string(config, "focal_mb"),
        "--broad-mb", _string(config, "broad_mb"),
    ]
    selected = sample_filter(config)
    if selected:
        prepare_command.extend(["--samples", selected])
    if _bool(config, "include_sex"):
        prepare_command.append("--include-sex")
    prepare_wrapped = toolchain.wrap("classifier", prepare_command)
    prepare_outputs = [
        prepared / "clean_events.tsv",
        prepared / "sample_cna_summary.tsv",
        prepared / "event_matrix_binary.tsv",
        prepared / "event_matrix_weighted.tsv",
        prepared / "driver_region_matrix.tsv",
        prepared / "driver_region_hits.tsv",
        prepared / "recurrent_events.tsv",
        prepared / "gistic_full.seg",
        prepared / "gistic_events.seg",
        prepared / "gistic_markers.tsv",
        prepared / "prepare_metrics.json",
    ]
    _stage(
        "classifier-prepare", prepare_wrapped, [cna_events, cna_notation, region_catalog, chrom_sizes],
        prepare_outputs, cwd=prepared, runner=runner, ledger=ledger, force=force,
        containment=classifier_environment,
    )

    gistic_dir, gistic_status, gistic_command = _run_gistic(
        root, config, lpwgs_root, prepared, gistic, runner, ledger, toolchain, force=force
    )
    parse_command = toolchain.wrap(
        "classifier",
        [
            "python", scripts / "04_parse_gistic_results.py",
            "--gistic-dir", gistic_dir,
            "--gistic-status", gistic_status,
            "--gistic-command", gistic_command,
        ],
    )
    parsed_outputs = [
        parsed / "gistic_lesions_matrix.tsv",
        parsed / "gistic_lesions_long.tsv",
        parsed / "gistic_lesions_summary.tsv",
        parsed / "gistic_parse_metrics.json",
    ]
    _stage(
        "classifier-parse-gistic", parse_command, [gistic_status, gistic_command], parsed_outputs,
        cwd=parsed, runner=runner, ledger=ledger, force=force,
        containment=classifier_environment,
    )

    classify_command = toolchain.wrap(
        "classifier",
        [
            "python", scripts / "02_classify_cna.py",
            "--clean-events", prepared / "clean_events.tsv",
            "--sample-summary", prepared / "sample_cna_summary.tsv",
            "--event-matrix", prepared / "event_matrix_binary.tsv",
            "--weighted-event-matrix", prepared / "event_matrix_weighted.tsv",
            "--driver-matrix", prepared / "driver_region_matrix.tsv",
            "--recurrent-events", prepared / "recurrent_events.tsv",
            "--driver-hits", prepared / "driver_region_hits.tsv",
            "--gistic-matrix", parsed / "gistic_lesions_matrix.tsv",
            "--gistic-long", parsed / "gistic_lesions_long.tsv",
            "--gistic-summary", parsed / "gistic_lesions_summary.tsv",
            "--low-events", _string(config, "low_events"),
            "--high-events", _string(config, "high_events"),
            "--ultra-events", _string(config, "ultra_events"),
            "--high-chromosomes", _string(config, "high_chromosomes"),
            "--high-altered-mb", _string(config, "high_altered_mb"),
            "--ultra-altered-mb", _string(config, "ultra_altered_mb"),
            "--nmf-clusters", _string(config, "nmf_clusters"),
            "--top-regions", _string(config, "top_regions"),
        ],
    )
    classify_outputs = [
        classification / "cna_patient_classification.tsv",
        classification / "unsupervised_clusters.tsv",
        classification / "heatmap_matrix.tsv",
        classification / "pca_coordinates.tsv",
        classification / "classification_metrics.json",
    ]
    _stage(
        "classifier-classify", classify_command, prepare_outputs + parsed_outputs, classify_outputs,
        cwd=classification, runner=runner, ledger=ledger, force=force,
        containment=classifier_environment,
    )

    knowledge_command = toolchain.wrap(
        "classifier",
        [
            "python", scripts / "05_scrape_cna_knowledge.py",
            "--classification", classification / "cna_patient_classification.tsv",
            "--clean-events", prepared / "clean_events.tsv",
            "--driver-hits", prepared / "driver_region_hits.tsv",
            "--region-catalog", region_catalog,
            "--enable-web", str(_bool(config, "knowledge_web")).lower(),
            "--allow-fail", str(_bool(config, "knowledge_allow_fail")).lower(),
            "--cache-dir", knowledge / "knowledge_http_cache",
            "--max-papers", _string(config, "knowledge_max_papers"),
            "--timeout", _string(config, "knowledge_timeout"),
            "--sleep", _string(config, "knowledge_sleep"),
            "--lymphoma-terms", _string(config, "knowledge_lymphoma_terms"),
            "--cancer-terms", _string(config, "knowledge_cancer_terms"),
            "--cancer-type", context,
            "--user-agent", _string(config, "knowledge_user_agent"),
            "--enable-hf-ner", str(_bool(config, "knowledge_hf_ner")).lower(),
            "--hf-model", _string(config, "knowledge_hf_model"),
            "--enable-literature-llm", str(_bool(config, "knowledge_literature_llm")).lower(),
            "--literature-llm-models", _string(config, "knowledge_literature_llm_models"),
            "--literature-llm-local-files-only", str(_bool(config, "knowledge_literature_llm_local_files_only")).lower(),
            "--literature-llm-max-features", _string(config, "knowledge_literature_llm_max_features"),
            "--literature-llm-max-input-chars", _string(config, "knowledge_literature_llm_max_input_chars"),
            "--literature-llm-max-new-tokens", _string(config, "knowledge_literature_llm_max_new_tokens"),
            "--llm-threads", _string(config, "knowledge_llm_threads"),
            "--deep-literature", str(_bool(config, "knowledge_deep_literature")).lower(),
            "--deep-max-papers-per-feature", _string(config, "knowledge_deep_max_papers_per_feature"),
            "--deep-top-papers-per-sample", _string(config, "knowledge_deep_top_papers_per_sample"),
            "--deep-enable-llm-ranker", str(_bool(config, "knowledge_deep_enable_llm_ranker")).lower(),
            "--deep-llm-ranker-models", _string(config, "knowledge_deep_llm_ranker_models"),
            "--deep-llm-ranker-local-files-only", str(_bool(config, "knowledge_deep_llm_ranker_local_files_only")).lower(),
            "--deep-llm-ranker-max-candidates-per-sample", _string(config, "knowledge_deep_llm_ranker_max_candidates_per_sample"),
            "--literature-reference-llm-selection", str(_bool(config, "knowledge_literature_reference_llm_selection")).lower(),
            "--literature-top-references", _string(config, "knowledge_literature_top_references"),
        ],
    )
    knowledge_outputs = [
        knowledge / "knowledge_base.tsv",
        knowledge / "sample_knowledge.tsv",
        knowledge / "sample_knowledge_summary.tsv",
        knowledge / "knowledge_references.tsv",
        knowledge / "sample_literature.tsv",
        knowledge / "sample_literature_summary.tsv",
        knowledge / "knowledge_metrics.json",
        knowledge / "knowledge_llm_trials.tsv",
        knowledge / "knowledge_literature_ranker_trials.tsv",
    ]
    _stage(
        "classifier-knowledge", knowledge_command,
        [classification / "cna_patient_classification.tsv", prepared / "clean_events.tsv", prepared / "driver_region_hits.tsv", region_catalog, scripts / "05_scrape_cna_knowledge.py", scripts / "llm_runtime.py"],
        knowledge_outputs, cwd=knowledge, runner=runner, ledger=ledger, force=force,
        containment=classifier_environment,
    )

    pathology_value = config.get("pathology_csv") or config.get("pathology")
    pathology = require_file(Path(str(pathology_value)), "pathology table") if pathology_value else require_file(assets / "empty_pathology.tsv", "empty pathology asset")
    pathology_command: list[str | Path] = [
        "python", scripts / "07_pathology_concordance.py",
        "--pathology", pathology,
        "--classification", classification / "cna_patient_classification.tsv",
        "--clean-events", prepared / "clean_events.tsv",
        "--driver-hits", prepared / "driver_region_hits.tsv",
        "--sample-knowledge-summary", knowledge / "sample_knowledge_summary.tsv",
        "--sample-set", context,
        "--enable-biomed-models", str(_bool(config, "pathology_use_biomed_models")).lower(),
        "--biomed-models", _string(config, "pathology_biomed_models"),
        "--biomed-local-files-only", str(_bool(config, "pathology_biomed_local_files_only")).lower(),
        "--biomed-max-tokens", _string(config, "pathology_biomed_max_tokens"),
    ]
    optional_pathology = {
        "pathology_sample_col": "--pathology-sample-col",
        "pathology_case_col": "--pathology-case-col",
        "pathology_diagnosis_col": "--pathology-diagnosis-col",
        "score_calibration_table": "--score-calibration-table",
        "score_calibration_score_col": "--score-calibration-score-col",
        "score_calibration_label_col": "--score-calibration-label-col",
    }
    for key, flag in optional_pathology.items():
        value = config.get(key)
        if value:
            pathology_command.extend([flag, str(value)])
    pathology_wrapped = toolchain.wrap("classifier", pathology_command)
    pathology_outputs = [
        pathology_out / "pathology_concordance.tsv",
        pathology_out / "pathology_records_matched.tsv",
        pathology_out / "pathology_concordance_metrics.json",
        pathology_out / "pathology_status.txt",
        pathology_out / "pathology_model_trials.tsv",
    ]
    _stage(
        "classifier-pathology", pathology_wrapped,
        [pathology, classification / "cna_patient_classification.tsv", knowledge / "sample_knowledge_summary.tsv"],
        pathology_outputs, cwd=pathology_out, runner=runner, ledger=ledger, force=force,
        containment=classifier_environment,
    )

    plot_command = toolchain.wrap(
        "classifier",
        [
            "python", scripts / "03_plot_report.py",
            "--clean-events", prepared / "clean_events.tsv",
            "--sample-summary", prepared / "sample_cna_summary.tsv",
            "--event-matrix", prepared / "event_matrix_binary.tsv",
            "--driver-matrix", prepared / "driver_region_matrix.tsv",
            "--recurrent-events", prepared / "recurrent_events.tsv",
            "--driver-hits", prepared / "driver_region_hits.tsv",
            "--gistic-full-seg", prepared / "gistic_full.seg",
            "--gistic-markers", prepared / "gistic_markers.tsv",
            "--gistic-status", gistic_status,
            "--gistic-command", gistic_command,
            "--gistic-matrix", parsed / "gistic_lesions_matrix.tsv",
            "--gistic-long", parsed / "gistic_lesions_long.tsv",
            "--gistic-summary", parsed / "gistic_lesions_summary.tsv",
            "--classification", classification / "cna_patient_classification.tsv",
            "--unsupervised-clusters", classification / "unsupervised_clusters.tsv",
            "--heatmap-matrix", classification / "heatmap_matrix.tsv",
            "--pca-coordinates", classification / "pca_coordinates.tsv",
            "--plot-top-features", _string(config, "plot_top_features"),
            "--pathology-concordance", pathology_out / "pathology_concordance.tsv",
            "--pathology-records", pathology_out / "pathology_records_matched.tsv",
        ],
    )
    plot_outputs = [report / "cna_classifier_report.html", report / "figures" / "cna_event_burden.pdf"]
    _stage(
        "classifier-report", plot_command,
        prepare_outputs + classify_outputs + parsed_outputs + pathology_outputs,
        plot_outputs, cwd=report, runner=runner, ledger=ledger, force=force,
        containment=classifier_environment,
    )
    report_tables = report / "report_tables"
    report_tables.mkdir(parents=True, exist_ok=True)
    for source, name in [
        (pathology_out / "pathology_concordance.tsv", "pathology_concordance.tsv"),
        (pathology_out / "pathology_records_matched.tsv", "pathology_records_matched.tsv"),
        (pathology_out / "pathology_status.txt", "pathology_status.txt"),
        (pathology_out / "pathology_model_trials.tsv", "pathology_model_trials.tsv"),
    ]:
        shutil.copy2(source, report_tables / name)

    if _bool(config, "run_pdf_reports"):
        pdf_command = toolchain.wrap(
            "classifier",
            [
                "python", scripts / "06_pdf_knowledge_reports.py",
                "--classification", classification / "cna_patient_classification.tsv",
                "--sample-summary", prepared / "sample_cna_summary.tsv",
                "--clean-events", prepared / "clean_events.tsv",
                "--driver-hits", prepared / "driver_region_hits.tsv",
                "--driver-matrix", prepared / "driver_region_matrix.tsv",
                "--gistic-matrix", parsed / "gistic_lesions_matrix.tsv",
                "--gistic-long", parsed / "gistic_lesions_long.tsv",
                "--gistic-summary", parsed / "gistic_lesions_summary.tsv",
                "--sample-knowledge", knowledge / "sample_knowledge.tsv",
                "--sample-knowledge-summary", knowledge / "sample_knowledge_summary.tsv",
                "--knowledge-references", knowledge / "knowledge_references.tsv",
                "--sample-literature", knowledge / "sample_literature.tsv",
                "--sample-literature-summary", knowledge / "sample_literature_summary.tsv",
                "--figures", report / "figures",
                "--pathology-concordance", pathology_out / "pathology_concordance.tsv",
                "--pathology-records", pathology_out / "pathology_records_matched.tsv",
                "--outdir", report / "pdf_reports",
                "--max-events", _string(config, "pdf_max_events"),
                "--include-full-events", str(_bool(config, "pdf_include_full_events")).lower(),
            ],
        )
        _stage(
            "classifier-pdf-reports", pdf_command,
            classify_outputs + knowledge_outputs + pathology_outputs,
            [report / "pdf_reports" / "pdf_report_index.tsv"],
            cwd=report, runner=runner, ledger=ledger, force=force,
            containment=classifier_environment,
        )

    if _bool(config, "run_clinician_reports"):
        clinician_command = toolchain.wrap(
            "classifier",
            [
                "python", scripts / "08_clinician_driver_reports.py",
                "--classification", classification / "cna_patient_classification.tsv",
                "--sample-summary", prepared / "sample_cna_summary.tsv",
                "--driver-hits", prepared / "driver_region_hits.tsv",
                "--sample-knowledge", knowledge / "sample_knowledge.tsv",
                "--sample-knowledge-summary", knowledge / "sample_knowledge_summary.tsv",
                "--sample-literature", knowledge / "sample_literature.tsv",
                "--pathology-concordance", pathology_out / "pathology_concordance.tsv",
                "--pathology-records", pathology_out / "pathology_records_matched.tsv",
                "--outdir", report / "clinician_reports",
                "--max-drivers", _string(config, "clinician_max_drivers"),
            ],
        )
        _stage(
            "classifier-clinician-reports", clinician_command,
            classify_outputs + knowledge_outputs + pathology_outputs,
            [report / "clinician_reports" / "clinician_report_index.tsv"],
            cwd=report, runner=runner, ledger=ledger, force=force,
            containment=classifier_environment,
        )

    summary = {
        "schema": "oncotracer-native-classifier-v1",
        "engine": "native",
        "nextflow_used": False,
        "sample_set": context,
        "classifier_outdir": str(classifier_out),
        "gistic_status": gistic_status.read_text(encoding="utf-8", errors="replace").splitlines()[-1].split("\t", 1)[0],
        "completed_at": utc_now(),
    }
    atomic_write_json(classifier_out / "native_classifier_summary.json", summary)
    _update_summary(analysis_outdir, classifier_out)
    return classifier_out
