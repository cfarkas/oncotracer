#!/usr/bin/env python3
"""Generate concise clinician-oriented CNA driver/probable-classification reports.

This script is intentionally downstream-only: it does not change CNA calling,
GISTIC parsing, CNA classification, pathology agreement scoring, or literature
ranking. It consumes the existing TSV outputs and writes a compact HTML/PDF
summary per sample focused on the items a clinician is most likely to review:
probable CNA-pattern classification, pathology agreement if available, key
driver CNAs, selected influential papers, and essential limitations.
"""

from __future__ import annotations

import argparse
import html
import re
from pathlib import Path
from typing import Any

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, LongTable,
)

try:
    from pypdf import PdfReader, PdfWriter
except Exception:  # pragma: no cover
    PdfReader = PdfWriter = None

PAGE_W, PAGE_H = A4
LEFT = RIGHT = 1.35 * cm
TOP = 1.25 * cm
BOTTOM = 1.15 * cm
USABLE = PAGE_W - LEFT - RIGHT
NAVY = colors.HexColor("#162033")
BLUE = colors.HexColor("#2f6f9f")
PALE = colors.HexColor("#eef2f7")
LINE = colors.HexColor("#cfd6df")
INK = colors.HexColor("#172033")
WARN = colors.HexColor("#fff6cc")

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name="Title2", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=20, leading=23, textColor=INK, spaceAfter=8))
styles.add(ParagraphStyle(name="H1bar", parent=styles["Heading1"], fontName="Helvetica-Bold", fontSize=12.5, leading=15, textColor=colors.white, spaceAfter=0))
styles.add(ParagraphStyle(name="H2x", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=11, leading=13, textColor=INK, spaceBefore=6, spaceAfter=3))
styles.add(ParagraphStyle(name="BodyX", parent=styles["BodyText"], fontName="Helvetica", fontSize=9.3, leading=12, textColor=INK, spaceAfter=4))
styles.add(ParagraphStyle(name="SmallX", parent=styles["BodyText"], fontName="Helvetica", fontSize=7.4, leading=9, textColor=INK))
styles.add(ParagraphStyle(name="TinyX", parent=styles["BodyText"], fontName="Helvetica", fontSize=6.1, leading=7.2, textColor=INK))
styles.add(ParagraphStyle(name="EmphX", parent=styles["BodyText"], fontName="Helvetica-Bold", fontSize=9.2, leading=12, textColor=INK, spaceAfter=4))
styles.add(ParagraphStyle(name="RightSmall", parent=styles["SmallX"], alignment=TA_RIGHT))


def safe(x: Any) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    return str(x)


def esc(x: Any) -> str:
    return html.escape(safe(x))


def slugify(x: Any) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", safe(x)).strip("_")
    return s or "sample"


def read_tsv(path: str | Path, index_col: int | None = None) -> pd.DataFrame:
    if not path or not Path(path).exists() or Path(path).stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(path, sep="\t", dtype=str, keep_default_na=False, index_col=index_col)
    except Exception:
        return pd.DataFrame()


def normalize_sample(*dfs: pd.DataFrame) -> None:
    for df in dfs:
        if df is not None and not df.empty and "sample" in df.columns:
            df["sample"] = df["sample"].astype(str)


def p(text: Any, style: str = "BodyX") -> Paragraph:
    return Paragraph(esc(text), styles[style])


def raw(text: str, style: str = "BodyX") -> Paragraph:
    return Paragraph(text, styles[style])


