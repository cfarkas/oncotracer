#!/usr/bin/env python3
"""Generate matched per-sample HTML and PDF CNA knowledge reports.

This is a reporting-only extension.  It does not change CNA calls,
classification rules, GISTIC parsing, or knowledge-enrichment logic.

The same underlying section data are used for each sample's HTML and PDF report:
interpretation summary, classification/burden metrics, SAMURAI summary,
state counts, driver-region calls, CNA event table, methods,
limitations, and references.
"""

from __future__ import annotations

import argparse
import html
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.platypus import (
    CondPageBreak,
    Image,
    KeepTogether,
    LongTable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import HRFlowable

try:
    from pypdf import PdfReader, PdfWriter
except Exception:  # pragma: no cover
    PdfReader = None
    PdfWriter = None

PAGE_WIDTH, PAGE_HEIGHT = A4
LEFT_MARGIN = 1.05 * cm
RIGHT_MARGIN = 1.05 * cm
TOP_MARGIN = 2.05 * cm
BOTTOM_MARGIN = 1.55 * cm
USABLE_WIDTH = PAGE_WIDTH - LEFT_MARGIN - RIGHT_MARGIN

INK = colors.HexColor("#172033")
MUTED = colors.HexColor("#5f6b7a")
LINE = colors.HexColor("#d7dde6")
PALE = colors.HexColor("#f5f7fb")
PALE2 = colors.HexColor("#eef2f7")
NAVY = colors.HexColor("#172033")
ACCENT = colors.HexColor("#2f6f9f")
WARN_BG = colors.HexColor("#fff5cc")
WARN_LINE = colors.HexColor("#d6a300")

STATE_COLORS = {
    "loss": colors.HexColor("#67A9CF"),
    "deep_loss": colors.HexColor("#2166AC"),
    "deep loss": colors.HexColor("#2166AC"),
    "gain": colors.HexColor("#F4A582"),
    "amplification": colors.HexColor("#B2182B"),
    "amp": colors.HexColor("#B2182B"),
}

BURDEN_COLORS = {
    "CNA-flat_or_no_high-confidence_CNA": colors.HexColor("#BDBDBD"),
    "CNA-flat": colors.HexColor("#BDBDBD"),
    "CNA-low": colors.HexColor("#80B1D3"),
    "CNA-intermediate": colors.HexColor("#FDB462"),
    "CNA-high_complex": colors.HexColor("#FB8072"),
    "CNA-ultracomplex": colors.HexColor("#B2182B"),
}

AGREEMENT_HEX = {
    "AGREEMENT": "#1B7837",
    "AGREEMENT_NON_LYMPHOMA": "#1B7837",
    "PARTIAL_AGREEMENT": "#B36B00",
    "PARTIAL_AGREEMENT_NON_LYMPHOMA": "#B36B00",
    "NOT_ASSESSABLE": "#6B7280",
    "NO_MATCH": "#6B7280",
    "PATHOLOGY_NOT_PROVIDED": "#BDBDBD",
    "DISAGREEMENT_REVIEW": "#B2182B",
}

MISSING_STRINGS = {"", "none", "none_detected", "none_detected_or_not_run", "nan", "na", "n/a", "null"}


def read_tsv(path: str | Path, index_col=None) -> pd.DataFrame:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(p, sep="\t", index_col=index_col)
    except Exception:
        return pd.DataFrame()


def safe_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    return str(value)


def slugify(value: Any) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", safe_str(value)).strip("_")
    return s or "sample"


def fmt(value: Any, digits: int = 2) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass
    try:
        f = float(value)
        if abs(f - round(f)) < 1e-9:
            return str(int(round(f)))
        return f"{f:.{digits}f}"
    except Exception:
        return safe_str(value)


def split_field(value: Any) -> list[str]:
    s = safe_str(value).strip()
    if s.lower() in MISSING_STRINGS:
        return []
    return [p.strip() for p in re.split(r"[;,]", s) if p.strip() and p.strip().lower() not in MISSING_STRINGS]


def normalize_sample_columns(*dfs: pd.DataFrame) -> None:
    for df in dfs:
        if not df.empty and "sample" in df.columns:
            df["sample"] = df["sample"].astype(str)


def esc(value: Any) -> str:
    return html.escape(safe_str(value), quote=False).replace("\n", "<br/>")


def esc_attr(value: Any) -> str:
    return html.escape(safe_str(value), quote=True)


def make_styles() -> dict[str, ParagraphStyle]:
    styles = getSampleStyleSheet()
    base = styles["BodyText"]
    out: dict[str, ParagraphStyle] = {}
    out["body"] = ParagraphStyle(
        "body", parent=base, fontName="Helvetica", fontSize=8.7, leading=11.4,
        spaceAfter=4, textColor=INK
    )
    out["body_indent"] = ParagraphStyle(
        "body_indent", parent=out["body"], leftIndent=10, firstLineIndent=-6
    )
    out["small"] = ParagraphStyle(
        "small", parent=base, fontName="Helvetica", fontSize=7.4, leading=9.4,
        spaceAfter=3, textColor=colors.HexColor("#354052")
    )
    out["tiny"] = ParagraphStyle(
        "tiny", parent=base, fontName="Helvetica", fontSize=6.35, leading=7.85,
        spaceAfter=1.5, textColor=colors.HexColor("#354052")
    )
    out["title"] = ParagraphStyle(
        "title", parent=base, fontName="Helvetica-Bold", fontSize=18.5, leading=22,
        spaceAfter=3, textColor=INK
    )
    out["subtitle"] = ParagraphStyle(
        "subtitle", parent=base, fontName="Helvetica", fontSize=9.0, leading=11.4,
        spaceAfter=3, textColor=MUTED
    )
    out["h1"] = ParagraphStyle(
        "h1", parent=base, fontName="Helvetica-Bold", fontSize=11.4, leading=13.8,
        spaceBefore=0, spaceAfter=0, textColor=colors.white
    )
    out["h2"] = ParagraphStyle(
        "h2", parent=base, fontName="Helvetica-Bold", fontSize=9.3, leading=11.2,
        spaceBefore=3, spaceAfter=3, textColor=INK
    )
    out["card_title"] = ParagraphStyle(
        "card_title", parent=base, fontName="Helvetica-Bold", fontSize=9.6, leading=11.6,
        spaceAfter=2, textColor=INK
    )
    out["warn"] = ParagraphStyle(
        "warn", parent=base, fontName="Helvetica", fontSize=8.25, leading=10.8,
        leftIndent=0, rightIndent=0, spaceBefore=3, spaceAfter=5,
        textColor=INK
    )
    out["metric_label"] = ParagraphStyle(
        "metric_label", parent=base, fontName="Helvetica-Bold", fontSize=6.7, leading=8.3,
        textColor=MUTED, alignment=TA_CENTER
    )
    out["metric_value"] = ParagraphStyle(
        "metric_value", parent=base, fontName="Helvetica-Bold", fontSize=10.4, leading=12.5,
        textColor=INK, alignment=TA_CENTER
    )
    return out


STYLES = make_styles()


def para(text: Any, style: str = "body") -> Paragraph:
    return Paragraph(esc(text), STYLES[style])


def raw_para(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, STYLES[style])


def html_table(df: pd.DataFrame, columns: list[str] | None = None, max_rows: int | None = None, css_class: str = "table") -> str:
    if df is None or df.empty:
        return "<p class='muted'>No rows.</p>"
    d = df.copy()
    if columns is not None:
        d = d[[c for c in columns if c in d.columns]]
    if max_rows is not None:
        d = d.head(max_rows)
    if d.empty:
        return "<p class='muted'>No rows.</p>"
    return d.to_html(index=False, escape=True, border=0, classes=css_class)


def dataframe_from_pairs(pairs: list[tuple[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame([{"field": k, "value": safe_str(v)} for k, v in pairs])


def kv_table(pairs: list[tuple[str, Any]], col_widths: list[float] | None = None) -> Table:
    data = [[Paragraph(f"<b>{esc(k)}</b>", STYLES["small"]), Paragraph(esc(v), STYLES["small"])] for k, v in pairs]
    if not data:
        data = [[Paragraph("No data", STYLES["small"]), Paragraph("", STYLES["small"])] ]
    tbl = Table(data, colWidths=col_widths or [4.2*cm, USABLE_WIDTH - 4.2*cm], repeatRows=0, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (0, -1), PALE),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return tbl


def weighted_col_widths(cols: list[str], usable_width: float = USABLE_WIDTH) -> list[float]:
    weights: list[float] = []
    for c in cols:
        name = c.lower()
        if any(k in name for k in ["interpretation", "rationale", "hint", "caveat", "title", "feature_label", "display", "genes", "flags", "molecular", "shorthand"]):
            weights.append(2.2)
        elif any(k in name for k in ["source", "url", "abstract", "query"]):
            weights.append(1.8)
        elif any(k in name for k in ["start", "end", "cytoband", "copy"]):
            weights.append(1.2)
        else:
            weights.append(1.0)
    total = sum(weights) or 1.0
    return [usable_width * w / total for w in weights]


def dataframe_table(
    df: pd.DataFrame,
    columns: list[str] | None = None,
    max_rows: int | None = None,
    style: str = "tiny",
    max_char: int = 140,
    repeat_rows: int = 1,
    usable_width: float = USABLE_WIDTH,
) -> LongTable | Paragraph:
    if df is None or df.empty:
        return para("No rows.", "small")
    d = df.copy()
    if columns is not None:
        d = d[[c for c in columns if c in d.columns]]
    if max_rows is not None:
        d = d.head(max_rows)
    if d.empty:
        return para("No rows.", "small")
    headers = [Paragraph(f"<b>{esc(c)}</b>", STYLES[style]) for c in d.columns]
    data = [headers]
    for _, row in d.iterrows():
        cells = []
        for c in d.columns:
            val = row[c]
            s = fmt(val, 3) if isinstance(val, (int, float)) else safe_str(val)
            if max_char and len(s) > max_char:
                s = s[:max_char - 1] + "..."
            cells.append(Paragraph(esc(s), STYLES[style]))
        data.append(cells)
    col_widths = weighted_col_widths(list(d.columns), usable_width=usable_width)
    tbl = LongTable(data, colWidths=col_widths, repeatRows=repeat_rows, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.20, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), PALE2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfcfe")]),
    ]))
    return tbl


def section_header(title: str) -> Table:
    tbl = Table(
        [[Paragraph(esc(title), STYLES["h1"])]],
        colWidths=[USABLE_WIDTH],
        hAlign="LEFT",
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("BOX", (0, 0), (-1, -1), 0.0, NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def add_section(story: list[Any], title: str, min_space: float = 4.2*cm) -> None:
    story.append(CondPageBreak(min_space))
    story.append(Spacer(1, 5))
    story.append(section_header(title))
    story.append(Spacer(1, 7))


def warning_box(text: str) -> Table:
    tbl = Table([[raw_para(f"<b>Important limitation:</b> {esc(text)}", "warn")]], colWidths=[USABLE_WIDTH], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), WARN_BG),
        ("BOX", (0, 0), (-1, -1), 0.7, WARN_LINE),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def agreement_hex(call: Any) -> str:
    return AGREEMENT_HEX.get(safe_str(call), "#6B7280")


def agreement_color(call: Any) -> colors.Color:
    return colors.HexColor(agreement_hex(call))


def agreement_label(call: Any) -> str:
    labels = {
        "AGREEMENT": "Agreement: CNA supports reported pathology",
        "PARTIAL_AGREEMENT": "Partial agreement: broadly compatible, not subtype-definitive",
        "AGREEMENT_NON_LYMPHOMA": "Agreement with reported non-lymphoid / non-lymphoma pathology",
        "PARTIAL_AGREEMENT_NON_LYMPHOMA": "Partial agreement with non-lymphoid / non-lymphoma pathology",
        "DISAGREEMENT_REVIEW": "Potential discordance: manual review recommended",
        "NOT_ASSESSABLE": "Not assessable from CNA-only data",
        "NO_MATCH": "No matching pathology row found",
        "PATHOLOGY_NOT_PROVIDED": "Pathology comparison not run",
    }
    return labels.get(safe_str(call), safe_str(call) or "Not assessable")


def pathology_score_explanation(pathology_row: pd.Series | None) -> str:
    """Explain the local token/biomedical-model pathology agreement score with numbers."""
    if pathology_row is None or pathology_row.empty:
        return ""
    call = safe_str(pathology_row.get("agreement_call"))
    score = fmt(pathology_row.get("agreement_score", ""), 0)
    if call in {"", "PATHOLOGY_NOT_PROVIDED"}:
        return ""
    if call == "NO_MATCH":
        return "Score 0 means the pathology table was supplied but no row matched this CNA sample; no pathology/CNA agreement score was calculated."
    breakdown = safe_str(pathology_row.get("agreement_score_breakdown", ""))
    model = safe_str(pathology_row.get("agreement_score_model", "OncoTracer local token agreement model"))
    overlap = safe_str(pathology_row.get("agreement_token_overlap", ""))
    token_only = safe_str(pathology_row.get("agreement_score_token_only", ""))
    final_source = safe_str(pathology_row.get("agreement_score_final_source", ""))
    biomed = safe_str(pathology_row.get("agreement_biomed_consensus_score", ""))
    probability = safe_str(pathology_row.get("agreement_probability_estimate", ""))
    calibration = safe_str(pathology_row.get("agreement_probability_calibration_status", ""))
    rationale = safe_str(pathology_row.get("agreement_rationale", pathology_row.get("agreement_summary", "")))
    return (
        f"Model: {model}. Final score = {score}/100. "
        f"Token-only score = {token_only or 'not available'}. Biomedical transformer consensus = {biomed or 'not available'}. Final source = {final_source or 'token_only'}. "
        f"Numeric breakdown: {breakdown}. Token overlap used: {overlap or 'none'}. "
        f"Probability estimate = {probability or 'not calculated'}; calibration status = {calibration or 'not available'}. "
        f"A probability is calibrated only when --score_calibration_table supplies labelled reference outcomes; otherwise it is a sigmoid-derived probability-like estimate. "
        f"Reason: {rationale}"
    )


def low_pass_wgs_capabilities_text() -> str:
    return (
        "Low-pass WGS, when analyzed with read-depth CNA methods such as SAMURAI/QDNAseq, can survey the whole genome for broad and focal copy-number gains, losses, deep losses, and high-level amplifications; estimate CNA burden, altered genome size, chromosomal/arm-level complexity and aneuploidy; flag recurrent context-specific CNA regions such as lymphoma-associated 2p16/REL-BCL11A, 9p24/JAK2-PD-L1/PD-L2, 9p21/CDKN2A-B, 17p13/TP53, 8q24/MYC, 18q21/BCL2-MALT1, or other cancer-context catalog regions when these regions pass threshold; and support cohort-level recurrence analysis with GISTIC2 when enough samples are available. It is best used as a genome-wide CNA screening and stratification assay, not as a complete molecular diagnostic test."
    )


def low_pass_wgs_limitations_text() -> str:
    return (
        "Low-pass WGS CNA analysis does not reliably detect SNVs, indels, balanced translocations, gene fusions, methylation class, gene/protein expression, clonality, copy-neutral loss of heterozygosity, or biallelic inactivation without orthogonal evidence. It also depends on tumor purity, sequencing depth, binning, segmentation, and threshold settings."
    )


def pathology_score_method_text() -> str:
    return (
        "When --pathology is supplied, the agreement score is calculated only in that pathology-enabled branch. The baseline model is a local token agreement model: it extracts tokens from pathology diagnosis/IHC/site text and from CNA-derived features, then sums auditable numeric components: base matched-pathology evidence, pathology-CNA token overlap, diagnosis-specific CNA biomarker support, CNA-burden context, IHC-token support, and penalties for discordant patterns. "
        "If --pathology_use_biomed_models true, three optional biomedical transformer language models are attempted on the pathology text versus CNA-evidence text. When at least one model succeeds, the final agreement score is 0.70 × token-only score + 0.30 × mean biomedical semantic score. If the models are unavailable or fail, the report keeps the token-only score and records the model status. "
        "The score is an explainability/compatibility score, not a final diagnosis. A probability is truly calibrated only if --score_calibration_table is supplied with labelled reference outcomes; otherwise the probability shown is an uncalibrated sigmoid-derived probability-like estimate."
    )


def probable_cna_score_method_text() -> str:
    return (
        "The probable CNA-based classification is calculated for every sample, even without pathology, from CNA tokens alone. The score uses base informative-CNA evidence, CNA-burden context, canonical driver-region tokens, pattern-specificity support, a small PubMed/Europe-PMC influential-literature support component when context-relevant selected papers are found for the detected CNA drivers, and penalties for flat/ambiguous or discordant patterns. This is a molecular CNA-pattern suggestion, not an integrated tumor diagnosis."
    )


def evidence_tier_method_text() -> str:
    return (
        "Evidence-tier labels describe how a CNA region is used in this research report. driver-CNA means a canonical/recurrent copy-number region in the built-in context-specific CNA catalog that can support a biologically meaningful CNA pattern. supportive-CNA means compatible but weaker copy-number evidence that supports context rather than a subtype call. CNA-context means broad information such as CNA burden, aneuploidy, arm-level change, chromosomal instability, or complexity. driver-CNA/high-risk-context means the CNA touches a high-risk axis such as TP53-region loss, but does not prove mutation or biallelic inactivation. driver-CNA/actionability-context means the locus can be clinically or biologically relevant in some diseases, but requires clinical-grade confirmation and the correct tumor context. context-dependent means the same CNA has different meanings depending on the selected --sample_set and the pathology context. These evidence tiers are research-reporting tiers, not AMP/ASCO/CAP clinical actionability tiers."
    )


def show_pathology_assessment(pathology_row: pd.Series | None) -> bool:
    if pathology_row is None or pathology_row.empty:
        return False
    call = safe_str(pathology_row.get("agreement_call"))
    return call not in {"", "PATHOLOGY_NOT_PROVIDED"}


def pathology_agreement_pdf_blocks(pathology_row: pd.Series | None) -> list[Any]:
    if not show_pathology_assessment(pathology_row):
        return []
    call = safe_str(pathology_row.get("agreement_call"))
    score = fmt(pathology_row.get("agreement_score", ""), 0)
    label = agreement_label(call)
    c = agreement_color(call)
    text_color = colors.white if call in {"AGREEMENT", "AGREEMENT_NON_LYMPHOMA", "DISAGREEMENT_REVIEW"} else INK
    banner = Table(
        [[Paragraph(f"<b>{esc(label)}</b>", STYLES["h2"]), Paragraph(f"Score: <b>{esc(score)}</b>", STYLES["h2"])]],
        colWidths=[USABLE_WIDTH - 3.0*cm, 3.0*cm],
        hAlign="LEFT",
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), c),
        ("TEXTCOLOR", (0, 0), (-1, -1), text_color),
        ("BOX", (0, 0), (-1, -1), 0.0, c),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    pairs = [
        ("Reported pathology diagnosis", pathology_row.get("pathology_final_diagnosis", "")),
        ("Pathology category", "; ".join([x for x in [safe_str(pathology_row.get("pathology_diagnosis_category_1", "")), safe_str(pathology_row.get("pathology_diagnosis_category_2", ""))] if x])),
        ("Inferred pathology lineage/subtype", f"{safe_str(pathology_row.get('pathology_lineage',''))} / {safe_str(pathology_row.get('pathology_subtype_inferred',''))}"),
        ("CNA knowledge pattern", pathology_row.get("cna_knowledge_pattern", "")),
        ("CNA rule-based class", pathology_row.get("cna_rule_based_class", "")),
        ("Why this call was made", pathology_row.get("agreement_summary", "")),
        ("Numeric score breakdown", pathology_row.get("agreement_score_breakdown", "")),
        ("Token-only score", pathology_row.get("agreement_score_token_only", "")),
        ("Final score source", pathology_row.get("agreement_score_final_source", "")),
        ("Biomedical model consensus score", pathology_row.get("agreement_biomed_consensus_score", "")),
        ("Biomedical model trial scores", pathology_row.get("agreement_biomed_model_scores", "")),
        ("Biomedical model status", pathology_row.get("agreement_biomed_model_status", "")),
        ("Probability estimate", pathology_row.get("agreement_probability_estimate", "")),
        ("Probability calibration status", pathology_row.get("agreement_probability_calibration_status", "")),
        ("Probability method", pathology_row.get("agreement_probability_method", "")),
        ("Token overlap", pathology_row.get("agreement_token_overlap", "")),
        ("Pathology tokens", pathology_row.get("agreement_pathology_tokens", "")),
        ("CNA tokens", pathology_row.get("agreement_cna_tokens", "")),
        ("Model", pathology_row.get("agreement_score_model", "")),
        ("How the score was assessed", pathology_score_explanation(pathology_row)),
        ("Explainability / rationale", pathology_row.get("agreement_rationale", "")),
        ("Supporting evidence", pathology_row.get("supporting_evidence", "")),
        ("Cautionary evidence", pathology_row.get("cautionary_evidence", "")),
        ("IHC/pathology highlights", pathology_row.get("pathology_ihc_highlights", "")),
        ("Anatomic site", "; ".join([x for x in [safe_str(pathology_row.get("pathology_specimen_organ", "")), safe_str(pathology_row.get("pathology_anatomical_site", ""))] if x])),
    ]
    caveat = "This is a compatibility assessment between CNA-derived patterns and the provided pathology text. It does not override the pathologist diagnosis and cannot resolve morphology, immunophenotype, SNVs/indels, balanced translocations, expression, methylation, or clinical context."
    return [
        section_header("PATHOLOGY AGREEMENT ASSESSMENT"),
        Spacer(1, 7),
        banner,
        Spacer(1, 6),
        kv_table(pairs, col_widths=[4.3*cm, USABLE_WIDTH-4.3*cm]),
        Spacer(1, 5),
        warning_box(caveat),
        Spacer(1, 7),
    ]


def show_probable_cna_assessment(pathology_row: pd.Series | None) -> bool:
    if pathology_row is None or pathology_row.empty:
        return False
    return bool(safe_str(pathology_row.get("probable_cna_classification", "")))


def probable_cna_pdf_blocks(pathology_row: pd.Series | None) -> list[Any]:
    if not show_probable_cna_assessment(pathology_row):
        return []
    score = fmt(pathology_row.get("probable_cna_score", ""), 0)
    label = safe_str(pathology_row.get("probable_cna_classification", ""))
    banner = Table(
        [[Paragraph(f"<b>{esc(label)}</b>", STYLES["h2"]), Paragraph(f"Score: <b>{esc(score)}</b>", STYLES["h2"])]],
        colWidths=[USABLE_WIDTH - 3.0*cm, 3.0*cm],
        hAlign="LEFT",
    )
    banner.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#2f6f9f")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.0, colors.HexColor("#2f6f9f")),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    pairs = [
        ("Probable CNA-based classification", pathology_row.get("probable_cna_classification", "")),
        ("Probable CNA score", pathology_row.get("probable_cna_score", "")),
        ("Why this classification was assigned", pathology_row.get("probable_cna_rationale", "")),
        ("Numeric score breakdown", pathology_row.get("probable_cna_score_breakdown", "")),
        ("Probability estimate", pathology_row.get("probable_cna_probability_estimate", "")),
        ("Probability calibration status", pathology_row.get("probable_cna_probability_calibration_status", "")),
        ("Probability method", pathology_row.get("probable_cna_probability_method", "")),
        ("CNA tokens used", pathology_row.get("probable_cna_tokens", "")),
        ("Model", pathology_row.get("probable_cna_model", "local token CNA-pattern score")),
    ]
    caveat = "This is a CNA-only molecular-pattern suggestion calculated even without pathology. The probability is calibrated only when a labelled calibration table is supplied; otherwise it is an uncalibrated probability-like estimate. It is not a final pathology diagnosis and must be integrated with morphology, IHC, SNVs/indels, fusions/translocations, methylation/expression, and clinical context."
    return [
        section_header("PROBABLE CNA-BASED CLASSIFICATION"),
        Spacer(1, 7),
        banner,
        Spacer(1, 6),
        kv_table(pairs, col_widths=[4.3*cm, USABLE_WIDTH-4.3*cm]),
        Spacer(1, 5),
        warning_box(caveat),
        Spacer(1, 7),
    ]