def section(title: str) -> Table:
    t = Table([[Paragraph(esc(title), styles["H1bar"])]], colWidths=[USABLE])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), NAVY),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def table_df(df: pd.DataFrame, columns: list[str] | None = None, max_rows: int | None = None, max_char: int | None = 0, tiny: bool = False):
    """Render a DataFrame as a wrapped table.

    max_char=0/None means no text truncation. This is important for the
    clinician driver summary, where explanatory fields must remain complete.
    """
    if df is None or df.empty:
        return p("No rows.", "SmallX")
    d = df.copy()
    if columns:
        d = d[[c for c in columns if c in d.columns]]
    if max_rows:
        d = d.head(max_rows)
    if d.empty:
        return p("No rows.", "SmallX")
    style = "TinyX" if tiny else "SmallX"
    data = [[Paragraph(f"<b>{esc(c)}</b>", styles[style]) for c in d.columns]]
    for _, r in d.iterrows():
        row = []
        for c in d.columns:
            txt = safe(r.get(c, ""))
            if max_char and max_char > 0 and len(txt) > max_char:
                txt = txt[: max_char].rstrip()
            row.append(Paragraph(esc(txt), styles[style]))
        data.append(row)
    weights = []
    for c in d.columns:
        name = c.lower()
        if any(k in name for k in ["interpretation", "rationale", "reason", "why", "diagnosis", "title", "abstract"]):
            weights.append(3.0)
        elif any(k in name for k in ["feature", "region", "display", "genes", "driver", "flags"]):
            weights.append(2.0)
        elif any(k in name for k in ["url", "pmid"]):
            weights.append(1.3)
        else:
            weights.append(1.0)
    tot = sum(weights)
    widths = [USABLE * w / tot for w in weights]
    t = LongTable(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.22, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), PALE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.2),
        ("TOPPADDING", (0, 0), (-1, -1), 2.4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfcfe")]),
    ]))
    return t


def driver_table(drivers: pd.DataFrame):
    """Clinician-friendly driver table with complete text (no ellipses)."""
    if drivers is None or drivers.empty:
        return p("No driver-region CNA rows were detected under the current thresholds.", "SmallX")
    raw_d = drivers.copy()
    d = pd.DataFrame()
    # Prefer the human-readable display label over the internal feature_id.
    for src in ["display", "feature_label", "feature_id"]:
        if src in raw_d.columns:
            d["CNA driver / region"] = raw_d[src].astype(str).map(human_driver_label)
            break
    if "genes" in raw_d.columns:
        d["Genes"] = raw_d["genes"]
    if "event_state" in raw_d.columns:
        d["Observed CNA"] = raw_d["event_state"]
    if "tier" in raw_d.columns:
        d["Evidence tier"] = raw_d["tier"]
    if "classification_hint" in raw_d.columns:
        d["Why it matters"] = raw_d["classification_hint"]
    elif "feature_label" in raw_d.columns:
        d["Why it matters"] = raw_d["feature_label"]
    if "top_pmids" in raw_d.columns:
        d["PMIDs"] = raw_d["top_pmids"].astype(str).str.replace(";", ", ", regex=False)
    if "mean_log2" in raw_d.columns:
        d["Mean log2"] = raw_d["mean_log2"]
    d = d.loc[:, ~d.columns.duplicated()].copy()
    data = [[Paragraph(f"<b>{esc(c)}</b>", styles["TinyX"]) for c in d.columns]]
    for _, r in d.iterrows():
        data.append([Paragraph(esc(r.get(c, "")), styles["TinyX"]) for c in d.columns])
    # Give the explanation column the most space.
    weights = []
    for c in d.columns:
        if c == "Why it matters":
            weights.append(4.2)
        elif c == "CNA driver / region":
            weights.append(2.6)
        elif c == "Genes":
            weights.append(1.8)
        elif c == "PMIDs":
            weights.append(1.8)
        else:
            weights.append(1.1)
    tot = sum(weights)
    widths = [USABLE * w / tot for w in weights]
    t = LongTable(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.22, LINE),
        ("BACKGROUND", (0, 0), (-1, 0), PALE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.0),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fbfcfe")]),
    ]))
    return t