def html_probable_cna_assessment(data: dict[str, Any]) -> str:
    pr = data.get("pathology_row", pd.Series(dtype=object))
    if not show_probable_cna_assessment(pr):
        return ""
    score = fmt(pr.get("probable_cna_score", ""), 0)
    label = safe_str(pr.get("probable_cna_classification", ""))
    pairs = [
        ("Probable CNA-based classification", pr.get("probable_cna_classification", "")),
        ("Probable CNA score", pr.get("probable_cna_score", "")),
        ("Why this classification was assigned", pr.get("probable_cna_rationale", "")),
        ("Numeric score breakdown", pr.get("probable_cna_score_breakdown", "")),
        ("Probability estimate", pr.get("probable_cna_probability_estimate", "")),
        ("Probability calibration status", pr.get("probable_cna_probability_calibration_status", "")),
        ("Probability method", pr.get("probable_cna_probability_method", "")),
        ("CNA tokens used", pr.get("probable_cna_tokens", "")),
        ("Model", pr.get("probable_cna_model", "local token CNA-pattern score")),
    ]
    caveat = "This is a CNA-only molecular-pattern suggestion calculated even without pathology. The probability is calibrated only with a user-supplied labelled calibration table; otherwise it is uncalibrated. It is not a final pathology diagnosis."
    return f"""
    <section class='pathology-section'>
      <h2>PROBABLE CNA-BASED CLASSIFICATION</h2>
      <div class='agreement-banner' style='background:#2f6f9f'>
        <div><strong>{html.escape(label)}</strong></div><div>Score: <strong>{html.escape(score)}</strong></div>
      </div>
      {html_kv_table(pairs)}
      <div class='warning'><strong>Interpretation caveat:</strong> {html.escape(caveat)}</div>
    </section>
    """


def state_counts(events: pd.DataFrame) -> pd.DataFrame:
    if events.empty or "state" not in events.columns:
        return pd.DataFrame(columns=["state", "n_events", "altered_mb"])
    d = events.copy()
    d["size_mb"] = pd.to_numeric(d.get("size_mb", 0), errors="coerce").fillna(0)
    out = d.groupby("state", dropna=False).agg(n_events=("state", "size"), altered_mb=("size_mb", "sum")).reset_index()
    return out.sort_values("n_events", ascending=False)


def state_counts_table(events: pd.DataFrame) -> LongTable | Paragraph:
    return dataframe_table(state_counts(events), columns=["state", "n_events", "altered_mb"], style="small", max_char=80)


def burden_color(row: pd.Series) -> colors.Color:
    return BURDEN_COLORS.get(safe_str(row.get("cna_burden_class", "")), colors.HexColor("#999999"))


def title_banner(row: pd.Series) -> Table:
    sample = safe_str(row.get("sample"))
    burden = safe_str(row.get("cna_burden_class", "unknown"))
    rule = safe_str(row.get("rule_based_cna_class", "unknown"))
    bcolor = burden_color(row)
    text_color = colors.white if burden == "CNA-ultracomplex" else INK
    data = [[
        Paragraph("<b>CNA burden</b>", STYLES["small"]),
        Paragraph(esc(burden.replace("_", " ")), STYLES["small"]),
        Paragraph("<b>Rule-based class</b>", STYLES["small"]),
        Paragraph(esc(rule), STYLES["small"]),
    ]]
    tbl = Table(data, colWidths=[2.55*cm, 5.15*cm, 3.0*cm, USABLE_WIDTH-10.7*cm], hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, LINE),
        ("BACKGROUND", (1, 0), (1, 0), bcolor),
        ("TEXTCOLOR", (1, 0), (1, 0), text_color),
        ("BACKGROUND", (0, 0), (0, 0), PALE),
        ("BACKGROUND", (2, 0), (2, 0), PALE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return tbl


def metric_card_table(row: pd.Series) -> Table:
    metrics = [
        ("CNA events", fmt(row.get("n_cna_events", 0), 0)),
        ("Altered Mb", fmt(row.get("altered_mb", 0), 1)),
        ("Gain / loss Mb", f"{fmt(row.get('gain_mb', 0), 1)} / {fmt(row.get('loss_mb', 0), 1)}"),
        ("Chr / arms", f"{fmt(row.get('n_chromosomes_affected', 0), 0)} / {fmt(row.get('n_arms_affected', 0), 0)}"),
    ]
    data = [[Paragraph(f"<b>{esc(v)}</b><br/><font color='#5f6b7a'>{esc(k)}</font>", STYLES["metric_value"]) for k, v in metrics]]
    w = USABLE_WIDTH / len(metrics)
    tbl = Table(data, colWidths=[w]*len(metrics), hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, LINE),
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#fafbfe")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    return tbl


def interpretation_paragraphs(row: pd.Series, ksummary: pd.Series | None, sk: pd.DataFrame) -> list[Any]:
    story: list[Any] = []
    sample = safe_str(row.get("sample"))
    refined = safe_str(ksummary.get("knowledge_refined_class")) if ksummary is not None and not ksummary.empty else ""
    rationale = safe_str(ksummary.get("knowledge_refined_class_rationale")) if ksummary is not None and not ksummary.empty else ""
    n_events = fmt(row.get("n_cna_events", 0), 0)
    altered = fmt(row.get("altered_mb", 0), 1)
    n_chr = fmt(row.get("n_chromosomes_affected", 0), 0)
    n_arms = fmt(row.get("n_arms_affected", 0), 0)
    flags = split_field(row.get("driver_region_flags", ""))
    story.append(para(f"Sample {sample} shows {n_events} high-confidence CNA events, covering approximately {altered} Mb and affecting {n_chr} chromosomes / {n_arms} chromosome arms under the current thresholds."))
    if refined:
        story.append(para(f"Knowledge-refined CNA pattern: {refined}. {rationale}"))
    if ksummary is not None and not ksummary.empty:
        lit_syn = safe_str(ksummary.get("knowledge_literature_synthesis", ""))
        lit_stat = safe_str(ksummary.get("knowledge_literature_llm_status", ""))
        if lit_syn:
            story.append(para("Literature/LLM-supported interpretation: " + lit_syn))
        if lit_stat:
            story.append(para("Literature model trace: " + lit_stat, "small"))
    if flags:
        story.append(para("Canonical CNA flags detected: " + ", ".join(flags) + "."))
    else:
        story.append(para("No canonical driver-region CNA flag was detected by the current CNA region catalog."))
    if not sk.empty:
        top = sk[sk["feature_id"].astype(str) != "none_detected"].head(5) if "feature_id" in sk.columns else sk.head(5)
        if not top.empty and "display" in top.columns:
            features = "; ".join([safe_str(x) for x in top["display"].tolist() if safe_str(x)])
            if features:
                story.append(para("Highest-priority CNA knowledge features for review: " + features + "."))
    story.append(warning_box("this is CNA-only interpretation. It must be integrated with histology, immunophenotype, SNVs/indels, structural variants/translocations, expression, methylation when available, and clinical context before tumor diagnosis or subtype assignment."))
    return story


def knowledge_feature_blocks(sk: pd.DataFrame) -> list[Any]:
    blocks: list[Any] = []
    if sk.empty:
        return [para("No knowledge-enrichment rows were produced for this sample.", "small")]
    for _, r in sk.iterrows():
        fid = safe_str(r.get("feature_id"))
        if fid == "none_detected":
            tbl = Table([
                [Paragraph("No canonical driver CNA detected", STYLES["card_title"])],
                [para(r.get("biological_interpretation", "No canonical CNA feature detected."), "body")],
                [raw_para("<b>Literature synthesis.</b> " + esc(r.get("literature_synthesis", "")), "small")],
                [para(r.get("caveat", ""), "small")],
            ], colWidths=[USABLE_WIDTH], hAlign="LEFT")
            tbl.setStyle(TableStyle([
                ("BOX", (0, 0), (-1, -1), 0.35, LINE),
                ("BACKGROUND", (0, 0), (0, 0), PALE2),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            blocks.append(tbl)
            blocks.append(Spacer(1, 7))
            continue
        title = safe_str(r.get("display")) or fid
        rows = [
            [Paragraph(esc(title), STYLES["card_title"])],
            [kv_table([
                ("Feature ID", fid),
                ("Genes / region", r.get("genes", "")),
                ("Observed state", r.get("event_state", "")),
                ("Cytoband", r.get("event_cytoband", "")),
                ("Evidence tier", r.get("tier", "")),
                ("Top PMIDs", r.get("top_pmids", "")),
            ], col_widths=[3.4*cm, USABLE_WIDTH-3.4*cm])],
            [raw_para("<b>Biological interpretation.</b> " + esc(r.get("biological_interpretation", "")), "body")],
            [raw_para("<b>PubMed / LLM literature synthesis.</b> " + esc(r.get("literature_synthesis", "")), "body")],
            [raw_para("<b>Literature synthesis source.</b> " + esc(r.get("literature_synthesis_source", "")) + "; model/status: " + esc(r.get("literature_llm_model_used", "")) + " / " + esc(r.get("literature_llm_status", "")), "small")],
            [raw_para("<b>Classification relevance.</b> " + esc(r.get("classification_hint", "")), "body")],
            [raw_para("<b>Caveat.</b> " + esc(r.get("caveat", "")), "small")],
        ]
        tbl = Table(rows, colWidths=[USABLE_WIDTH], hAlign="LEFT")
        tbl.setStyle(TableStyle([
            ("BOX", (0, 0), (-1, -1), 0.35, LINE),
            ("BACKGROUND", (0, 0), (0, 0), colors.HexColor("#edf4fa")),
            ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ("RIGHTPADDING", (0, 0), (-1, -1), 7),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ]))
        blocks.append(tbl)
        blocks.append(Spacer(1, 8))
    return blocks


def filter_sample_gistic_matrix(gistic_matrix: pd.DataFrame, sample: str) -> pd.DataFrame:
    if gistic_matrix.empty:
        return pd.DataFrame(columns=["gistic_feature", "call_value"])
    gm = gistic_matrix.copy()
    gm.index = gm.index.astype(str)
    if sample not in gm.index:
        return pd.DataFrame(columns=["gistic_feature", "call_value"])
    vals = gm.loc[[sample]].T.reset_index()
    vals.columns = ["gistic_feature", "call_value"]
    vals["call_value_numeric"] = pd.to_numeric(vals["call_value"], errors="coerce").fillna(0)
    vals = vals[vals["call_value_numeric"] != 0].drop(columns=["call_value_numeric"], errors="ignore")
    return vals


def filter_driver_matrix(driver_matrix: pd.DataFrame, sample: str) -> pd.DataFrame:
    if driver_matrix.empty:
        return pd.DataFrame(columns=["driver_region", "signed_call"])
    dm = driver_matrix.copy()
    dm.index = dm.index.astype(str)
    if sample not in dm.index:
        return pd.DataFrame(columns=["driver_region", "signed_call"])
    vals = dm.loc[sample].reset_index()
    vals.columns = ["driver_region", "signed_call"]
    vals["signed_call_numeric"] = pd.to_numeric(vals["signed_call"], errors="coerce").fillna(0)
    vals = vals[vals["signed_call_numeric"] != 0].drop(columns=["signed_call_numeric"], errors="ignore")
    return vals


def sample_report_data(
    sample: str,
    row: pd.Series,
    sample_summary: pd.DataFrame,
    events: pd.DataFrame,
    driver_hits: pd.DataFrame,
    driver_matrix: pd.DataFrame,
    gistic_matrix: pd.DataFrame,
    gistic_long: pd.DataFrame,
    sample_knowledge: pd.DataFrame,
    sample_knowledge_summary: pd.DataFrame,
    references: pd.DataFrame,
    sample_literature: pd.DataFrame,
    sample_literature_summary: pd.DataFrame,
    pathology_concordance: pd.DataFrame,
    max_events: int,
    include_full_events: bool,
) -> dict[str, Any]:
    ks_row = pd.Series(dtype=object)
    if not sample_knowledge_summary.empty and "sample" in sample_knowledge_summary.columns:
        m = sample_knowledge_summary[sample_knowledge_summary["sample"].astype(str) == sample]
        if not m.empty:
            ks_row = m.iloc[0]

    pathology_row = pd.Series(dtype=object)
    if not pathology_concordance.empty and "sample" in pathology_concordance.columns:
        pm = pathology_concordance[pathology_concordance["sample"].astype(str) == sample]
        if not pm.empty:
            pathology_row = pm.iloc[0]

    ev = events.copy()
    if not ev.empty:
        if "mean_log2" in ev.columns:
            ev["_abs"] = pd.to_numeric(ev["mean_log2"], errors="coerce").abs().fillna(0)
            ev = ev.sort_values(["_abs", "chrom", "start"], ascending=[False, True, True]).drop(columns=["_abs"], errors="ignore")
        elif "chrom" in ev.columns and "start" in ev.columns:
            ev = ev.sort_values(["chrom", "start"])
        if max_events > 0:
            ev_pdf = ev.head(max_events)
        else:
            ev_pdf = ev
    else:
        ev_pdf = ev

    gistic_calls_long = pd.DataFrame()
    if not gistic_long.empty and "sample" in gistic_long.columns:
        gistic_calls_long = gistic_long[gistic_long["sample"].astype(str) == sample].copy()
    gistic_calls_matrix = filter_sample_gistic_matrix(gistic_matrix, sample)
    if gistic_calls_long.empty:
        gistic_calls = gistic_calls_matrix
    else:
        gistic_calls = gistic_calls_long

    driver_matrix_calls = filter_driver_matrix(driver_matrix, sample)
    feature_ids = []
    if not sample_knowledge.empty and "feature_id" in sample_knowledge.columns:
        feature_ids = [x for x in sample_knowledge["feature_id"].astype(str).tolist() if x and x != "none_detected"]
    refs = references[references["feature_id"].astype(str).isin(feature_ids)].copy() if not references.empty and "feature_id" in references.columns else pd.DataFrame()
    if not refs.empty and "title" in refs.columns:
        if "selected_influential" in refs.columns:
            refs["_selected"] = refs["selected_influential"].astype(str).str.lower().isin(["true", "1", "yes"]).astype(int)
        else:
            refs["_selected"] = 0
        if "influence_score" in refs.columns:
            refs["_influence"] = pd.to_numeric(refs["influence_score"], errors="coerce").fillna(0)
        else:
            refs["_influence"] = 0
        refs["_score"] = refs["title"].astype(str).ne("PMID seed from built-in CNA knowledge dictionary").astype(int)
        refs = refs.sort_values(["_selected", "_influence", "_score", "feature_id"], ascending=[False, False, False, True]).drop(columns=["_selected", "_influence", "_score"], errors="ignore")

    sample_lit = pd.DataFrame()
    if sample_literature is not None and not sample_literature.empty and "sample" in sample_literature.columns:
        sample_lit = sample_literature[sample_literature["sample"].astype(str) == sample].copy()
        if not sample_lit.empty:
            if "paper_rank" in sample_lit.columns:
                sample_lit["_rank"] = pd.to_numeric(sample_lit["paper_rank"], errors="coerce").fillna(9999)
                sample_lit = sample_lit.sort_values(["_rank"]).drop(columns=["_rank"], errors="ignore")
            elif "influence_score" in sample_lit.columns:
                sample_lit["_score"] = pd.to_numeric(sample_lit["influence_score"], errors="coerce").fillna(0)
                sample_lit = sample_lit.sort_values(["_score"], ascending=False).drop(columns=["_score"], errors="ignore")

    sample_lit_summary_row = pd.Series(dtype=object)
    if sample_literature_summary is not None and not sample_literature_summary.empty and "sample" in sample_literature_summary.columns:
        sm = sample_literature_summary[sample_literature_summary["sample"].astype(str) == sample]
        if not sm.empty:
            sample_lit_summary_row = sm.iloc[0]

    class_pairs = [
        ("Probable CNA-based classification", pathology_row.get("probable_cna_classification", "") if not pathology_row.empty else ""),
        ("Probable CNA score", pathology_row.get("probable_cna_score", "") if not pathology_row.empty else ""),
        ("Probable CNA score breakdown", pathology_row.get("probable_cna_score_breakdown", "") if not pathology_row.empty else ""),
        ("Rule-based CNA class", row.get("rule_based_cna_class", "")),
        ("Knowledge-refined CNA pattern", ks_row.get("knowledge_refined_class", "") if not ks_row.empty else ""),
        ("Knowledge rationale", ks_row.get("knowledge_refined_class_rationale", "") if not ks_row.empty else ""),
        ("CNA burden class", row.get("cna_burden_class", "")),
        ("Gain/loss direction class", row.get("gain_loss_direction_class", "")),
        ("Focal/broad class", row.get("focal_broad_class", "")),
        ("N CNA events", row.get("n_cna_events", "")),
        ("Altered genome size (Mb)", fmt(row.get("altered_mb", ""), 1)),
        ("Gain Mb / Loss Mb", f"{fmt(row.get('gain_mb', 0),1)} / {fmt(row.get('loss_mb', 0),1)}"),
        ("Chromosomes affected", row.get("n_chromosomes_affected", "")),
        ("Arms affected", row.get("n_arms_affected", "")),
        ("Driver-region flags", row.get("driver_region_flags", "")),
        ("Hierarchical / NMF cluster", f"{row.get('hierarchical_cluster','')} / {row.get('nmf_cluster','')}"),
    ]

    event_cols = [c for c in ["state", "chrom", "start", "end", "size_mb", "cytoband", "n_bins", "mean_log2", "median_log2", "estimated_total_copy_number", "copy_code", "cna_shorthand", "source", "input_source_file"] if c in ev_pdf.columns]
    driver_hit_cols = [c for c in ["feature_id", "feature_label", "genes", "event_state", "event_chrom", "event_start", "event_end", "event_cytoband", "mean_log2", "overlap_fraction_region"] if c in driver_hits.columns]
    gistic_cols = list(gistic_calls.columns) if not gistic_calls.empty else []
    ref_cols = [c for c in ["feature_id", "selected_influential", "influence_rank", "influence_score", "feature_reference_rank", "pmid", "year", "title", "journal", "cited_by_count", "url", "selected_by", "llm_model_used", "llm_status", "abstract_excerpt"] if c in refs.columns]
    sample_lit_cols = [c for c in ["paper_rank", "feature_display", "genes", "influence_score", "pmid", "year", "title", "journal", "cited_by_count", "selection_method", "llm_ranker_model", "abstract_excerpt"] if c in sample_lit.columns]

    return {
        "ks_row": ks_row,
        "pathology_row": pathology_row,
        "events_pdf": ev_pdf,
        "event_cols": event_cols,
        "class_pairs": class_pairs,
        "sample_summary": sample_summary,
        "state_counts": state_counts(events),
        "driver_matrix_calls": driver_matrix_calls,
        "driver_hits": driver_hits,
        "driver_hit_cols": driver_hit_cols,
        "gistic_calls": gistic_calls,
        "gistic_cols": gistic_cols,
        "sample_knowledge": sample_knowledge,
        "references": refs,
        "ref_cols": ref_cols,
        "sample_literature": sample_lit,
        "sample_literature_summary": sample_lit_summary_row,
        "sample_lit_cols": sample_lit_cols,
        "n_events_total": len(events),
        "n_events_shown": len(ev_pdf),
        "max_events": max_events,
        "include_full_events": include_full_events,
    }


def make_header_footer(canvas, doc, sample: str):
    canvas.saveState()
    width, height = A4
    canvas.setFillColor(INK)
    canvas.setFont("Helvetica-Bold", 9.2)
    canvas.drawString(doc.leftMargin, height - 12*mm, "OncoTracer AI - CNA Knowledge Report")
    canvas.setFont("Helvetica", 7.8)
    canvas.setFillColor(MUTED)
    canvas.drawRightString(width - doc.rightMargin, height - 12*mm, f"Sample: {sample}")
    canvas.setStrokeColor(LINE)
    canvas.setLineWidth(0.45)
    canvas.line(doc.leftMargin, height - 15*mm, width - doc.rightMargin, height - 15*mm)
    canvas.setFont("Helvetica", 7.2)
    canvas.setFillColor(colors.HexColor("#6b7280"))
    canvas.drawString(doc.leftMargin, 9.5*mm, "Research-use CNA interpretation from low-pass WGS/SAMURAI calls. Not a standalone clinical diagnosis.")
    canvas.drawRightString(width - doc.rightMargin, 9.5*mm, f"Page {doc.page}")
    canvas.restoreState()


def build_sample_pdf(
    out_pdf: Path,
    sample: str,
    row: pd.Series,
    data: dict[str, Any],
) -> None:
    doc = SimpleDocTemplate(
        str(out_pdf), pagesize=A4,
        rightMargin=RIGHT_MARGIN, leftMargin=LEFT_MARGIN,
        topMargin=TOP_MARGIN, bottomMargin=BOTTOM_MARGIN,
        title=f"CNA knowledge report - {sample}",
        author="OncoTracer AI CNA classifier",
    )
    story: list[Any] = []
    story.append(Paragraph("OncoTracer AI CNA Knowledge Report", STYLES["title"]))
    story.append(Paragraph(f"Sample: <b>{esc(sample)}</b> | Assay context: low-pass WGS / SAMURAI CNA codification", STYLES["subtitle"]))
    story.append(Paragraph("Research-style interpretation: relevant findings, biological context, caveats, methods, and references.", STYLES["subtitle"]))
    story.append(Spacer(1, 4))
    story.append(title_banner(row))
    story.append(Spacer(1, 8))
    story.append(metric_card_table(row))
    story.append(Spacer(1, 8))
    story.extend(probable_cna_pdf_blocks(data.get("pathology_row")))
    story.extend(pathology_agreement_pdf_blocks(data.get("pathology_row")))

    add_section(story, "1 - CNA INTERPRETATION SUMMARY", min_space=5.0*cm)
    story.extend(interpretation_paragraphs(row, data["ks_row"], data["sample_knowledge"]))

    add_section(story, "2 - SAMPLE CLASSIFICATION AND BURDEN METRICS", min_space=6.0*cm)
    story.append(kv_table(data["class_pairs"]))
    story.append(Spacer(1, 6))
    story.append(KeepTogether([
        Paragraph("SAMURAI CNA summary row", STYLES["h2"]),
        dataframe_table(data["sample_summary"], style="tiny", max_char=110),
    ]))
    story.append(Spacer(1, 5))
    story.append(CondPageBreak(3.0*cm))
    story.append(KeepTogether([
        Paragraph("CNA state counts", STYLES["h2"]),
        dataframe_table(data["state_counts"], columns=["state", "n_events", "altered_mb"], style="small", max_char=80),
    ]))

    add_section(story, "3 - RELEVANT CNA BIOMARKERS AND BIOLOGICAL INTERPRETATION", min_space=7.0*cm)
    story.extend(knowledge_feature_blocks(data["sample_knowledge"]))

    add_section(story, "4 - DRIVER-REGION CALLS AND HIT TABLE", min_space=7.0*cm)
    story.append(Paragraph("Driver-region matrix calls", STYLES["h2"]))
    story.append(dataframe_table(data["driver_matrix_calls"], columns=["driver_region", "signed_call"], style="tiny", max_char=100))
    story.append(Spacer(1, 6))
    story.append(Paragraph("Driver-region hit table", STYLES["h2"]))
    story.append(dataframe_table(data["driver_hits"], columns=data["driver_hit_cols"], style="tiny", max_char=90))

    add_section(story, "5 - CNA EVENT TABLE", min_space=6.0*cm)
    story.append(dataframe_table(data["events_pdf"], columns=data["event_cols"], style="tiny", max_char=90))
    if data["max_events"] > 0 and data["n_events_total"] > data["n_events_shown"]:
        story.append(para(f"Only {data['n_events_shown']} of {data['n_events_total']} CNA events are shown in this PDF because --pdf_max_events is set. Set --pdf_max_events 0 to include every CNA event.", "small"))

    add_section(story, "6 - METHODS AND INTERPRETATION LIMITATIONS", min_space=6.0*cm)
    methods = [
        "Input CNA calls were produced from SAMURAI/QDNAseq-style low-pass whole-genome sequencing codification. The report summarizes high-confidence segmented gains, losses, deep losses, and amplifications after the configured pipeline thresholds.",
        "What low-pass WGS can do: " + low_pass_wgs_capabilities_text(),
        "What low-pass WGS cannot do alone: " + low_pass_wgs_limitations_text(),
        "Driver-region annotations are based on overlap between CNA events and the context-specific CNA region catalog selected by --sample_set, or a user-provided catalog when --region_catalog is supplied. The knowledge-enriched pattern is a research interpretation layer and intentionally does not override formal pathology or integrated molecular classification.",
        "GISTIC2, when available and run on enough samples, contributes cohort-level recurrent CNA evidence in the cohort report; empty per-sample GISTIC sections are intentionally omitted from individual reports.",
        pathology_score_method_text(),
        probable_cna_score_method_text(),
        evidence_tier_method_text(),
        "PubMed / Hugging Face literature synthesis and influence ranking: when --knowledge_web true, the pipeline queries Europe-PMC/PubMed-style metadata for CNA features detected in the sample using the --sample_set context. The deep literature layer retrieves larger candidate sets for gene/region/CNA combinations, ranks papers using citation count, direct CNA/gene/context text overlap, abstract availability, recency, and review/classification signals, and optionally uses local Hugging Face text-generation/summarization models to synthesize the literature and score candidate-paper relevance. If models fail or are unavailable, deterministic ranking and extractive PubMed-text synthesis are used and the model status is recorded.",
    ]
    for m in methods:
        story.append(raw_para("<b>-</b> " + esc(m), "body_indent"))

    add_section(story, "7 - SELECTED INFLUENTIAL REFERENCES AND WEB-KNOWLEDGE TRACE", min_space=5.0*cm)
    story.append(raw_para("<b>Literature-selection method:</b> for each detected driver CNA, the pipeline queries PubMed/Europe-PMC metadata and abstracts, ranks candidate papers by citation count, CNA/gene/context text overlap, abstract availability, recency, and optional local Hugging Face model scores. The selected papers below are intended to prioritize manual review, not to provide clinical-grade evidence grading.", "body"))
    story.append(Spacer(1, 5))
    if not data.get("sample_literature", pd.DataFrame()).empty:
        story.append(Paragraph("Selected influential papers for this sample", STYLES["h2"]))
        story.append(dataframe_table(data["sample_literature"], columns=data["sample_lit_cols"], style="tiny", max_char=115))
        story.append(Spacer(1, 6))
    story.append(Paragraph("Full feature-level web-knowledge trace", STYLES["h2"]))
    story.append(dataframe_table(data["references"], columns=data["ref_cols"], style="tiny", max_char=120))
    story.append(Spacer(1, 5))
    story.append(warning_box("generated automatically by the CNA classifier PDF/HTML extension. Web-derived literature titles/abstracts and Hugging Face outputs are assistive traces and should be reviewed manually before use in manuscripts or clinical documents."))

    doc.build(story, onFirstPage=lambda canvas, d: make_header_footer(canvas, d, sample), onLaterPages=lambda canvas, d: make_header_footer(canvas, d, sample))


def html_kv_table(pairs: list[tuple[str, Any]]) -> str:
    return html_table(dataframe_from_pairs(pairs), css_class="table kv-table")


def html_section(title: str, body: str) -> str:
    return f"<section class='section'><h2>{html.escape(title)}</h2>{body}</section>"


def html_metric_cards(row: pd.Series) -> str:
    metrics = [
        ("CNA events", fmt(row.get("n_cna_events", 0), 0)),
        ("Altered Mb", fmt(row.get("altered_mb", 0), 1)),
        ("Gain / loss Mb", f"{fmt(row.get('gain_mb', 0), 1)} / {fmt(row.get('loss_mb', 0), 1)}"),
        ("Chr / arms", f"{fmt(row.get('n_chromosomes_affected', 0), 0)} / {fmt(row.get('n_arms_affected', 0), 0)}"),
    ]
    return "<div class='metric-grid'>" + "".join(f"<div class='metric-card'><div class='metric-value'>{html.escape(v)}</div><div class='metric-label'>{html.escape(k)}</div></div>" for k, v in metrics) + "</div>"


def html_interpretation(row: pd.Series, data: dict[str, Any]) -> str:
    sample = safe_str(row.get("sample"))
    refined = safe_str(data["ks_row"].get("knowledge_refined_class")) if not data["ks_row"].empty else ""
    rationale = safe_str(data["ks_row"].get("knowledge_refined_class_rationale")) if not data["ks_row"].empty else ""
    flags = split_field(row.get("driver_region_flags", ""))
    bits = [
        f"<p>Sample <strong>{html.escape(sample)}</strong> shows <strong>{html.escape(fmt(row.get('n_cna_events', 0), 0))}</strong> high-confidence CNA events, covering approximately <strong>{html.escape(fmt(row.get('altered_mb', 0), 1))} Mb</strong> and affecting <strong>{html.escape(fmt(row.get('n_chromosomes_affected', 0), 0))}</strong> chromosomes / <strong>{html.escape(fmt(row.get('n_arms_affected', 0), 0))}</strong> chromosome arms under the current thresholds.</p>"
    ]
    if refined:
        bits.append(f"<p>Knowledge-refined CNA pattern: <strong>{html.escape(refined)}</strong>. {html.escape(rationale)}</p>")
    if not data["ks_row"].empty:
        lit_syn = safe_str(data["ks_row"].get("knowledge_literature_synthesis", ""))
        lit_stat = safe_str(data["ks_row"].get("knowledge_literature_llm_status", ""))
        if lit_syn:
            bits.append(f"<p><strong>Literature/LLM-supported interpretation.</strong> {html.escape(lit_syn)}</p>")
        if lit_stat:
            bits.append(f"<p class='muted'><strong>Literature model trace.</strong> {html.escape(lit_stat)}</p>")
    if flags:
        bits.append("<p>Canonical CNA flags detected: " + html.escape(", ".join(flags)) + ".</p>")
    else:
        bits.append("<p>No canonical driver-region CNA flag was detected by the current CNA region catalog.</p>")
    if not data["sample_knowledge"].empty and "display" in data["sample_knowledge"].columns:
        top = data["sample_knowledge"][data["sample_knowledge"].get("feature_id", pd.Series(dtype=str)).astype(str) != "none_detected"].head(5)
        if not top.empty:
            bits.append("<p>Highest-priority CNA knowledge features for review: " + html.escape("; ".join(top["display"].astype(str).tolist())) + ".</p>")
    bits.append("<div class='warning'><strong>Important limitation:</strong> this is CNA-only interpretation. It must be integrated with histology, immunophenotype, SNVs/indels, structural variants/translocations, expression, methylation when available, and clinical context before tumor diagnosis or subtype assignment.</div>")
    return "".join(bits)


def html_knowledge_cards(sk: pd.DataFrame) -> str:
    if sk.empty:
        return "<p class='muted'>No knowledge-enrichment rows were produced for this sample.</p>"
    cards: list[str] = []
    for _, r in sk.iterrows():
        title = safe_str(r.get("display")) or safe_str(r.get("feature_id")) or "CNA knowledge feature"
        meta = dataframe_from_pairs([
            ("Feature ID", r.get("feature_id", "")),
            ("Genes / region", r.get("genes", "")),
            ("Observed state", r.get("event_state", "")),
            ("Cytoband", r.get("event_cytoband", "")),
            ("Evidence tier", r.get("tier", "")),
            ("Top PMIDs", r.get("top_pmids", "")),
        ])
        cards.append(f"""
        <div class='biomarker-card'>
          <h3>{html.escape(title)}</h3>
          {html_table(meta, css_class='table kv-table')}
          <p><strong>Biological interpretation.</strong> {html.escape(safe_str(r.get('biological_interpretation', '')))}</p>
          <p><strong>PubMed / LLM literature synthesis.</strong> {html.escape(safe_str(r.get('literature_synthesis', '')))}</p>
          <p class='muted'><strong>Literature synthesis source.</strong> {html.escape(safe_str(r.get('literature_synthesis_source', '')))}; model/status: {html.escape(safe_str(r.get('literature_llm_model_used', '')))} / {html.escape(safe_str(r.get('literature_llm_status', '')))}</p>
          <p><strong>Classification relevance.</strong> {html.escape(safe_str(r.get('classification_hint', '')))}</p>
          <p class='muted'><strong>Caveat.</strong> {html.escape(safe_str(r.get('caveat', '')))}</p>
        </div>""")
    return "".join(cards)


def html_pathology_agreement(data: dict[str, Any]) -> str:
    pr = data.get("pathology_row", pd.Series(dtype=object))
    if not show_pathology_assessment(pr):
        return ""
    call = safe_str(pr.get("agreement_call"))
    color = agreement_hex(call)
    label = agreement_label(call)
    score = fmt(pr.get("agreement_score", ""), 0)
    pairs = [
        ("Reported pathology diagnosis", pr.get("pathology_final_diagnosis", "")),
        ("Pathology category", "; ".join([x for x in [safe_str(pr.get("pathology_diagnosis_category_1", "")), safe_str(pr.get("pathology_diagnosis_category_2", ""))] if x])),
        ("Inferred pathology lineage/subtype", f"{safe_str(pr.get('pathology_lineage',''))} / {safe_str(pr.get('pathology_subtype_inferred',''))}"),
        ("CNA knowledge pattern", pr.get("cna_knowledge_pattern", "")),
        ("CNA rule-based class", pr.get("cna_rule_based_class", "")),
        ("Why this call was made", pr.get("agreement_summary", "")),
        ("Numeric score breakdown", pr.get("agreement_score_breakdown", "")),
        ("Token-only score", pr.get("agreement_score_token_only", "")),
        ("Final score source", pr.get("agreement_score_final_source", "")),
        ("Biomedical model consensus score", pr.get("agreement_biomed_consensus_score", "")),
        ("Biomedical model trial scores", pr.get("agreement_biomed_model_scores", "")),
        ("Biomedical model status", pr.get("agreement_biomed_model_status", "")),
        ("Probability estimate", pr.get("agreement_probability_estimate", "")),
        ("Probability calibration status", pr.get("agreement_probability_calibration_status", "")),
        ("Probability method", pr.get("agreement_probability_method", "")),
        ("Token overlap", pr.get("agreement_token_overlap", "")),
        ("Pathology tokens", pr.get("agreement_pathology_tokens", "")),
        ("CNA tokens", pr.get("agreement_cna_tokens", "")),
        ("Model", pr.get("agreement_score_model", "")),
        ("How the score was assessed", pathology_score_explanation(pr)),
        ("Explainability / rationale", pr.get("agreement_rationale", "")),
        ("Supporting evidence", pr.get("supporting_evidence", "")),
        ("Cautionary evidence", pr.get("cautionary_evidence", "")),
        ("IHC/pathology highlights", pr.get("pathology_ihc_highlights", "")),
        ("Anatomic site", "; ".join([x for x in [safe_str(pr.get("pathology_specimen_organ", "")), safe_str(pr.get("pathology_anatomical_site", ""))] if x])),
    ]
    table = html_kv_table(pairs)
    caveat = "This is a compatibility assessment between CNA-derived patterns and the provided pathology text. It does not override the pathologist diagnosis and cannot resolve morphology, immunophenotype, SNVs/indels, balanced translocations, expression, methylation, or clinical context."
    return f"""
    <section class='pathology-section'>
      <h2>PATHOLOGY AGREEMENT ASSESSMENT</h2>
      <div class='agreement-banner' style='background:{html.escape(color)}'>
        <div><strong>{html.escape(label)}</strong></div><div>Score: <strong>{html.escape(score)}</strong></div>
      </div>
      {table}
      <div class='warning'><strong>Interpretation caveat:</strong> {html.escape(caveat)}</div>
    </section>
    """


def build_sample_html(out_html: Path, sample: str, row: pd.Series, data: dict[str, Any], pdf_name: str) -> None:
    state_counts_html = html_table(data["state_counts"])
    event_note = ""
    if data["max_events"] > 0 and data["n_events_total"] > data["n_events_shown"]:
        event_note = f"<p class='muted'>Only {data['n_events_shown']} of {data['n_events_total']} CNA events are shown because --pdf_max_events is set. Set --pdf_max_events 0 to include every CNA event.</p>"
    # The per-sample HTML mirrors the PDF sections and uses the same source tables.
    body = html_probable_cna_assessment(data) + html_pathology_agreement(data) + "".join([
        html_section("1 - CNA INTERPRETATION SUMMARY", html_interpretation(row, data)),
        html_section("2 - SAMPLE CLASSIFICATION AND BURDEN METRICS", html_metric_cards(row) + "<h3>Classification fields</h3>" + html_kv_table(data["class_pairs"]) + "<h3>SAMURAI CNA summary row</h3>" + html_table(data["sample_summary"]) + "<h3>CNA state counts</h3>" + state_counts_html),
        html_section("3 - RELEVANT CNA BIOMARKERS AND BIOLOGICAL INTERPRETATION", html_knowledge_cards(data["sample_knowledge"])),
        html_section("4 - DRIVER-REGION CALLS AND HIT TABLE", "<h3>Driver-region matrix calls</h3>" + html_table(data["driver_matrix_calls"], columns=["driver_region", "signed_call"]) + "<h3>Driver-region hit table</h3>" + html_table(data["driver_hits"], columns=data["driver_hit_cols"])),
        html_section("5 - CNA EVENT TABLE", event_note + html_table(data["events_pdf"], columns=data["event_cols"])),
        html_section("6 - METHODS AND INTERPRETATION LIMITATIONS", f"""
            <ul>
              <li>Input CNA calls were produced from SAMURAI/QDNAseq-style low-pass whole-genome sequencing codification. The report summarizes high-confidence segmented gains, losses, deep losses, and amplifications after the configured pipeline thresholds.</li>
              <li><strong>What low-pass WGS can do:</strong> {html.escape(low_pass_wgs_capabilities_text())}</li>
              <li><strong>What low-pass WGS cannot do alone:</strong> {html.escape(low_pass_wgs_limitations_text())}</li>
              <li>Driver-region annotations are based on overlap between CNA events and the context-specific CNA region catalog selected by --sample_set, or a user-provided catalog when --region_catalog is supplied. The knowledge-enriched pattern is a research interpretation layer and intentionally does not override formal pathology or integrated molecular classification.</li>
              <li>GISTIC2, when available and run on enough samples, contributes cohort-level recurrent CNA evidence in the cohort report; empty per-sample GISTIC sections are intentionally omitted from individual reports.</li>
              <li>{html.escape(pathology_score_method_text())}</li>
              <li>{html.escape(probable_cna_score_method_text())}</li>
              <li>{html.escape(evidence_tier_method_text())}</li>
            </ul>"""),
        html_section("7 - SELECTED INFLUENTIAL REFERENCES AND WEB-KNOWLEDGE TRACE", "<p><strong>Literature-selection method:</strong> for each detected driver CNA, the pipeline queries PubMed/Europe-PMC metadata and abstracts, ranks candidate papers by citation count, CNA/gene/context text overlap, abstract availability, recency, and optional local Hugging Face model scores. The selected papers prioritize manual review; they are not clinical-grade evidence grading.</p>" + ("<h3>Selected influential papers for this sample</h3>" + html_table(data["sample_literature"], columns=data["sample_lit_cols"]) if not data.get("sample_literature", pd.DataFrame()).empty else "") + "<h3>Full feature-level web-knowledge trace</h3>" + html_table(data["references"], columns=data["ref_cols"]) + "<div class='warning'><strong>Report status:</strong> generated automatically by the CNA classifier PDF/HTML extension. Web-derived literature titles/abstracts and Hugging Face outputs are assistive traces and should be reviewed manually before use in manuscripts or clinical documents.</div>"),
    ])
    title = f"OncoTracer AI CNA Knowledge Report - {sample}"
    burden = safe_str(row.get("cna_burden_class", "unknown"))
    burden_color_hex = "#999999"
    if burden in BURDEN_COLORS:
        # ReportLab Color -> approximate hex via .hexval in newer versions unavailable; map explicitly.
        burden_color_hex = {
            "CNA-flat_or_no_high-confidence_CNA": "#BDBDBD", "CNA-flat": "#BDBDBD", "CNA-low": "#80B1D3",
            "CNA-intermediate": "#FDB462", "CNA-high_complex": "#FB8072", "CNA-ultracomplex": "#B2182B",
        }.get(burden, "#999999")
    html_text = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>
:root {{ --ink:#172033; --muted:#5f6b7a; --line:#d7dde6; --bg:#f5f7fb; --panel:#ffffff; --navy:#172033; --accent:#2f6f9f; }}
body {{ font-family: Arial, Helvetica, sans-serif; margin:0; color:var(--ink); background:var(--bg); }}
main {{ max-width: 1220px; margin: 0 auto; padding: 28px 36px 60px; }}
.header {{ background: white; border:1px solid var(--line); border-radius:16px; padding:22px 24px; box-shadow:0 2px 8px rgba(23,32,51,.04); }}
h1 {{ font-size:30px; letter-spacing:-.03em; margin:0 0 8px; }}
h2 {{ background:var(--navy); color:white; font-size:17px; padding:10px 14px; border-radius:8px; margin:30px 0 14px; }}
h3 {{ font-size:15px; margin:18px 0 8px; }}
.subtitle {{ color:var(--muted); margin:4px 0; }}
.badge-row {{ display:grid; grid-template-columns:170px 1fr 170px 1fr; border:1px solid var(--line); margin-top:14px; }}
.badge-row > div {{ padding:10px 12px; border-right:1px solid var(--line); }}
.badge-label {{ font-weight:700; background:#f5f7fb; }}
.badge-value {{ background:{burden_color_hex}; }}
.agreement-banner {{ color:white; display:grid; grid-template-columns:1fr 130px; gap:10px; align-items:center; border-radius:12px; padding:13px 16px; margin:12px 0; box-shadow:0 2px 8px rgba(23,32,51,.08); }}
.agreement-banner div:last-child {{ text-align:right; }}
.metric-grid {{ display:grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap:10px; margin:12px 0; }}
.metric-card {{ background:white; border:1px solid var(--line); border-radius:12px; padding:14px 12px; text-align:center; }}
.metric-value {{ font-weight:700; font-size:20px; }}
.metric-label {{ color:var(--muted); font-size:12px; margin-top:4px; }}
.section {{ background:transparent; }}
.warning {{ background:#fff5cc; border:1px solid #d6a300; border-left:5px solid #d6a300; padding:12px 14px; border-radius:8px; margin:14px 0; }}
.table {{ border-collapse:collapse; font-size:12px; width:max-content; min-width:100%; background:white; }}
.table th {{ background:#eef2f7; }}
.table th, .table td {{ border:1px solid #d7dde6; padding:6px 8px; text-align:left; vertical-align:top; }}
.table-wrap {{ overflow-x:auto; background:white; border:1px solid #d7dde6; border-radius:12px; padding:8px; margin:8px 0 14px; }}
.kv-table td:first-child {{ font-weight:700; background:#f5f7fb; white-space:nowrap; }}
.biomarker-card {{ background:white; border:1px solid var(--line); border-radius:14px; padding:14px 16px; margin:14px 0; box-shadow:0 1px 4px rgba(23,32,51,.035); }}
.biomarker-card h3 {{ margin-top:0; color:#172033; }}
.muted {{ color:var(--muted); }}
a {{ color:var(--accent); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
ul {{ line-height:1.65; }}
</style>
</head>
<body><main>
<p><a href="index.html">Report index</a> | <a href="{html.escape(pdf_name)}">Matched PDF</a> | <a href="../cna_classifier_report.html">Cohort report</a></p>
<div class="header">
  <h1>OncoTracer AI CNA Knowledge Report</h1>
  <p class="subtitle">Sample: <strong>{html.escape(sample)}</strong> | Assay context: low-pass WGS / SAMURAI CNA codification</p>
  <p class="subtitle">The HTML and PDF report are generated from the same section data and contain the same report tables.</p>
  <div class="badge-row"><div class="badge-label">CNA burden</div><div class="badge-value">{html.escape(burden.replace('_',' '))}</div><div class="badge-label">Rule-based class</div><div>{html.escape(safe_str(row.get('rule_based_cna_class','')))}</div></div>
</div>
{body}
</main></body></html>"""
    out_html.write_text(html_text)


def combine_pdfs(pdf_paths: list[Path], out_pdf: Path) -> None:
    if PdfReader is None or PdfWriter is None or not pdf_paths:
        return
    writer = PdfWriter()
    for p in pdf_paths:
        try:
            reader = PdfReader(str(p))
            for page in reader.pages:
                writer.add_page(page)
        except Exception:
            continue
    if len(writer.pages) > 0:
        with out_pdf.open("wb") as fh:
            writer.write(fh)


def write_index(outdir: Path, rows: list[dict[str, Any]]) -> None:
    summary = pd.DataFrame(rows)
    summary.to_csv(outdir / "pdf_html_report_index.tsv", sep="\t", index=False)
    summary.to_csv(outdir / "pdf_report_index.tsv", sep="\t", index=False)
    tr = []
    for r in rows:
        tr.append(
            "<tr>"
            f"<td>{html.escape(r['sample'])}</td>"
            f"<td><a href='{html.escape(r['html'])}'>HTML</a></td>"
            f"<td><a href='{html.escape(r['pdf'])}'>PDF</a></td>"
            f"<td>{html.escape(safe_str(r.get('pathology_agreement_call','')))}</td>"
            f"<td>{html.escape(safe_str(r.get('pathology_final_diagnosis','')))}</td>"
            f"<td>{html.escape(safe_str(r.get('probable_cna_classification','')))}</td>"
            f"<td>{html.escape(fmt(r.get('probable_cna_score',''),0))}</td>"
            f"<td>{html.escape(safe_str(r.get('knowledge_refined_class','')))}</td>"
            f"<td>{html.escape(safe_str(r.get('rule_based_cna_class','')))}</td>"
            f"<td>{html.escape(safe_str(r.get('n_cna_events','')))}</td>"
            f"<td>{html.escape(safe_str(r.get('driver_region_flags','')))}</td>"
            "</tr>"
        )
    combined = "all_sample_CNA_knowledge_reports.pdf"
    text = f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>CNA PDF/HTML reports</title>
<style>body{{font-family:Arial,Helvetica,sans-serif;margin:32px;color:#172033;background:#f5f7fb}}.panel{{background:white;border:1px solid #d7dde6;border-radius:14px;padding:20px}}table{{border-collapse:collapse;font-size:12px;background:white}}th,td{{border:1px solid #d7dde6;padding:6px 8px;text-align:left;vertical-align:top}}th{{background:#eef2f7}}a{{color:#2f6f9f;text-decoration:none}}a:hover{{text-decoration:underline}}.muted{{color:#5f6b7a}}</style>
</head><body><div class='panel'><h1>CNA knowledge HTML/PDF reports</h1><p class='muted'>Each sample has a matched HTML and PDF report generated from the same source sections and tables.</p><p><a href='../cna_classifier_report.html'>Cohort HTML report</a> | <a href='{combined}'>Combined PDF</a> | <a href='../clinician_reports/index.html'>Clinician driver summaries</a></p><table><thead><tr><th>sample</th><th>HTML</th><th>PDF</th><th>pathology agreement</th><th>reported pathology diagnosis</th><th>probable CNA classification</th><th>probable CNA score</th><th>knowledge-refined CNA pattern</th><th>rule-based class</th><th>n CNA events</th><th>driver flags</th></tr></thead><tbody>{''.join(tr)}</tbody></table></div></body></html>"""
    (outdir / "index.html").write_text(text)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate matched report-style PDF and HTML files for cancer-agnostic CNA knowledge interpretation.")
    ap.add_argument("--classification", required=True)
    ap.add_argument("--sample-summary", required=True)
    ap.add_argument("--clean-events", required=True)
    ap.add_argument("--driver-hits", required=True)
    ap.add_argument("--sample-knowledge", required=True)
    ap.add_argument("--sample-knowledge-summary", required=True)
    ap.add_argument("--knowledge-references", required=True)
    ap.add_argument("--sample-literature", required=False, default="")
    ap.add_argument("--sample-literature-summary", required=False, default="")
    ap.add_argument("--driver-matrix", default="")
    ap.add_argument("--gistic-matrix", default="")
    ap.add_argument("--gistic-long", default="")
    ap.add_argument("--gistic-summary", default="")
    ap.add_argument("--figures", required=False, default="figures")
    ap.add_argument("--pathology-concordance", required=False, default="")
    ap.add_argument("--pathology-records", required=False, default="")
    ap.add_argument("--outdir", default="pdf_reports")
    ap.add_argument("--max-events", type=int, default=0, help="0 means include all events; otherwise cap event rows per sample in both HTML and PDF.")
    ap.add_argument("--include-full-events", default="true")
    args = ap.parse_args()

    include_full_events = safe_str(args.include_full_events).strip().lower() in {"true", "1", "yes", "y", "on"}
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    classification = read_tsv(args.classification)
    sample_summary = read_tsv(args.sample_summary)
    clean_events = read_tsv(args.clean_events)
    driver_hits = read_tsv(args.driver_hits)
    sample_knowledge = read_tsv(args.sample_knowledge)
    sample_knowledge_summary = read_tsv(args.sample_knowledge_summary)
    references = read_tsv(args.knowledge_references)
    sample_literature = read_tsv(args.sample_literature) if args.sample_literature else pd.DataFrame()
    sample_literature_summary = read_tsv(args.sample_literature_summary) if args.sample_literature_summary else pd.DataFrame()
    driver_matrix = read_tsv(args.driver_matrix, index_col=0) if args.driver_matrix else pd.DataFrame()
    gistic_matrix = read_tsv(args.gistic_matrix, index_col=0) if args.gistic_matrix else pd.DataFrame()
    gistic_long = read_tsv(args.gistic_long) if args.gistic_long else pd.DataFrame()
    _gistic_summary = read_tsv(args.gistic_summary) if args.gistic_summary else pd.DataFrame()
    pathology_concordance = read_tsv(args.pathology_concordance) if args.pathology_concordance else pd.DataFrame()
    _pathology_records = read_tsv(args.pathology_records) if args.pathology_records else pd.DataFrame()

    normalize_sample_columns(classification, sample_summary, clean_events, driver_hits, sample_knowledge, sample_knowledge_summary, references, sample_literature, sample_literature_summary, gistic_long, pathology_concordance)

    if classification.empty or "sample" not in classification.columns:
        (outdir / "index.html").write_text("<html><body><h1>No reports generated</h1></body></html>")
        return

    pdf_paths: list[Path] = []
    rows: list[dict[str, Any]] = []
    for _, row in classification.sort_values("sample").iterrows():
        sample = safe_str(row.get("sample"))
        slug = slugify(sample)
        ev = clean_events[clean_events["sample"] == sample].copy() if not clean_events.empty and "sample" in clean_events.columns else pd.DataFrame()
        if not ev.empty and "chrom" in ev.columns:
            ev["_chrom_sort"] = ev["chrom"].astype(str).str.replace("chr", "", regex=False).replace({"X": "23", "Y": "24"})
            ev["_chrom_sort"] = pd.to_numeric(ev["_chrom_sort"], errors="coerce").fillna(999)
            ev["_start_sort"] = pd.to_numeric(ev.get("start", 0), errors="coerce").fillna(0)
            ev = ev.sort_values(["_chrom_sort", "_start_sort"]).drop(columns=["_chrom_sort", "_start_sort"], errors="ignore")
        dh = driver_hits[driver_hits["sample"] == sample].copy() if not driver_hits.empty and "sample" in driver_hits.columns else pd.DataFrame()
        sk = sample_knowledge[sample_knowledge["sample"] == sample].copy() if not sample_knowledge.empty and "sample" in sample_knowledge.columns else pd.DataFrame()
        ks = sample_knowledge_summary[sample_knowledge_summary["sample"] == sample].copy() if not sample_knowledge_summary.empty and "sample" in sample_knowledge_summary.columns else pd.DataFrame()
        ss = sample_summary[sample_summary["sample"] == sample].copy() if not sample_summary.empty and "sample" in sample_summary.columns else pd.DataFrame()

        data = sample_report_data(
            sample=sample,
            row=row,
            sample_summary=ss,
            events=ev,
            driver_hits=dh,
            driver_matrix=driver_matrix,
            gistic_matrix=gistic_matrix,
            gistic_long=gistic_long,
            sample_knowledge=sk,
            sample_knowledge_summary=sample_knowledge_summary,
            references=references,
            sample_literature=sample_literature,
            sample_literature_summary=sample_literature_summary,
            pathology_concordance=pathology_concordance,
            max_events=args.max_events,
            include_full_events=include_full_events,
        )
        out_pdf = outdir / f"{slug}_CNA_knowledge_report.pdf"
        out_html = outdir / f"{slug}_CNA_knowledge_report.html"
        build_sample_pdf(out_pdf, sample, row, data)
        build_sample_html(out_html, sample, row, data, out_pdf.name)
        pdf_paths.append(out_pdf)
        ks_row = ks.iloc[0] if not ks.empty else pd.Series(dtype=object)
        pr = data.get("pathology_row", pd.Series(dtype=object))
        rows.append({
            "sample": sample,
            "html": out_html.name,
            "pdf": out_pdf.name,
            "pathology_agreement_call": pr.get("agreement_call", "") if pr is not None and not pr.empty else "",
            "pathology_agreement_summary": pr.get("agreement_summary", "") if pr is not None and not pr.empty else "",
            "pathology_final_diagnosis": pr.get("pathology_final_diagnosis", "") if pr is not None and not pr.empty else "",
            "probable_cna_classification": pr.get("probable_cna_classification", "") if pr is not None and not pr.empty else "",
            "probable_cna_score": pr.get("probable_cna_score", "") if pr is not None and not pr.empty else "",
            "knowledge_refined_class": ks_row.get("knowledge_refined_class", "") if not ks_row.empty else "",
            "rule_based_cna_class": row.get("rule_based_cna_class", ""),
            "n_cna_events": row.get("n_cna_events", ""),
            "driver_region_flags": row.get("driver_region_flags", ""),
        })

    combine_pdfs(pdf_paths, outdir / "all_sample_CNA_knowledge_reports.pdf")
    write_index(outdir, rows)


if __name__ == "__main__":
    main()