def kv(pairs: list[tuple[str, Any]]) -> Table:
    data = [[Paragraph(f"<b>{esc(k)}</b>", styles["SmallX"]), Paragraph(esc(v), styles["SmallX"])] for k, v in pairs]
    t = Table(data, colWidths=[4.4*cm, USABLE - 4.4*cm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.22, LINE),
        ("BACKGROUND", (0, 0), (0, -1), PALE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def header_footer(canvas, doc, sample: str) -> None:
    canvas.saveState()
    canvas.setStrokeColor(LINE); canvas.setLineWidth(0.5)
    canvas.line(LEFT, PAGE_H - 0.9*cm, PAGE_W - RIGHT, PAGE_H - 0.9*cm)
    canvas.setFont("Helvetica-Bold", 8.5); canvas.setFillColor(INK)
    canvas.drawString(LEFT, PAGE_H - 0.65*cm, "OncoTracer AI - CNA clinician driver summary")
    canvas.setFont("Helvetica", 8); canvas.drawRightString(PAGE_W - RIGHT, PAGE_H - 0.65*cm, f"Sample: {sample}")
    canvas.line(LEFT, 0.75*cm, PAGE_W - RIGHT, 0.75*cm)
    canvas.setFont("Helvetica", 7.2); canvas.setFillColor(colors.HexColor("#5f6b7a"))
    canvas.drawString(LEFT, 0.48*cm, "Research-use CNA-only interpretation from low-pass WGS/SAMURAI calls. Not a standalone clinical diagnosis.")
    canvas.drawRightString(PAGE_W - RIGHT, 0.48*cm, f"Page {doc.page}")
    canvas.restoreState()


def get_row(df: pd.DataFrame, sample: str) -> pd.Series:
    if df is None or df.empty or "sample" not in df.columns:
        return pd.Series(dtype=object)
    m = df[df["sample"].astype(str) == sample]
    return m.iloc[0] if not m.empty else pd.Series(dtype=object)


def top_drivers(sample: str, driver_hits: pd.DataFrame, sample_knowledge: pd.DataFrame, max_drivers: int) -> pd.DataFrame:
    sk = sample_knowledge[sample_knowledge["sample"].astype(str) == sample].copy() if not sample_knowledge.empty and "sample" in sample_knowledge.columns else pd.DataFrame()
    if not sk.empty and "feature_id" in sk.columns:
        sk = sk[sk["feature_id"].astype(str) != "none_detected"]
        cols = [c for c in ["feature_id", "display", "genes", "event_state", "tier", "classification_hint", "top_pmids"] if c in sk.columns]
        if cols:
            return sk[cols].head(max_drivers)
    dh = driver_hits[driver_hits["sample"].astype(str) == sample].copy() if not driver_hits.empty and "sample" in driver_hits.columns else pd.DataFrame()
    cols = [c for c in ["feature_id", "feature_label", "genes", "event_state", "mean_log2"] if c in dh.columns]
    return dh[cols].head(max_drivers) if cols else pd.DataFrame()


def selected_papers(sample: str, sample_literature: pd.DataFrame, max_rows: int = 5) -> pd.DataFrame:
    if sample_literature is None or sample_literature.empty or "sample" not in sample_literature.columns:
        return pd.DataFrame()
    d = sample_literature[sample_literature["sample"].astype(str) == sample].copy()
    if d.empty:
        return d
    if "paper_rank" in d.columns:
        d["_rank"] = pd.to_numeric(d["paper_rank"], errors="coerce").fillna(999)
    else:
        d["_rank"] = 999
    if "influence_score" in d.columns:
        d["_score"] = pd.to_numeric(d["influence_score"], errors="coerce").fillna(0)
    else:
        d["_score"] = 0
    return d.sort_values(["_rank", "_score"], ascending=[True, False]).drop(columns=["_rank", "_score"], errors="ignore").head(max_rows)


DRIVER_LABEL_MAP = {
    "2p16_REL_BCL11A_gain_amp": "2p16 REL/BCL11A gain or amplification",
    "18q21_BCL2_MALT1_gain_amp": "18q21 BCL2/MALT1 gain or amplification",
    "8q24_MYC_gain_amp": "8q24 MYC gain or amplification",
    "9p24_JAK2_PDL1_PDL2_gain_amp": "9p24 JAK2/PD-L1/PD-L2 gain or amplification",
    "9p21_CDKN2A_B_loss": "9p21 CDKN2A/B loss",
    "17p13_TP53_loss": "17p13 TP53-region loss",
    "6q_loss_PRDM1_TNFAIP3_axis": "6q PRDM1/TNFAIP3-axis loss",
    "10q23_PTEN_loss": "10q23 PTEN-region loss",
    "12q15_MDM2_CDK4_gain_amp": "12q15 MDM2/CDK4 gain or amplification",
    "1q_gain_pattern": "1q gain pattern",
    "1p_loss_pattern": "1p loss pattern",
    "chr7_gain_pattern": "chromosome 7 gain pattern",
    "22q_loss_pattern": "22q loss pattern",
}


def human_driver_label(flag: Any) -> str:
    txt = safe(flag).strip()
    if not txt:
        return ""
    txt = re.sub(r"^D:", "", txt)
    if txt in DRIVER_LABEL_MAP:
        return DRIVER_LABEL_MAP[txt]
    # Convert internal snake-case labels without corrupting already-readable
    # words such as "amplification".
    txt = txt.replace("_gain_amp", " gain or amplification")
    txt = txt.replace("_loss", " loss")
    txt = txt.replace("_", " ")
    txt = re.sub(r"\bamp\b", "amplification", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def clean_rationale(text: Any) -> str:
    """Make internal scoring/rationale text easier for clinicians to read."""
    t = safe(text).strip()
    if not t:
        return ""
    replacements = {
        "--sample_set lymphoma was supplied.": "The analysis was run in lymphoma-restricted mode, so non-lymphoma labels were suppressed.",
        "PubMed/Europe-PMC influential-paper support contributed": "Literature support contributed",
        "driver-region flags": "driver-region signals",
        "CNA-only": "copy-number-only",
        "subtype-unspecific": "not subtype-definitive",
    }
    for a, b in replacements.items():
        t = t.replace(a, b)
    t = re.sub(r"\s+", " ", t)
    return t


def driver_names_from_data(row: pd.Series, drivers: pd.DataFrame | None = None, limit: int = 8) -> list[str]:
    names: list[str] = []
    if drivers is not None and not drivers.empty:
        for col in ["display", "feature_label", "feature_id", "CNA driver / region"]:
            if col in drivers.columns:
                for x in drivers[col].astype(str).tolist():
                    lab = human_driver_label(x)
                    if lab and lab not in names and lab.lower() != "none detected":
                        names.append(lab)
                if names:
                    break
    if names:
        return names[:limit]
    flags = safe(row.get("driver_region_flags", ""))
    for f in re.split(r"[;,]", flags):
        lab = human_driver_label(f)
        if lab and lab not in names and lab not in {"none detected", "none_detected"}:
            names.append(lab)
    return names[:limit]


def make_interpretation_pairs(pr: pd.Series, row: pd.Series, ks: pd.Series, drivers: pd.DataFrame | None = None) -> list[tuple[str, str]]:
    cls = safe(pr.get("probable_cna_classification", "")) or safe(ks.get("knowledge_refined_class", "")) or safe(row.get("rule_based_cna_class", ""))
    score = safe(pr.get("probable_cna_score", ""))
    probability = safe(pr.get("probable_cna_probability_estimate", ""))
    n_events = safe(row.get("n_cna_events", ""))
    burden = safe(row.get("cna_burden_class", ""))
    altered = safe(row.get("altered_mb", ""))
    rationale = clean_rationale(pr.get("probable_cna_rationale", "")) or clean_rationale(ks.get("knowledge_refined_class_rationale", ""))
    dnames = driver_names_from_data(row, drivers)
    if dnames:
        evidence = "; ".join(dnames)
    else:
        evidence = "No curated driver-region CNA was detected under the current thresholds."
    if not rationale:
        rationale = "The call is based on the CNA burden class, altered genome size, and overlap with the selected CNA driver-region catalog."
    score_text = "The numeric score summarizes CNA burden, driver-region matches, context-restricted classification rules, and literature support when available. It is not a standalone diagnostic probability."
    if score:
        score_text = f"Score {score}" + (f" (probability-like estimate {probability})" if probability else "") + ": " + score_text
    return [
        ("Clinician-readable interpretation", cls or "No probable CNA pattern could be assigned."),
        ("Main copy-number evidence", evidence),
        ("Why this pattern was assigned", rationale),
        ("CNA burden context", f"{n_events or '0'} high-confidence CNA events; {altered or '0'} Mb altered; burden class: {burden or 'not available'}."),
        ("How to use this result", "Use as supportive genomic context for the pathology diagnosis. CNA patterns can prioritize confirmatory tests such as IHC, FISH, karyotype, SNV/SV assays, expression, or methylation profiling when relevant."),
        ("Score meaning", score_text),
    ]


def interpretation_table(pr: pd.Series, row: pd.Series, ks: pd.Series, drivers: pd.DataFrame | None = None) -> Table:
    data = [[Paragraph(f"<b>{esc(k)}</b>", styles["SmallX"]), Paragraph(esc(v), styles["SmallX"])] for k, v in make_interpretation_pairs(pr, row, ks, drivers)]
    t = Table(data, colWidths=[5.0*cm, USABLE - 5.0*cm])
    t.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.22, LINE),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f6fa")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    return t


def html_interpretation_table(pr: pd.Series, row: pd.Series, ks: pd.Series, drivers: pd.DataFrame | None = None) -> str:
    trs = []
    for k, v in make_interpretation_pairs(pr, row, ks, drivers):
        trs.append(f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>")
    return "<table class='interpretation'><tbody>" + "".join(trs) + "</tbody></table>"


def brief_interpretation(pr: pd.Series, row: pd.Series, ks: pd.Series) -> str:
    # Backward-compatible short text for downstream callers; the clinician report now
    # uses make_interpretation_pairs()/interpretation_table() for readability.
    pairs = make_interpretation_pairs(pr, row, ks, None)
    return " ".join(f"{k}: {v}" for k, v in pairs[:3])


def build_pdf(out_pdf: Path, sample: str, row: pd.Series, ss: pd.Series, pr: pd.Series, ks: pd.Series, drivers: pd.DataFrame, papers: pd.DataFrame) -> None:
    doc = SimpleDocTemplate(str(out_pdf), pagesize=A4, leftMargin=LEFT, rightMargin=RIGHT, topMargin=TOP, bottomMargin=BOTTOM, title=f"CNA clinician driver summary - {sample}")
    story = []
    story.append(Paragraph("CNA Driver and Probable Classification Report", styles["Title2"]))
    story.append(raw(f"Sample: <b>{esc(sample)}</b> | Assay: low-pass WGS / SAMURAI CNA codification", "BodyX"))
    story.append(Spacer(1, 5))
    story.append(section("1 - PROBABLE CNA CLASSIFICATION"))
    story.append(kv([
        ("Probable CNA classification", pr.get("probable_cna_classification", "") or ks.get("knowledge_refined_class", "") or row.get("rule_based_cna_class", "")),
        ("Probable CNA score", pr.get("probable_cna_score", "")),
        ("Probability estimate", pr.get("probable_cna_probability_estimate", "")),
        ("Rule-based CNA class", row.get("rule_based_cna_class", "")),
        ("CNA burden class", row.get("cna_burden_class", "")),
        ("N CNA events", row.get("n_cna_events", "")),
        ("Altered genome size (Mb)", row.get("altered_mb", "")),
    ]))
    story.append(Spacer(1, 6))
    story.append(raw("<b>Plain-language interpretation for clinician review</b>", "EmphX"))
    story.append(interpretation_table(pr, row, ks, drivers))
    if safe(pr.get("agreement_call", "")) and safe(pr.get("agreement_call", "")) != "PATHOLOGY_NOT_PROVIDED":
        story.append(Spacer(1, 6)); story.append(section("2 - PATHOLOGY AGREEMENT"))
        story.append(kv([
            ("Agreement call", pr.get("agreement_call", "")),
            ("Agreement score", pr.get("agreement_score", "")),
            ("Reported diagnosis", pr.get("pathology_final_diagnosis", "")),
            ("Why", pr.get("agreement_summary", "")),
            ("Rationale", pr.get("agreement_rationale", "")),
        ]))
    story.append(Spacer(1, 6)); story.append(section("3 - DRIVER CNA REGIONS TO REVIEW"))
    story.append(driver_table(drivers))
    story.append(Spacer(1, 6)); story.append(section("4 - SELECTED INFLUENTIAL LITERATURE"))
    paper_cols = [c for c in ["paper_rank", "feature_display", "influence_score", "pmid", "year", "cited_by_count", "title", "journal", "selection_method"] if c in papers.columns]
    story.append(table_df(papers, columns=paper_cols, max_char=0, tiny=True))
    story.append(Spacer(1, 6)); story.append(section("5 - LIMITATIONS"))
    story.append(p("This clinician-oriented summary is CNA-only. Low-pass WGS can support genome-wide copy-number screening, CNA burden assessment, and driver-region prioritization, but cannot by itself determine SNVs/indels, balanced translocations, gene fusions, methylation class, expression, clonality, or biallelic inactivation. Integrate with histology, IHC, flow cytometry, FISH/karyotype, SNV/SV data, and clinical context."))
    doc.build(story, onFirstPage=lambda canvas, d: header_footer(canvas, d, sample), onLaterPages=lambda canvas, d: header_footer(canvas, d, sample))


def html_table(df: pd.DataFrame, columns: list[str] | None = None) -> str:
    if df is None or df.empty:
        return "<p class='muted'>No rows.</p>"
    d = df.copy()
    if columns:
        d = d[[c for c in columns if c in d.columns]]
    head = "".join(f"<th>{html.escape(c)}</th>" for c in d.columns)
    rows = []
    for _, r in d.iterrows():
        rows.append("<tr>" + "".join(f"<td>{html.escape(safe(r.get(c,'')))}</td>" for c in d.columns) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def html_driver_table(drivers: pd.DataFrame) -> str:
    if drivers is None or drivers.empty:
        return "<p class='muted'>No driver-region CNA rows were detected under the current thresholds.</p>"
    raw_d = drivers.copy()
    d = pd.DataFrame()
    for src in ["display", "feature_label", "feature_id"]:
        if src in raw_d.columns:
            d["CNA driver / region"] = raw_d[src].astype(str).map(human_driver_label)
            break
    if "genes" in raw_d.columns:
        d["Genes"] = raw_d["genes"]
    if "event_state" in raw_d.columns:
        d["Observed CNA"] = raw_d["event_state"]
    if "tier" in raw_d.columns:
        d["Evidence tier"] = raw_d["tier"]
    if "classification_hint" in raw_d.columns:
        d["Why it matters"] = raw_d["classification_hint"]
    elif "feature_label" in raw_d.columns:
        d["Why it matters"] = raw_d["feature_label"]
    if "top_pmids" in raw_d.columns:
        d["PMIDs"] = raw_d["top_pmids"].astype(str).str.replace(";", ", ", regex=False)
    if "mean_log2" in raw_d.columns:
        d["Mean log2"] = raw_d["mean_log2"]
    d = d.loc[:, ~d.columns.duplicated()].copy()
    return html_table(d)


def html_kv(pairs: list[tuple[str, Any]]) -> str:
    return html_table(pd.DataFrame([{"field": k, "value": safe(v)} for k, v in pairs]))


def build_html(out_html: Path, sample: str, row: pd.Series, ss: pd.Series, pr: pd.Series, ks: pd.Series, drivers: pd.DataFrame, papers: pd.DataFrame, pdf_name: str) -> None:
    paper_cols = [c for c in ["paper_rank", "feature_display", "influence_score", "pmid", "year", "cited_by_count", "title", "journal", "selection_method", "url"] if c in papers.columns]
    path_sec = ""
    if safe(pr.get("agreement_call", "")) and safe(pr.get("agreement_call", "")) != "PATHOLOGY_NOT_PROVIDED":
        path_sec = "<section><h2>2 - Pathology agreement</h2>" + html_kv([
            ("Agreement call", pr.get("agreement_call", "")), ("Agreement score", pr.get("agreement_score", "")),
            ("Reported diagnosis", pr.get("pathology_final_diagnosis", "")), ("Why", pr.get("agreement_summary", "")),
            ("Rationale", pr.get("agreement_rationale", "")),
        ]) + "</section>"
    text = f"""<!DOCTYPE html><html><head><meta charset='utf-8'><title>CNA clinician driver summary - {html.escape(sample)}</title>
<style>body{{font-family:Arial,Helvetica,sans-serif;margin:30px;color:#172033;background:#f5f7fb}}main{{max-width:1120px;margin:auto;background:white;border:1px solid #d7dde6;border-radius:14px;padding:24px}}h1{{margin-top:0}}h2{{background:#162033;color:white;padding:10px 12px;border-radius:6px;font-size:18px}}table{{border-collapse:collapse;width:100%;font-size:12px;table-layout:auto}}th,td{{border:1px solid #d7dde6;padding:6px 8px;text-align:left;vertical-align:top;white-space:normal;word-break:normal;overflow-wrap:anywhere}}th{{background:#eef2f7}}table.interpretation th{{width:260px}}.muted{{color:#5f6b7a}}.warning{{background:#fff6cc;border:1px solid #e0b800;padding:12px;margin-top:12px}}.plain{{background:#f8fafc;border:1px solid #d7dde6;padding:12px;border-radius:8px;margin-top:10px}}a{{color:#2f6f9f;text-decoration:none}}</style></head><body><main>
<h1>CNA Driver and Probable Classification Report</h1><p class='muted'>Sample: <b>{html.escape(sample)}</b> | <a href='{html.escape(pdf_name)}'>PDF version</a></p>
<section><h2>1 - Probable CNA classification</h2>{html_kv([
("Probable CNA classification", pr.get("probable_cna_classification", "") or ks.get("knowledge_refined_class", "") or row.get("rule_based_cna_class", "")),
("Probable CNA score", pr.get("probable_cna_score", "")), ("Probability estimate", pr.get("probable_cna_probability_estimate", "")),
("Rule-based CNA class", row.get("rule_based_cna_class", "")), ("CNA burden class", row.get("cna_burden_class", "")),
("N CNA events", row.get("n_cna_events", "")), ("Altered genome size (Mb)", row.get("altered_mb", "")),
])}<div class='plain'><h3>Plain-language interpretation for clinician review</h3>{html_interpretation_table(pr, row, ks, drivers)}</div></section>
{path_sec}
<section><h2>3 - Driver CNA regions to review</h2>{html_driver_table(drivers)}</section>
<section><h2>4 - Selected influential literature</h2>{html_table(papers, columns=paper_cols)}</section>
<section><h2>5 - Limitations</h2><div class='warning'>This clinician-oriented summary is CNA-only. Low-pass WGS supports genome-wide copy-number screening and driver-region prioritization but is not a standalone clinical diagnosis. Integrate with pathology, IHC/flow, FISH/karyotype, SNV/SV data, expression/methylation when available, and clinical context.</div></section>
</main></body></html>"""
    out_html.write_text(text)


def combine_pdfs(paths: list[Path], out_pdf: Path) -> None:
    if PdfReader is None or PdfWriter is None or not paths:
        return
    writer = PdfWriter()
    for pth in paths:
        try:
            reader = PdfReader(str(pth))
            for page in reader.pages:
                writer.add_page(page)
        except Exception:
            continue
    if len(writer.pages):
        with out_pdf.open("wb") as fh:
            writer.write(fh)


def write_index(outdir: Path, rows: list[dict[str, Any]]) -> None:
    pd.DataFrame(rows).to_csv(outdir / "clinician_report_index.tsv", sep="\t", index=False)
    trs = []
    for r in rows:
        trs.append(f"<tr><td>{html.escape(r['sample'])}</td><td><a href='{html.escape(r['html'])}'>HTML</a></td><td><a href='{html.escape(r['pdf'])}'>PDF</a></td><td>{html.escape(safe(r.get('probable_cna_classification','')))}</td><td>{html.escape(safe(r.get('agreement_call','')))}</td><td>{html.escape(safe(r.get('n_drivers','')))}</td></tr>")
    text = """<!DOCTYPE html><html><head><meta charset='utf-8'><title>Clinician CNA driver summaries</title>
<style>body{font-family:Arial,Helvetica,sans-serif;margin:32px;background:#f5f7fb;color:#172033}.panel{background:#fff;border:1px solid #d7dde6;border-radius:14px;padding:22px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #d7dde6;padding:6px 8px;text-align:left}th{background:#eef2f7}a{color:#2f6f9f;text-decoration:none}</style></head><body><div class='panel'><h1>Clinician CNA driver summaries</h1><p><a href='all_sample_clinician_driver_summaries.pdf'>Combined PDF</a></p><table><thead><tr><th>sample</th><th>HTML</th><th>PDF</th><th>probable CNA classification</th><th>pathology agreement</th><th>n drivers</th></tr></thead><tbody>""" + "".join(trs) + "</tbody></table></div></body></html>"
    (outdir / "index.html").write_text(text)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate clinician-oriented CNA driver/probable-classification reports.")
    ap.add_argument("--classification", required=True)
    ap.add_argument("--sample-summary", required=True)
    ap.add_argument("--driver-hits", required=True)
    ap.add_argument("--sample-knowledge", required=True)
    ap.add_argument("--sample-knowledge-summary", required=True)
    ap.add_argument("--sample-literature", required=True)
    ap.add_argument("--pathology-concordance", default="")
    ap.add_argument("--pathology-records", default="")
    ap.add_argument("--outdir", default="clinician_reports")
    ap.add_argument("--max-drivers", type=int, default=14)
    args = ap.parse_args()

    outdir = Path(args.outdir); outdir.mkdir(parents=True, exist_ok=True)
    classification = read_tsv(args.classification)
    sample_summary = read_tsv(args.sample_summary)
    driver_hits = read_tsv(args.driver_hits)
    sample_knowledge = read_tsv(args.sample_knowledge)
    sample_knowledge_summary = read_tsv(args.sample_knowledge_summary)
    sample_literature = read_tsv(args.sample_literature)
    pathology = read_tsv(args.pathology_concordance) if args.pathology_concordance else pd.DataFrame()
    normalize_sample(classification, sample_summary, driver_hits, sample_knowledge, sample_knowledge_summary, sample_literature, pathology)

    if classification.empty or "sample" not in classification.columns:
        (outdir / "index.html").write_text("<html><body><h1>No clinician reports generated</h1></body></html>")
        return

    rows = []; pdfs = []
    for _, row in classification.sort_values("sample").iterrows():
        sample = safe(row.get("sample"))
        slug = slugify(sample)
        ss = get_row(sample_summary, sample)
        pr = get_row(pathology, sample)
        ks = get_row(sample_knowledge_summary, sample)
        drivers = top_drivers(sample, driver_hits, sample_knowledge, max(1, int(args.max_drivers)))
        papers = selected_papers(sample, sample_literature, max_rows=6)
        out_pdf = outdir / f"{slug}_clinical_driver_summary.pdf"
        out_html = outdir / f"{slug}_clinical_driver_summary.html"
        build_pdf(out_pdf, sample, row, ss, pr, ks, drivers, papers)
        build_html(out_html, sample, row, ss, pr, ks, drivers, papers, out_pdf.name)
        pdfs.append(out_pdf)
        rows.append({
            "sample": sample,
            "html": out_html.name,
            "pdf": out_pdf.name,
            "probable_cna_classification": pr.get("probable_cna_classification", "") if not pr.empty else ks.get("knowledge_refined_class", ""),
            "agreement_call": pr.get("agreement_call", "") if not pr.empty else "",
            "n_drivers": len(drivers),
        })
    combine_pdfs(pdfs, outdir / "all_sample_clinician_driver_summaries.pdf")
    write_index(outdir, rows)


if __name__ == "__main__":
    main()
