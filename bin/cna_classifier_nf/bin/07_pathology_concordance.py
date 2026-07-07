#!/usr/bin/env python3
"""Pathology-vs-CNA agreement and CNA-only probable classification.

This reporting-only extension does not change CNA calls, CNA classification rules,
GISTIC parsing, or knowledge enrichment. It always computes a transparent
CNA-only probable classification from the CNA features. If a real pathology table
is provided with --pathology, it additionally computes an explainable token-based
agreement score between the pathology text/IHC/site fields and the CNA-derived
pattern.
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

MISSING = {"", "na", "n/a", "nan", "none", "null", "not reported", "not applicable"}
TOKEN_SCORE_MODEL_VERSION = "OncoTracer local token agreement model v3.0-context-aware"
BIOMED_MODEL_LAYER_VERSION = "OncoTracer optional biomedical transformer agreement layer v1.0"
DEFAULT_BIOMED_MODELS = [
    "microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract",
    "dmis-lab/biobert-base-cased-v1.1",
    "emilyalsentzer/Bio_ClinicalBERT",
]


def safe_str(x: Any) -> str:
    if x is None:
        return ""
    try:
        if pd.isna(x):
            return ""
    except Exception:
        pass
    return str(x).strip()


def num(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        if pd.isna(v):
            return default
        return v
    except Exception:
        return default


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> int:
    return int(max(lo, min(hi, round(x))))


def sigmoid_probability(score: Any, center: float = 55.0, scale: float = 12.0) -> float:
    """Convert a 0-100 score to a probability-like number.

    This is not externally calibrated unless a user-supplied validation table is
    used. It simply makes reports easier to read by mapping larger scores to
    larger probability-like values.
    """
    x = max(0.0, min(100.0, num(score, 0.0)))
    try:
        return round(1.0 / (1.0 + math.exp(-(x - center) / scale)), 3)
    except Exception:
        return 0.0


def score_to_probability_fields(score: Any, calibration_status: str = "heuristic_sigmoid_uncalibrated_no_reference_labels") -> dict[str, Any]:
    return {
        "probability_estimate": sigmoid_probability(score),
        "probability_calibration_status": calibration_status,
        "probability_method": "sigmoid(score; center=55; scale=12) unless a user-supplied labelled calibration set is provided",
    }


def norm_id(x: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", safe_str(x).lower())


def identifier_tokens(x: Any) -> list[str]:
    raw = safe_str(x)
    if not raw:
        return []
    pieces = [raw] + re.split(r"[,;|\s/]+", raw)
    out: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        candidates = [
            piece,
            re.sub(r"(_markdup|_bins|_dedup|_sorted|\.bam|\.bed|\.tsv|\.csv)$", "", piece, flags=re.I),
            re.sub(r"[-_ ]?(markdup|bins|dedup|sorted)$", "", piece, flags=re.I),
        ]
        for c in candidates:
            n = norm_id(c)
            if n and n not in out:
                out.append(n)
    return out


def norm_text(x: Any) -> str:
    s = safe_str(x).lower()
    s = s.replace("non hodgkin", "non-hodgkin")
    s = re.sub(r"\s+", " ", s)
    return s.strip()




def canonical_sample_set(value: Any) -> str:
    raw = safe_str(value) or "pan_cancer"
    head = re.split(r"[:=]", raw, maxsplit=1)[0]
    key = re.sub(r"[^a-z0-9]+", "_", head.lower()).strip("_")
    aliases = {
        "pan": "broad_cancer", "pancancer": "broad_cancer", "pan_cancer": "broad_cancer", "broad": "broad_cancer", "broad_cancer": "broad_cancer", "all": "broad_cancer", "all_cancers": "broad_cancer", "solid": "broad_cancer", "solid_tumor": "broad_cancer", "solid_tumour": "broad_cancer",
        "lymphomas": "lymphoma", "dlbcl": "lymphoma", "b_cell_lymphoma": "lymphoma", "bcell_lymphoma": "lymphoma", "hematolymphoid": "lymphoma",
        "brain": "brain_cns", "cns": "brain_cns", "glioma": "brain_cns", "glioblastoma": "brain_cns", "astrocytoma": "brain_cns", "meningioma": "brain_cns", "pediatric_glioma": "brain_cns",
        "mammary": "breast", "breast_cancer": "breast",
        "pancreatic": "pancreas", "pancreatic_cancer": "pancreas", "pancreatobiliary": "pancreas", "biliary": "pancreas", "cholangiocarcinoma": "pancreas",
        "colon": "colorectal", "crc": "colorectal", "rectal": "colorectal", "rectum": "colorectal",
        "leukaemia": "leukemia", "aml": "leukemia", "acute_lymphoblastic_leukemia": "leukemia", "all_leukemia": "leukemia", "mds": "leukemia", "myeloid": "leukemia", "hematologic": "leukemia", "haematologic": "leukemia",
        "nsclc": "lung", "sclc": "lung", "pulmonary": "lung",
        "prostatic": "prostate",
        "ovary": "ovarian", "fallopian_tube": "ovarian", "peritoneal": "ovarian", "hgsoc": "ovarian",
        "endometrium": "endometrial", "uterine": "endometrial",
        "stomach": "gastric_esophageal", "gastric": "gastric_esophageal", "gastroesophageal": "gastric_esophageal", "gej": "gastric_esophageal", "esophageal": "gastric_esophageal", "oesophageal": "gastric_esophageal",
        "soft_tissue": "sarcoma", "gist": "sarcoma", "liposarcoma": "sarcoma", "leiomyosarcoma": "sarcoma", "osteosarcoma": "sarcoma",
        "kidney": "renal", "rcc": "renal", "clear_cell_rcc": "renal", "ccrcc": "renal",
        "bladder": "urothelial", "urinary_tract": "urothelial",
        "hcc": "liver", "hepatocellular": "liver",
        "hnscc": "head_neck", "oral": "head_neck", "oropharyngeal": "head_neck", "laryngeal": "head_neck",
        "testicular": "germ_cell", "seminoma": "germ_cell", "nonseminoma": "germ_cell",
        "multiple_myeloma": "myeloma", "plasma_cell": "myeloma",
        "net": "neuroendocrine", "neuroendocrine_tumor": "neuroendocrine",
        "pediatric": "pediatric_solid", "paediatric": "pediatric_solid",
    }
    return aliases.get(key, key or "broad_cancer")

def text_tokens(x: Any) -> set[str]:
    t = norm_text(x).replace("b-cell", "b_cell").replace("t-cell", "t_cell")
    raw = re.findall(r"[a-z0-9_]+", t)
    stop = {"and", "or", "of", "the", "with", "without", "not", "no", "by", "to", "in", "for", "a", "an", "variant", "tissue", "sample"}
    return {r for r in raw if len(r) > 1 and r not in stop}


def read_tsv(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    try:
        return pd.read_csv(p, sep="\t")
    except Exception:
        return pd.DataFrame()


def read_pathology_table(path: str | Path) -> pd.DataFrame:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    if p.name == "empty_pathology.tsv":
        return pd.DataFrame()
    suffix = p.suffix.lower()
    try:
        if suffix in {".xlsx", ".xls"}:
            return pd.read_excel(p)
    except Exception as e:
        raise SystemExit(f"Could not read pathology Excel file {p}: {e}")
    errors: list[str] = []
    for enc in ["utf-8-sig", "utf-8", "latin1", "cp1252"]:
        for sep in [None, "\t", ",", ";"]:
            try:
                return pd.read_csv(p, encoding=enc, sep=sep, engine="python")
            except Exception as e:
                errors.append(f"{enc}/{sep}: {e}")
    raise SystemExit("Could not read pathology table. Tried encodings utf-8-sig, utf-8, latin1, cp1252 and common delimiters. Last errors: " + " | ".join(errors[-3:]))


def choose_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    if df is None or len(df.columns) == 0:
        return None
    cmap = {c.lower().strip(): c for c in df.columns}
    for cand in candidates:
        key = cand.lower().strip()
        if key in cmap:
            return cmap[key]
    nmap = {re.sub(r"[^a-z0-9]+", "", c.lower()): c for c in df.columns}
    for cand in candidates:
        key = re.sub(r"[^a-z0-9]+", "", cand.lower())
        if key in nmap:
            return nmap[key]
    return None


def concat_cols(row: pd.Series, cols: list[str | None]) -> str:
    vals: list[str] = []
    for c in cols:
        if c and c in row.index:
            s = safe_str(row.get(c))
            if s and s.lower() not in MISSING:
                vals.append(s)
    return "; ".join(vals)


def positive_marker(text: str, marker: str) -> bool:
    t = norm_text(text)
    m = marker.lower().replace("_", "[-_ ]?")
    patterns = [rf"{m}\s*:\s*[^.;,]*positive", rf"{m}\s+positive", rf"positive\s+[^.;,]*{m}"]
    return any(re.search(p, t) for p in patterns)


def negative_marker(text: str, marker: str) -> bool:
    t = norm_text(text)
    m = marker.lower().replace("_", "[-_ ]?")
    patterns = [rf"{m}\s*:\s*[^.;,]*negative", rf"{m}\s+negative", rf"negative\s+[^.;,]*{m}"]
    return any(re.search(p, t) for p in patterns)


def infer_pathology_profile(path_text: str, marker_text: str) -> dict[str, Any]:
    t = norm_text(path_text + " ; " + marker_text)
    profile: dict[str, Any] = {
        "is_lymphoma": False,
        "is_b_cell": False,
        "is_t_cell": False,
        "is_hodgkin": False,
        "is_dlbcl": False,
        "is_hgbl": False,
        "is_follicular": False,
        "is_mantle": False,
        "is_pmbl": False,
        "is_cll_sll": False,
        "is_leukemia": False,
        "is_glioma": False,
        "is_glioblastoma": False,
        "is_meningioma": False,
        "is_benign_cyst": False,
        "is_carcinoma": False,
        "is_breast": False,
        "is_pancreatic": False,
        "is_colorectal": False,
        "is_lung": False,
        "is_sarcoma": False,
        "is_prostate": False,
        "is_ovarian": False,
        "is_endometrial": False,
        "is_gastric_esophageal": False,
        "is_renal": False,
        "is_urothelial": False,
        "is_thyroid": False,
        "is_melanoma": False,
        "is_liver": False,
        "is_head_neck": False,
        "is_germ_cell": False,
        "is_myeloma": False,
        "is_neuroblastoma": False,
        "is_neuroendocrine": False,
        "lineage": "unknown",
        "subtype": "not inferred",
        "tokens": set(),
        "ihc_highlights": "",
    }
    profile["is_hodgkin"] = "hodgkin" in t and "non-hodgkin" not in t
    profile["is_dlbcl"] = any(x in t for x in ["diffuse large b-cell", "diffuse large b cell", "large b-cell", "large b cell", "dlbcl"])
    profile["is_hgbl"] = any(x in t for x in ["high-grade b-cell", "high grade b-cell", "high-grade b cell", "high grade b cell", "hgbl"])
    profile["is_follicular"] = "follicular lymphoma" in t
    profile["is_mantle"] = "mantle cell" in t
    profile["is_pmbl"] = "primary mediastinal" in t or "thymic large b-cell" in t or "thymic large b cell" in t or "pmbcl" in t
    profile["is_cll_sll"] = "small lymphocytic" in t or "chronic lymphocytic" in t or re.search(r"\bcll\b|\bsll\b", t) is not None
    profile["is_leukemia"] = "leukemia" in t or "leukaemia" in t
    profile["is_lymphoma"] = "lymphoma" in t or profile["is_hodgkin"]
    profile["is_b_cell"] = any(x in t for x in ["b-cell", "b cell", "b-lineage", "b lineage"]) or any(positive_marker(marker_text, m) for m in ["CD20", "CD79a", "PAX5"])
    profile["is_t_cell"] = any(x in t for x in ["t-cell", "t cell", "peripheral t"]) or positive_marker(marker_text, "CD3")
    profile["is_glioblastoma"] = "glioblastoma" in t
    profile["is_glioma"] = profile["is_glioblastoma"] or "glioma" in t or "astrocytoma" in t or positive_marker(marker_text, "GFAP") or positive_marker(marker_text, "OLIG2")
    profile["is_meningioma"] = "meningioma" in t
    profile["is_benign_cyst"] = "arachnoid cyst" in t or "benign cyst" in t or ("cyst" in t and "benign" in t)
    profile["is_carcinoma"] = "carcinoma" in t or positive_marker(marker_text, "Pan-cytokeratin") or positive_marker(marker_text, "GATA3") or positive_marker(marker_text, "cytokeratin")
    profile["is_breast"] = any(x in t for x in ["breast", "mammary", "ductal carcinoma", "lobular carcinoma"]) or positive_marker(marker_text, "GATA3") or positive_marker(marker_text, "ER") or positive_marker(marker_text, "PR") or positive_marker(marker_text, "HER2")
    profile["is_pancreatic"] = any(x in t for x in ["pancreas", "pancreatic", "pancreatobiliary", "ductal adenocarcinoma"])
    profile["is_colorectal"] = any(x in t for x in ["colon", "colonic", "rectal", "rectum", "colorectal", "crc"]) or positive_marker(marker_text, "CDX2") or positive_marker(marker_text, "SATB2")
    profile["is_lung"] = any(x in t for x in ["lung", "pulmonary", "non-small cell", "nsclc"]) or positive_marker(marker_text, "TTF1") or positive_marker(marker_text, "Napsin A")
    profile["is_sarcoma"] = "sarcoma" in t or "liposarcoma" in t or "leiomyosarcoma" in t or "gist" in t
    profile["is_prostate"] = "prostate" in t or "prostatic" in t or positive_marker(marker_text, "PSA") or positive_marker(marker_text, "NKX3.1") or positive_marker(marker_text, "PSAP")
    profile["is_ovarian"] = any(x in t for x in ["ovarian", "ovary", "fallopian", "peritoneal", "serous carcinoma", "hgsoc"]) or positive_marker(marker_text, "PAX8") or positive_marker(marker_text, "WT1")
    profile["is_endometrial"] = any(x in t for x in ["endometrial", "endometrium", "uterine"]) or (positive_marker(marker_text, "PAX8") and positive_marker(marker_text, "ER"))
    profile["is_gastric_esophageal"] = any(x in t for x in ["gastric", "stomach", "gastroesophageal", "gej", "esophageal", "oesophageal"])
    profile["is_renal"] = any(x in t for x in ["renal cell", "kidney", "clear cell renal", "papillary renal", "rcc"]) or positive_marker(marker_text, "PAX8") and positive_marker(marker_text, "CAIX")
    profile["is_urothelial"] = any(x in t for x in ["urothelial", "bladder", "urinary tract"]) or positive_marker(marker_text, "GATA3") and positive_marker(marker_text, "p63")
    profile["is_thyroid"] = "thyroid" in t or positive_marker(marker_text, "TTF1") and positive_marker(marker_text, "thyroglobulin")
    profile["is_melanoma"] = "melanoma" in t or positive_marker(marker_text, "SOX10") or positive_marker(marker_text, "S100") or positive_marker(marker_text, "Melan-A") or positive_marker(marker_text, "HMB45")
    profile["is_liver"] = any(x in t for x in ["hepatocellular", "hcc", "liver carcinoma", "cholangiocarcinoma"]) or positive_marker(marker_text, "HepPar1") or positive_marker(marker_text, "Arginase")
    profile["is_head_neck"] = any(x in t for x in ["head and neck", "oropharyngeal", "oral cavity", "laryngeal", "nasopharyngeal"]) or positive_marker(marker_text, "p16") and positive_marker(marker_text, "p40")
    profile["is_germ_cell"] = any(x in t for x in ["germ cell", "seminoma", "nonseminoma", "embryonal carcinoma", "yolk sac"]) or positive_marker(marker_text, "OCT4") or positive_marker(marker_text, "SALL4")
    profile["is_myeloma"] = any(x in t for x in ["myeloma", "plasma cell neoplasm", "plasmacytoma"]) or positive_marker(marker_text, "CD138")
    profile["is_neuroblastoma"] = "neuroblastoma" in t or positive_marker(marker_text, "PHOX2B") or positive_marker(marker_text, "synaptophysin") and "adrenal" in t
    profile["is_neuroendocrine"] = any(x in t for x in ["neuroendocrine", "carcinoid"]) or (positive_marker(marker_text, "synaptophysin") or positive_marker(marker_text, "chromogranin"))
    if any(profile.get(k) for k in ["is_breast", "is_pancreatic", "is_colorectal", "is_lung", "is_prostate", "is_ovarian", "is_endometrial", "is_gastric_esophageal", "is_renal", "is_urothelial", "is_thyroid", "is_liver", "is_head_neck"]):
        profile["is_carcinoma"] = True

    # Context-aware cleanup for lymphoma pathology reports.
    # In real lymphoma pathology text, markers such as CD3, cytokeratin or GATA3 may appear as
    # background/reactive cells or differential-diagnosis statements. Do not let isolated marker
    # positivity override a lymphoma diagnosis unless the diagnosis text itself clearly states a
    # competing non-lymphoid tumor or a T-cell lymphoma.
    explicit_t_cell_dx = any(x in t for x in [
        "t-cell lymphoma", "t cell lymphoma", "peripheral t-cell", "peripheral t cell",
        "t lymphoblastic", "t-lymphoblastic", "anaplastic large cell", "mycosis fungoides",
        "sezary", "sézary"
    ])
    explicit_non_lymphoid_dx = any(x in t for x in [
        "carcinoma", "adenocarcinoma", "squamous cell carcinoma", "sarcoma", "glioma",
        "glioblastoma", "astrocytoma", "meningioma", "melanoma", "germ cell tumor",
        "seminoma", "neuroblastoma", "neuroendocrine tumor", "hepatocellular carcinoma"
    ])
    if profile["is_lymphoma"]:
        if not explicit_t_cell_dx:
            profile["is_t_cell"] = False
        if not explicit_non_lymphoid_dx:
            profile["is_carcinoma"] = False
            for epithelial_context_key in [
                "is_breast", "is_pancreatic", "is_colorectal", "is_lung", "is_prostate",
                "is_ovarian", "is_endometrial", "is_gastric_esophageal", "is_renal",
                "is_urothelial", "is_thyroid", "is_liver", "is_head_neck", "is_germ_cell",
                "is_neuroblastoma", "is_neuroendocrine", "is_sarcoma", "is_melanoma"
            ]:
                profile[epithelial_context_key] = False
    if profile["is_b_cell"] and not explicit_t_cell_dx:
        profile["is_t_cell"] = False

    if profile["is_hodgkin"]:
        profile["lineage"] = "Hodgkin lymphoma"
        profile["subtype"] = "classic Hodgkin lymphoma" if "classic" in t else "Hodgkin lymphoma"
    elif profile["is_pmbl"]:
        profile["lineage"] = "B-cell lymphoma"
        profile["subtype"] = "primary mediastinal large B-cell lymphoma / high-grade B-cell lymphoma"
    elif profile["is_dlbcl"]:
        profile["lineage"] = "B-cell lymphoma"
        profile["subtype"] = "diffuse large B-cell lymphoma"
    elif profile["is_hgbl"]:
        profile["lineage"] = "B-cell lymphoma"
        profile["subtype"] = "high-grade B-cell lymphoma"
    elif profile["is_mantle"]:
        profile["lineage"] = "B-cell lymphoma"
        profile["subtype"] = "mantle cell lymphoma"
    elif profile["is_follicular"]:
        profile["lineage"] = "B-cell lymphoma"
        profile["subtype"] = "follicular lymphoma"
    elif profile["is_cll_sll"]:
        profile["lineage"] = "B-cell lymphoma"
        profile["subtype"] = "CLL/SLL"
    elif profile["is_lymphoma"] and profile["is_b_cell"]:
        profile["lineage"] = "B-cell lymphoma"
        profile["subtype"] = "B-cell lymphoma, subtype not inferred"
    elif profile["is_lymphoma"] and profile["is_t_cell"]:
        profile["lineage"] = "T-cell lymphoma"
        profile["subtype"] = "T-cell lymphoma"
    elif profile["is_leukemia"]:
        profile["lineage"] = "leukemia / hematologic neoplasm"
        profile["subtype"] = "leukemia"
    elif profile["is_glioblastoma"]:
        profile["lineage"] = "non-lymphoid CNS tumor"
        profile["subtype"] = "glioblastoma / high-grade glioma"
    elif profile["is_glioma"]:
        profile["lineage"] = "non-lymphoid CNS tumor"
        profile["subtype"] = "glioma"
    elif profile["is_meningioma"]:
        profile["lineage"] = "non-lymphoid CNS tumor"
        profile["subtype"] = "meningioma"
    elif profile["is_benign_cyst"]:
        profile["lineage"] = "benign / non-neoplastic or low-neoplastic lesion"
        profile["subtype"] = "benign cyst / arachnoid cyst"
    elif profile["is_breast"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "breast carcinoma-compatible pathology text"
    elif profile["is_pancreatic"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "pancreaticobiliary carcinoma-compatible pathology text"
    elif profile["is_colorectal"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "colorectal carcinoma-compatible pathology text"
    elif profile["is_lung"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "lung carcinoma-compatible pathology text"
    elif profile["is_prostate"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "prostate carcinoma-compatible pathology text"
    elif profile["is_ovarian"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "ovarian/fallopian/peritoneal carcinoma-compatible pathology text"
    elif profile["is_endometrial"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "endometrial/uterine carcinoma-compatible pathology text"
    elif profile["is_gastric_esophageal"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "gastric/esophageal carcinoma-compatible pathology text"
    elif profile["is_renal"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "renal-cell carcinoma-compatible pathology text"
    elif profile["is_urothelial"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "urothelial carcinoma-compatible pathology text"
    elif profile["is_thyroid"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "thyroid carcinoma-compatible pathology text"
    elif profile["is_liver"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "liver/hepatobiliary carcinoma-compatible pathology text"
    elif profile["is_head_neck"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "head-and-neck carcinoma-compatible pathology text"
    elif profile["is_germ_cell"]:
        profile["lineage"] = "germ-cell tumor"
        profile["subtype"] = "germ-cell tumor-compatible pathology text"
    elif profile["is_myeloma"]:
        profile["lineage"] = "plasma-cell neoplasm"
        profile["subtype"] = "myeloma/plasma-cell neoplasm-compatible pathology text"
    elif profile["is_neuroblastoma"]:
        profile["lineage"] = "embryonal / neural-crest tumor"
        profile["subtype"] = "neuroblastoma-compatible pathology text"
    elif profile["is_neuroendocrine"]:
        profile["lineage"] = "neuroendocrine neoplasm"
        profile["subtype"] = "neuroendocrine tumor-compatible pathology text"
    elif profile["is_sarcoma"]:
        profile["lineage"] = "sarcoma / mesenchymal tumor"
        profile["subtype"] = "sarcoma-compatible pathology text"
    elif profile["is_carcinoma"]:
        profile["lineage"] = "carcinoma / epithelial tumor"
        profile["subtype"] = "carcinoma"

    tokens = text_tokens(profile["lineage"] + " " + profile["subtype"])
    if profile["is_lymphoma"]:
        tokens.add("lymphoma")
    if profile["is_b_cell"]:
        tokens.update(["b_cell", "bcell"])
    if profile["is_t_cell"]:
        tokens.add("t_cell")
    for key, tok in [
        ("is_hodgkin", "hodgkin"), ("is_dlbcl", "dlbcl"), ("is_hgbl", "hgbl"),
        ("is_pmbl", "pmbcl"), ("is_follicular", "follicular"), ("is_mantle", "mantle"),
        ("is_cll_sll", "cll_sll"), ("is_glioma", "glioma"), ("is_meningioma", "meningioma"),
        ("is_benign_cyst", "benign"), ("is_carcinoma", "carcinoma"), ("is_breast", "breast"), ("is_pancreatic", "pancreas"), ("is_colorectal", "colorectal"), ("is_lung", "lung"), ("is_prostate", "prostate"), ("is_ovarian", "ovarian"), ("is_endometrial", "endometrial"), ("is_gastric_esophageal", "gastric_esophageal"), ("is_renal", "renal"), ("is_urothelial", "urothelial"), ("is_thyroid", "thyroid"), ("is_melanoma", "melanoma"), ("is_liver", "liver"), ("is_head_neck", "head_neck"), ("is_germ_cell", "germ_cell"), ("is_myeloma", "myeloma"), ("is_neuroblastoma", "neuroblastoma"), ("is_neuroendocrine", "neuroendocrine"), ("is_sarcoma", "sarcoma")
    ]:
        if profile.get(key):
            tokens.add(tok)
    profile["tokens"] = tokens

    highlights = []
    for m in ["CD45", "CD20", "CD79a", "PAX5", "CD3", "CD5", "CD10", "CD15", "CD30", "CD23", "BCL2", "BCL6", "MUM1", "Cyclin D1", "SOX11", "cMYC", "Ki67", "EBV_EBER", "GFAP", "OLIG2", "EMA", "ER", "PR", "HER2", "GATA3", "CDX2", "SATB2", "CK7", "CK20", "TTF1", "Napsin A", "p53", "ATRX", "IDH1_R132H", "PSA", "NKX3.1", "PAX8", "WT1", "CAIX", "p63", "thyroglobulin", "SOX10", "S100", "Melan-A", "HMB45", "HepPar1", "Arginase", "p40", "OCT4", "SALL4", "CD138", "PHOX2B", "synaptophysin", "chromogranin"]:
        mm = m.replace("_", " ")
        if positive_marker(marker_text, m) or positive_marker(marker_text, mm):
            highlights.append(f"{mm} positive")
        elif negative_marker(marker_text, m) or negative_marker(marker_text, mm):
            highlights.append(f"{mm} negative")
    profile["ihc_highlights"] = "; ".join(highlights[:14])
    return profile


def feature_present(text: str, keys: list[str]) -> bool:
    t = norm_text(text).replace("_", " ").replace("-", " ")
    return any(k.lower().replace("_", " ").replace("-", " ") in t for k in keys)


def infer_cna_profile(row: pd.Series, sample_events: pd.DataFrame, sample_driver_hits: pd.DataFrame, ks_row: pd.Series | None = None) -> dict[str, Any]:
    """Infer a pan-cancer CNA semantic profile from the catalog hits and events."""
    fields = [row.get("driver_region_flags", ""), row.get("rule_based_cna_class", ""), row.get("cna_burden_class", ""), row.get("gain_loss_direction_class", "")]
    if ks_row is not None and not ks_row.empty:
        fields += [ks_row.get("knowledge_refined_class", ""), ks_row.get("knowledge_refined_class_rationale", "")]
    if not sample_driver_hits.empty:
        for c in ["feature_id", "feature_label", "genes", "event_cytoband"]:
            if c in sample_driver_hits.columns:
                fields += sample_driver_hits[c].astype(str).tolist()
    # Event cytobands add broad context, but feature-specific calls are driven mostly by driver-region hits.
    if not sample_events.empty:
        for c in ["state", "chrom", "cytoband", "cna_shorthand", "molecular_piece"]:
            if c in sample_events.columns:
                fields += sample_events[c].astype(str).tolist()
    text = " ; ".join(fields)
    n = int(num(row.get("n_cna_events", 0), 0))
    altered = num(row.get("altered_mb", 0), 0)
    burden = safe_str(row.get("cna_burden_class", ""))

    flags = {
        "1q_gain": feature_present(text, ["1q_gain", "1q gain", "gain(1)(q"]),
        "1p_loss": feature_present(text, ["1p_loss", "1p loss", "del(1)(p"]),
        "2p16_REL_BCL11A": feature_present(text, ["2p16_REL_BCL11A", "REL/BCL11A", "REL BCL11A"]),
        "2p24_MYCN": feature_present(text, ["2p24_MYCN", "MYCN"]),
        "3p_loss": feature_present(text, ["3p_loss", "3p loss", "del(3)(p"]),
        "3q26_PIK3CA_SOX2_TERC": feature_present(text, ["3q26_PIK3CA", "PIK3CA/SOX2", "SOX2/TERC"]),
        "3q27_BCL6": feature_present(text, ["3q27_BCL6", "BCL6"]),
        "4q12_KIT_PDGFRA_KDR": feature_present(text, ["4q12_KIT", "KIT/PDGFRA", "PDGFRA/KDR"]),
        "4q_loss": feature_present(text, ["4q_loss", "4q loss", "del(4)(q"]),
        "5p15_TERT": feature_present(text, ["5p15_TERT", "TERT"]),
        "5q_gain_RCC": feature_present(text, ["5q_gain_RCC", "5q gain", "gain(5)(q"]),
        "5q22_APC_loss": feature_present(text, ["5q22_APC", "APC"]),
        "5q_loss_MDS_AML": feature_present(text, ["5q_loss_MDS_AML", "5q loss", "del(5)(q"]),
        "6p21_HLA": feature_present(text, ["6p21_HLA", "HLA-region", "HLA"]),
        "6q_PRDM1_TNFAIP3": feature_present(text, ["6q_loss_PRDM1_TNFAIP3", "6q21_PRDM1", "6q23_TNFAIP3", "PRDM1", "TNFAIP3"]),
        "7_gain": feature_present(text, ["chr7 gain", "chromosome 7 gain", "7_gain", "gain(7)"]),
        "7q_loss": feature_present(text, ["7q_loss", "7q loss", "del(7)(q"]),
        "7p11_EGFR": feature_present(text, ["7p11_EGFR", "EGFR"]),
        "7q31_MET": feature_present(text, ["7q31_MET", "MET"]),
        "7q34_BRAF_KIAA1549": feature_present(text, ["7q34_BRAF", "KIAA1549", "BRAF"]),
        "8p_loss": feature_present(text, ["8p_loss", "8p loss", "del(8)(p"]),
        "8q_gain": feature_present(text, ["8q_gain", "8q gain", "gain(8)(q"]),
        "8q24_MYC": feature_present(text, ["8q24_MYC", "MYC"]),
        "9p24_JAK2_PDL1_PDL2": feature_present(text, ["9p24_JAK2", "PD-L1", "PDL1", "PDCD1LG2", "CD274"]),
        "9p21_CDKN2A_B": feature_present(text, ["9p21_CDKN2A", "CDKN2A/CDKN2B", "CDKN2A", "CDKN2B"]),
        "9q_loss_bladder": feature_present(text, ["9q_loss_bladder", "9q loss", "del(9)(q"]),
        "10_loss": feature_present(text, ["10_loss_GBM_context", "chr10_loss", "chromosome 10 loss"]),
        "10q23_PTEN": feature_present(text, ["10q23_PTEN", "PTEN"]),
        "10q26_FGFR2": feature_present(text, ["10q26_FGFR2", "FGFR2"]),
        "11q13_CCND1_FGF": feature_present(text, ["11q13_CCND1", "CCND1/FGF", "CCND1"]),
        "11q_loss": feature_present(text, ["11q_loss", "11q loss", "del(11)(q"]),
        "12p_gain_germ_cell": feature_present(text, ["12p_gain_germ_cell", "12p gain", "gain(12)(p"]),
        "12p13_ETV6": feature_present(text, ["12p13_ETV6", "ETV6"]),
        "12q15_MDM2_CDK4": feature_present(text, ["12q15_MDM2", "MDM2/CDK4", "MDM2", "CDK4"]),
        "13q14": feature_present(text, ["13q14_loss", "13q14_RB1", "RB1", "13q14"]),
        "13q_gain": feature_present(text, ["13q_gain", "chromosome 13 gain", "gain(13)"]),
        "14q13_NKX2_1": feature_present(text, ["14q13_NKX2", "NKX2-1", "NKX2_1"]),
        "14q_loss_RCC": feature_present(text, ["14q_loss_RCC", "14q loss", "del(14)(q"]),
        "15q21_B2M": feature_present(text, ["15q21_B2M", "B2M"]),
        "16q_loss": feature_present(text, ["16q_loss", "16q loss", "del(16)(q"]),
        "16p13_CIITA": feature_present(text, ["16p13_CIITA", "CIITA"]),
        "17p13_TP53": feature_present(text, ["17p13_TP53", "TP53"]),
        "17q12_ERBB2": feature_present(text, ["17q12_ERBB2", "ERBB2", "HER2"]),
        "17q_gain_neuroblastoma": feature_present(text, ["17q_gain_neuroblastoma", "17q gain", "gain(17)(q"]),
        "18q21_BCL2_MALT1": feature_present(text, ["18q21_BCL2", "BCL2/MALT1", "BCL2", "MALT1"]),
        "18q_loss_SMAD4_DCC": feature_present(text, ["18q_loss_SMAD4", "SMAD4/DCC", "SMAD4", "DCC"]),
        "19p13_STK11_KEAP1": feature_present(text, ["19p13_STK11", "STK11", "KEAP1"]),
        "19q12_CCNE1": feature_present(text, ["19q12_CCNE1", "CCNE1"]),
        "19q_gain": feature_present(text, ["19q_gain", "19q gain", "gain(19)(q"]),
        "20q_gain": feature_present(text, ["20q_gain", "20q gain", "gain(20)"]),
        "21q_RUNX1": feature_present(text, ["21q_RUNX1", "RUNX1"]),
        "22q_loss": feature_present(text, ["22q_loss", "22q loss", "del(22)"]),
        "Xq12_AR": feature_present(text, ["Xq12_AR", "AR-region", "AR gain"]),
        "Xp11_TFE3": feature_present(text, ["Xp11_TFE3", "TFE3"]),
    }

    lymphoma_specific = ["2p16_REL_BCL11A", "18q21_BCL2_MALT1", "3q27_BCL6", "9p24_JAK2_PDL1_PDL2", "6q_PRDM1_TNFAIP3"]
    lymphoma_score = sum(1 for k in lymphoma_specific if flags[k])
    strong_bcell_features = [k for k in lymphoma_specific if flags[k]]
    cns_glioma_features = [k for k in ["7p11_EGFR", "7_gain", "10_loss", "10q23_PTEN", "9p21_CDKN2A_B", "17p13_TP53"] if flags[k]]
    meningioma_features = [k for k in ["22q_loss", "1p_loss"] if flags[k]]
    cll_features = [k for k in ["13q14", "11q_loss", "17p13_TP53", "12q15_MDM2_CDK4"] if flags[k]]
    breast_features = [k for k in ["17q12_ERBB2", "11q13_CCND1_FGF", "1q_gain", "16q_loss", "8q24_MYC", "8q_gain", "17p13_TP53", "3q26_PIK3CA_SOX2_TERC"] if flags[k]]
    colorectal_features = [k for k in ["20q_gain", "18q_loss_SMAD4_DCC", "17p13_TP53", "8q24_MYC", "8q_gain", "13q_gain", "8p_loss", "5q22_APC_loss"] if flags[k]]
    pancreatic_features = [k for k in ["9p21_CDKN2A_B", "17p13_TP53", "18q_loss_SMAD4_DCC", "8q24_MYC", "20q_gain", "10q26_FGFR2", "7q31_MET"] if flags[k]]
    leukemia_features = [k for k in ["5q_loss_MDS_AML", "7q_loss", "12p13_ETV6", "21q_RUNX1", "13q14", "17p13_TP53"] if flags[k]]
    lung_features = [k for k in ["3q26_PIK3CA_SOX2_TERC", "7p11_EGFR", "7q31_MET", "14q13_NKX2_1", "9p21_CDKN2A_B", "19p13_STK11_KEAP1", "8q24_MYC"] if flags[k]]
    prostate_features = [k for k in ["8q24_MYC", "8q_gain", "8p_loss", "10q23_PTEN", "13q14", "17p13_TP53", "Xq12_AR"] if flags[k]]
    ovarian_features = [k for k in ["19q12_CCNE1", "8q24_MYC", "8q_gain", "3q26_PIK3CA_SOX2_TERC", "17p13_TP53", "20q_gain"] if flags[k]]
    gastric_features = [k for k in ["17q12_ERBB2", "7q31_MET", "10q26_FGFR2", "19q12_CCNE1", "8q24_MYC", "20q_gain"] if flags[k]]
    renal_features = [k for k in ["3p_loss", "5q_gain_RCC", "9p21_CDKN2A_B", "14q_loss_RCC", "17p13_TP53"] if flags[k]]
    urothelial_features = [k for k in ["9p21_CDKN2A_B", "9q_loss_bladder", "8q24_MYC", "11q13_CCND1_FGF", "17p13_TP53"] if flags[k]]
    sarcoma_features = [k for k in ["12q15_MDM2_CDK4", "4q12_KIT_PDGFRA_KDR", "13q14", "17p13_TP53"] if flags[k]]
    melanoma_features = [k for k in ["9p21_CDKN2A_B", "10q23_PTEN", "11q13_CCND1_FGF", "8q24_MYC"] if flags[k]]
    liver_features = [k for k in ["1q_gain", "8q24_MYC", "8q_gain", "8p_loss", "4q_loss", "17p13_TP53"] if flags[k]]
    germ_cell_features = [k for k in ["12p_gain_germ_cell", "8q24_MYC", "1p_loss"] if flags[k]]
    neuroblastoma_features = [k for k in ["2p24_MYCN", "1p_loss", "11q_loss", "17q_gain_neuroblastoma"] if flags[k]]
    myeloma_features = [k for k in ["1q_gain", "1p_loss", "13q14", "17p13_TP53", "11q13_CCND1_FGF"] if flags[k]]
    endometrial_features = [k for k in ["10q23_PTEN", "17p13_TP53", "3q26_PIK3CA_SOX2_TERC", "8q24_MYC", "19q12_CCNE1", "1q_gain"] if flags[k]]
    thyroid_features = [k for k in ["7q34_BRAF_KIAA1549", "5p15_TERT", "1q_gain", "7p11_EGFR"] if flags[k]]
    head_neck_features = [k for k in ["3q26_PIK3CA_SOX2_TERC", "11q13_CCND1_FGF", "9p21_CDKN2A_B", "8q24_MYC", "5p15_TERT", "17p13_TP53"] if flags[k]]
    neuroendocrine_features = [k for k in ["8q24_MYC", "9p21_CDKN2A_B", "17p13_TP53", "11q13_CCND1_FGF", "20q_gain"] if flags[k]]
    pediatric_solid_features = list(dict.fromkeys(neuroblastoma_features + cns_glioma_features + germ_cell_features + [k for k in ["2p24_MYCN", "7q34_BRAF_KIAA1549"] if flags[k]]))
    solid_features = [k for k in ["17q12_ERBB2", "7p11_EGFR", "7q31_MET", "10q26_FGFR2", "11q13_CCND1_FGF", "19q12_CCNE1", "20q_gain", "18q_loss_SMAD4_DCC", "3q26_PIK3CA_SOX2_TERC", "8q24_MYC", "17p13_TP53", "9p21_CDKN2A_B", "2p24_MYCN", "12p_gain_germ_cell", "Xq12_AR"] if flags[k]]

    tokens = set()
    if lymphoma_score:
        tokens.update(["lymphoma", "b_cell", "bcell"])
    if strong_bcell_features:
        tokens.update(["large_b_cell", "dlbcl", "hgbl"])
    if flags["9p24_JAK2_PDL1_PDL2"]:
        tokens.update(["hodgkin", "pmbcl", "immune_evasion"])
    if flags["18q21_BCL2_MALT1"]:
        tokens.update(["follicular", "germinal_center", "bcl2"])
    if flags["3q27_BCL6"]:
        tokens.update(["follicular", "germinal_center", "bcl6"])
    if flags["17p13_TP53"]:
        tokens.update(["tp53_axis", "aggressive", "chromosomal_instability"])
    if flags["17q12_ERBB2"]:
        tokens.update(["breast", "gastric", "carcinoma", "her2", "erbb2"])
    if flags["11q13_CCND1_FGF"] or flags["16q_loss"] or flags["1q_gain"]:
        tokens.update(["breast", "solid_tumor"])
    if flags["20q_gain"] or flags["13q_gain"] or flags["18q_loss_SMAD4_DCC"]:
        tokens.update(["colorectal", "colon", "carcinoma", "chromosomal_instability"])
    if flags["18q_loss_SMAD4_DCC"] and flags["9p21_CDKN2A_B"]:
        tokens.update(["pancreas", "pancreatic", "carcinoma"])
    if flags["7p11_EGFR"] or flags["10_loss"] or flags["10q23_PTEN"]:
        tokens.add("glioma")
    if flags["22q_loss"]:
        tokens.add("meningioma")
    if flags["13q14"] or flags["11q_loss"]:
        tokens.add("cll_sll")
    if lung_features:
        tokens.update(["lung", "nsclc", "carcinoma"])
    if prostate_features:
        tokens.update(["prostate", "carcinoma"])
    if ovarian_features:
        tokens.update(["ovarian", "serous", "carcinoma"])
    if gastric_features:
        tokens.update(["gastric_esophageal", "carcinoma"])
    if renal_features:
        tokens.update(["renal", "rcc", "carcinoma"])
    if urothelial_features:
        tokens.update(["urothelial", "bladder", "carcinoma"])
    if sarcoma_features:
        tokens.update(["sarcoma", "gist", "mesenchymal"])
    if melanoma_features:
        tokens.update(["melanoma"])
    if liver_features:
        tokens.update(["liver", "hcc", "carcinoma"])
    if germ_cell_features:
        tokens.update(["germ_cell", "testicular"])
    if neuroblastoma_features:
        tokens.update(["neuroblastoma", "embryonal"])
    if myeloma_features:
        tokens.update(["myeloma", "plasma_cell"])
    if endometrial_features:
        tokens.update(["endometrial", "uterine", "carcinoma"])
    if thyroid_features:
        tokens.update(["thyroid", "carcinoma"])
    if head_neck_features:
        tokens.update(["head_neck", "hnscc", "carcinoma"])
    if neuroendocrine_features:
        tokens.update(["neuroendocrine", "carcinoma"])
    if pediatric_solid_features:
        tokens.update(["pediatric_solid"])
    if leukemia_features:
        tokens.update(["leukemia", "aml", "mds", "hematologic"])

    flat_or_low = n == 0 or "flat" in burden.lower() or (n <= 5 and altered <= 25 and len(solid_features) == 0 and lymphoma_score == 0 and len(leukemia_features) == 0 and len(neuroblastoma_features) == 0 and len(germ_cell_features) == 0)
    cna_high = (not flat_or_low) and (n >= 20 or "high_complex" in burden.lower() or "ultracomplex" in burden.lower() or "ultra" in burden.lower())
    return {
        "flags": flags,
        "tokens": tokens,
        "n_cna_events": n,
        "altered_mb": altered,
        "burden": burden,
        "lymphoma_score": lymphoma_score,
        "strong_bcell_features": strong_bcell_features,
        "cns_glioma_features": cns_glioma_features,
        "meningioma_features": meningioma_features,
        "cll_features": cll_features,
        "breast_features": breast_features,
        "colorectal_features": colorectal_features,
        "pancreatic_features": pancreatic_features,
        "leukemia_features": leukemia_features,
        "lung_features": lung_features,
        "prostate_features": prostate_features,
        "ovarian_features": ovarian_features,
        "gastric_features": gastric_features,
        "renal_features": renal_features,
        "urothelial_features": urothelial_features,
        "sarcoma_features": sarcoma_features,
        "melanoma_features": melanoma_features,
        "liver_features": liver_features,
        "germ_cell_features": germ_cell_features,
        "neuroblastoma_features": neuroblastoma_features,
        "myeloma_features": myeloma_features,
        "endometrial_features": endometrial_features,
        "thyroid_features": thyroid_features,
        "head_neck_features": head_neck_features,
        "neuroendocrine_features": neuroendocrine_features,
        "pediatric_solid_features": pediatric_solid_features,
        "solid_features": solid_features,
        "supporting_feature_text": "; ".join(strong_bcell_features + cns_glioma_features + meningioma_features + cll_features + breast_features + colorectal_features + pancreatic_features + leukemia_features + lung_features + prostate_features + ovarian_features + gastric_features + renal_features + urothelial_features + sarcoma_features + melanoma_features + liver_features + germ_cell_features + neuroblastoma_features + myeloma_features + endometrial_features + thyroid_features + head_neck_features + neuroendocrine_features + pediatric_solid_features),
        "is_flat_or_low": flat_or_low,
        "is_cna_high": cna_high,
    }

def cna_probable_classification(cna_profile: dict[str, Any], row: pd.Series, ks_row: pd.Series | None = None, sample_set: str = "broad_cancer") -> dict[str, Any]:
    """Assign a probable CNA-pattern class.

    In v8 the class space is context-aware.  When --sample_set is a specific
    context such as lymphoma, breast, pancreas, etc., the function does not emit
    unrelated disease labels even when generic pan-cancer regions overlap.  The
    broad all-context behavior is used only for --sample_set broad_cancer or
    synonyms such as pan_cancer/all.
    """
    flags = cna_profile["flags"]
    n = cna_profile["n_cna_events"]
    altered = cna_profile["altered_mb"]
    burden = cna_profile["burden"]
    knowledge = safe_str(ks_row.get("knowledge_refined_class", "")) if ks_row is not None and not ks_row.empty else ""

    base = 10 if n > 0 else 0
    if cna_profile["is_flat_or_low"]:
        burden_component = 0
    elif n >= 100 or "ultracomplex" in burden.lower() or "ultra" in burden.lower():
        burden_component = 20
    elif n >= 50 or "high_complex" in burden.lower():
        burden_component = 16
    elif n >= 10 or altered >= 100:
        burden_component = 10
    elif n > 0:
        burden_component = 5
    else:
        burden_component = 0

    feature_groups = {
        "lymphoma": cna_profile.get("strong_bcell_features", []),
        "breast": cna_profile.get("breast_features", []),
        "colorectal": cna_profile.get("colorectal_features", []),
        "pancreas": cna_profile.get("pancreatic_features", []),
        "leukemia": cna_profile.get("leukemia_features", []),
        "brain_cns": cna_profile.get("cns_glioma_features", []) + cna_profile.get("meningioma_features", []),
        "lung": cna_profile.get("lung_features", []),
        "prostate": cna_profile.get("prostate_features", []),
        "ovarian": cna_profile.get("ovarian_features", []),
        "endometrial": cna_profile.get("endometrial_features", []),
        "gastric_esophageal": cna_profile.get("gastric_features", []),
        "renal": cna_profile.get("renal_features", []),
        "urothelial": cna_profile.get("urothelial_features", []),
        "sarcoma": cna_profile.get("sarcoma_features", []),
        "melanoma": cna_profile.get("melanoma_features", []),
        "liver": cna_profile.get("liver_features", []),
        "thyroid": cna_profile.get("thyroid_features", []),
        "head_neck": cna_profile.get("head_neck_features", []),
        "germ_cell": cna_profile.get("germ_cell_features", []),
        "neuroblastoma": cna_profile.get("neuroblastoma_features", []),
        "myeloma": cna_profile.get("myeloma_features", []),
        "neuroendocrine": cna_profile.get("neuroendocrine_features", []),
        "pediatric_solid": cna_profile.get("pediatric_solid_features", []),
    }
    context = canonical_sample_set(sample_set)
    broad_mode = context in {"broad_cancer", "pan_cancer", "all", "all_cancers"}
    context_features = feature_groups.get(context, [])
    max_group = max((len(v) for v in feature_groups.values()), default=0)

    if broad_mode:
        canonical_component = min(36, 7 * max_group + 2 * max(0, len(cna_profile.get("solid_features", [])) - max_group))
    else:
        # Specific sample_set runs should not score strongly just because unrelated
        # pan-cancer regions are present.  Use the current context plus generic
        # complexity only.
        canonical_component = min(30, 8 * len(context_features))
        if len(context_features) == 0 and cna_profile["is_cna_high"]:
            canonical_component = 6

    pattern_component = 0
    class_label = "CNA-flat / no high-confidence driver CNA"
    class_tokens: set[str] = set()
    rationale: list[str] = []

    def generic_context_fallback(label_prefix: str, tokens: list[str], min_features: int = 1, component: int = 18) -> bool:
        nonlocal class_label, pattern_component
        if len(context_features) >= min_features:
            class_label = label_prefix
            class_tokens.update(tokens + ["sample_set_context"])
            pattern_component = component
            rationale.append(f"--sample_set {context} was supplied, so interpretation was restricted to that context. Compatible CNA features detected: " + ", ".join(context_features[:8]) + ".")
            return True
        return False

    if cna_profile["is_flat_or_low"]:
        class_label = "CNA-flat or low-CNA pattern - not subtype-definitive"
        class_tokens.update(["flat", "not_assessable", context])
        pattern_component = 5 if n > 0 else 0
        rationale.append("The CNA profile is flat/low under current thresholds; this can occur with low tumor content, low CNA burden, or tumors driven by SNVs/fusions/methylation rather than broad CNA.")
    elif context == "lymphoma" and not broad_mode:
        # Restricted lymphoma mode: no breast/CNS/pan-cancer subtype labels.
        if flags["9p24_JAK2_PDL1_PDL2"] and flags["2p16_REL_BCL11A"]:
            class_label = "Lymphoma-context Hodgkin/PMBCL-compatible immune-evasion CNA pattern"
            class_tokens.update(["lymphoma", "hodgkin", "pmbcl", "b_cell", "immune_evasion", "sample_set_context"])
            pattern_component = 24
            rationale.append("--sample_set lymphoma was supplied. 9p24/JAK2-PD-L1/PD-L2-region and 2p16/REL-BCL11A-region CNA signals support a lymphoma immune-evasion/PMBCL-Hodgkin-like CNA context, not a non-lymphoma class.")
        elif flags["17p13_TP53"] and (cna_profile["is_cna_high"] or len(cna_profile.get("strong_bcell_features", [])) >= 2):
            class_label = "Lymphoma-context CNA-high / TP53-axis candidate pattern"
            class_tokens.update(["lymphoma", "b_cell", "tp53_axis", "chromosomal_instability", "sample_set_context"])
            pattern_component = 23
            rationale.append("--sample_set lymphoma was supplied. CNA complexity plus 17p13/TP53-region loss supports a lymphoma CNA-high/TP53-axis candidate pattern; TP53 mutation status remains unknown from CNA-only data.")
        elif len(cna_profile.get("strong_bcell_features", [])) >= 2 or (flags["2p16_REL_BCL11A"] and (flags["18q21_BCL2_MALT1"] or flags["8q24_MYC"] or flags["3q27_BCL6"])):
            class_label = "Lymphoma-context B-cell / high-grade B-cell lymphoma-like CNA pattern"
            class_tokens.update(["lymphoma", "b_cell", "large_b_cell", "dlbcl", "hgbl", "sample_set_context"])
            pattern_component = 24
            rationale.append("--sample_set lymphoma was supplied. Multiple lymphoma-associated CNA features were detected: " + ", ".join(cna_profile.get("strong_bcell_features", [])[:8]) + ".")
        elif len(cna_profile.get("strong_bcell_features", [])) >= 1:
            class_label = "Lymphoma-context CNA pattern, subtype-unspecific"
            class_tokens.update(["lymphoma", "b_cell", "sample_set_context"])
            pattern_component = 16
            rationale.append("--sample_set lymphoma was supplied. At least one lymphoma-associated CNA feature was detected: " + ", ".join(cna_profile.get("strong_bcell_features", [])[:5]) + ".")
        elif cna_profile["is_cna_high"]:
            class_label = "Lymphoma-context CNA-high pattern, driver-subtype-unspecified"
            class_tokens.update(["lymphoma", "complex_cna", "sample_set_context"])
            pattern_component = 13
            rationale.append("--sample_set lymphoma was supplied. The sample is CNA-high/complex, but the current CNA catalog did not detect strong lymphoma-specific driver-region combinations. This remains lymphoma-context because the run was restricted by --sample_set.")
        elif "gain-dominant" in safe_str(row.get("gain_loss_direction_class", "")).lower():
            class_label = "Lymphoma-context gain-dominant CNA pattern, subtype-unspecified"
            class_tokens.update(["lymphoma", "gain_dominant", "sample_set_context"])
            pattern_component = 10
            rationale.append("--sample_set lymphoma was supplied. The CNA profile is gain-dominant but lacks a specific lymphoma driver-region combination in the current catalog.")
        else:
            class_label = "Lymphoma-context CNA pattern, subtype-unspecified"
            class_tokens.update(["lymphoma", "complex_cna", "sample_set_context"])
            pattern_component = 9
            rationale.append("--sample_set lymphoma was supplied. CNA abnormalities are present, but no highly specific lymphoma CNA pattern was detected.")
    elif not broad_mode:
        # Other restricted contexts.  Only emit the requested cancer-context label
        # or a non-subtype-definitive context label.
        context_label_map = {
            "breast": ("Breast carcinoma-context CNA pattern", ["breast", "carcinoma"], 2, 22),
            "pancreas": ("Pancreaticobiliary carcinoma-context CNA pattern", ["pancreas", "pancreatic", "carcinoma"], 2, 22),
            "colorectal": ("Colorectal carcinoma-context chromosomal-instability CNA pattern", ["colorectal", "colon", "carcinoma", "chromosomal_instability"], 2, 22),
            "leukemia": ("Leukemia/MDS-context CNA pattern", ["leukemia", "aml", "mds", "hematologic"], 1, 22),
            "brain_cns": ("Brain/CNS tumor-context CNA pattern", ["brain_cns", "glioma", "meningioma"], 1, 22),
            "lung": ("Lung carcinoma-context CNA pattern", ["lung", "nsclc", "carcinoma"], 2, 21),
            "prostate": ("Prostate carcinoma-context CNA pattern", ["prostate", "carcinoma"], 2, 21),
            "ovarian": ("Ovarian/fallopian/peritoneal carcinoma-context CNA pattern", ["ovarian", "serous", "carcinoma"], 2, 21),
            "endometrial": ("Endometrial/uterine carcinoma-context CNA pattern", ["endometrial", "uterine", "carcinoma"], 2, 21),
            "gastric_esophageal": ("Gastric/esophageal carcinoma-context CNA pattern", ["gastric_esophageal", "carcinoma"], 2, 21),
            "liver": ("Liver/HCC-context CNA pattern", ["liver", "hcc", "carcinoma"], 2, 21),
            "head_neck": ("Head-and-neck carcinoma-context CNA pattern", ["head_neck", "hnscc", "carcinoma"], 2, 21),
            "renal": ("Renal-cell carcinoma-context CNA pattern", ["renal", "rcc", "carcinoma"], 2, 21),
            "urothelial": ("Urothelial carcinoma-context CNA pattern", ["urothelial", "bladder", "carcinoma"], 2, 21),
            "sarcoma": ("Sarcoma/GIST-context CNA pattern", ["sarcoma", "mesenchymal"], 1, 20),
            "germ_cell": ("Germ-cell tumor-context CNA pattern", ["germ_cell", "testicular"], 1, 21),
            "neuroblastoma": ("Neuroblastoma-context CNA pattern", ["neuroblastoma", "embryonal"], 1, 21),
            "myeloma": ("Myeloma/plasma-cell neoplasm-context CNA pattern", ["myeloma", "plasma_cell"], 1, 20),
            "melanoma": ("Melanoma-context CNA pattern", ["melanoma"], 1, 18),
            "thyroid": ("Thyroid carcinoma-context CNA pattern", ["thyroid", "carcinoma"], 1, 18),
            "neuroendocrine": ("Neuroendocrine neoplasm-context CNA pattern", ["neuroendocrine"], 1, 18),
            "pediatric_solid": ("Pediatric solid tumor-context CNA pattern", ["pediatric_solid"], 1, 18),
        }
        label, toks, minf, comp = context_label_map.get(context, (f"{context} context CNA pattern", [context], 1, 16))
        if not generic_context_fallback(label, toks, min_features=minf, component=comp):
            if cna_profile["is_cna_high"]:
                class_label = f"{context} context CNA-high pattern, subtype-unspecified"
                pattern_component = 12
                rationale.append(f"--sample_set {context} was supplied. CNA burden is high/complex, but the current catalog did not detect enough context-specific driver-region features; unrelated tumor-type labels were intentionally suppressed.")
            elif "gain-dominant" in safe_str(row.get("gain_loss_direction_class", "")).lower():
                class_label = f"{context} context gain-dominant CNA pattern, subtype-unspecified"
                pattern_component = 9
                rationale.append(f"--sample_set {context} was supplied. The profile is gain-dominant but not subtype-definitive in this context.")
            else:
                class_label = f"{context} context CNA pattern, subtype-unspecified"
                pattern_component = 8
                rationale.append(f"--sample_set {context} was supplied. CNA abnormalities are present but context-specific driver-region evidence is limited; unrelated tumor-type labels were suppressed.")
            class_tokens.update([context, "sample_set_context", "neoplastic"])
    else:
        # Broad cancer mode: allow all tumor-type patterns to compete.
        if flags["17q12_ERBB2"]:
            class_label = "HER2/ERBB2-amplified carcinoma-compatible CNA pattern"
            class_tokens.update(["carcinoma", "breast", "gastric", "her2", "erbb2"])
            pattern_component = 24
            rationale.append("17q12 ERBB2/HER2-region gain/amplification was detected; in broad_cancer mode this is allowed to map to a HER2-amplified carcinoma-compatible CNA context.")
        elif flags["7p11_EGFR"] or (flags["7_gain"] and flags["10_loss"]):
            class_label = "EGFR/chr7/chr10 CNS glioma-compatible CNA pattern"
            class_tokens.update(["glioma", "cns", "egfr", "chromosome_7_gain", "chromosome_10_loss"])
            pattern_component = 23
            rationale.append("EGFR-region gain/amplification or chromosome 7 gain with chromosome 10 loss was detected; this supports a glioma-like CNA context when pathology/site is compatible.")
        elif len(cna_profile.get("leukemia_features", [])) >= 2:
            class_label = "Leukemia/MDS-compatible CNA pattern"
            class_tokens.update(["leukemia", "aml", "mds", "hematologic"])
            pattern_component = 22
            rationale.append("Leukemia/MDS-associated CNA features were detected: " + ", ".join(cna_profile.get("leukemia_features", [])[:8]) + ".")
        elif len(cna_profile.get("neuroblastoma_features", [])) >= 2:
            class_label = "Neuroblastoma-compatible CNA pattern"
            class_tokens.update(["neuroblastoma", "embryonal"])
            pattern_component = 22
            rationale.append("Neuroblastoma-associated CNA features were detected: " + ", ".join(cna_profile.get("neuroblastoma_features", [])[:8]) + ".")
        elif len(cna_profile.get("germ_cell_features", [])) >= 1:
            class_label = "Germ-cell tumor-compatible 12p/oncogene CNA pattern"
            class_tokens.update(["germ_cell", "testicular"])
            pattern_component = 21
            rationale.append("Germ-cell tumor-associated CNA features were detected: " + ", ".join(cna_profile.get("germ_cell_features", [])[:8]) + ".")
        elif len(cna_profile.get("ovarian_features", [])) >= 3:
            class_label = "Ovarian/serous carcinoma-compatible CNA pattern"
            class_tokens.update(["ovarian", "serous", "carcinoma"])
            pattern_component = 21
            rationale.append("Ovarian/serous carcinoma-associated CNA features were detected: " + ", ".join(cna_profile.get("ovarian_features", [])[:8]) + ".")
        elif len(cna_profile.get("prostate_features", [])) >= 3:
            class_label = "Prostate carcinoma-compatible CNA pattern"
            class_tokens.update(["prostate", "carcinoma"])
            pattern_component = 20
            rationale.append("Prostate-associated CNA features were detected: " + ", ".join(cna_profile.get("prostate_features", [])[:8]) + ".")
        elif len(cna_profile.get("lung_features", [])) >= 3:
            class_label = "Lung carcinoma-compatible CNA pattern"
            class_tokens.update(["lung", "nsclc", "carcinoma"])
            pattern_component = 20
            rationale.append("Lung carcinoma-associated CNA features were detected: " + ", ".join(cna_profile.get("lung_features", [])[:8]) + ".")
        elif len(cna_profile.get("gastric_features", [])) >= 3:
            class_label = "Gastric/esophageal carcinoma-compatible CNA pattern"
            class_tokens.update(["gastric_esophageal", "carcinoma"])
            pattern_component = 20
            rationale.append("Gastric/esophageal carcinoma-associated CNA features were detected: " + ", ".join(cna_profile.get("gastric_features", [])[:8]) + ".")
        elif len(cna_profile.get("renal_features", [])) >= 2:
            class_label = "Renal-cell carcinoma-compatible CNA pattern"
            class_tokens.update(["renal", "rcc", "carcinoma"])
            pattern_component = 20
            rationale.append("Renal-cell carcinoma-associated CNA features were detected: " + ", ".join(cna_profile.get("renal_features", [])[:8]) + ".")
        elif len(cna_profile.get("urothelial_features", [])) >= 2:
            class_label = "Urothelial carcinoma-compatible CNA pattern"
            class_tokens.update(["urothelial", "bladder", "carcinoma"])
            pattern_component = 20
            rationale.append("Urothelial carcinoma-associated CNA features were detected: " + ", ".join(cna_profile.get("urothelial_features", [])[:8]) + ".")
        elif len(cna_profile.get("colorectal_features", [])) >= 3:
            class_label = "Colorectal-like chromosomal-instability CNA pattern"
            class_tokens.update(["colorectal", "colon", "carcinoma", "chromosomal_instability"])
            pattern_component = 21
            rationale.append("Colorectal-like CIN features were detected: " + ", ".join(cna_profile.get("colorectal_features", [])[:8]) + ".")
        elif len(cna_profile.get("pancreatic_features", [])) >= 3:
            class_label = "Pancreaticobiliary/colorectal tumor-suppressor-loss CNA pattern"
            class_tokens.update(["pancreas", "pancreatic", "carcinoma", "tumor_suppressor_loss"])
            pattern_component = 20
            rationale.append("Pancreaticobiliary/colorectal tumor-suppressor-loss CNA features were detected: " + ", ".join(cna_profile.get("pancreatic_features", [])[:8]) + ".")
        elif len(cna_profile.get("breast_features", [])) >= 3:
            class_label = "Breast/epithelial carcinoma-compatible CNA pattern"
            class_tokens.update(["breast", "carcinoma", "solid_tumor"])
            pattern_component = 19
            rationale.append("Breast/epithelial carcinoma-associated CNA features were detected: " + ", ".join(cna_profile.get("breast_features", [])[:8]) + ".")
        elif flags["9p24_JAK2_PDL1_PDL2"] and flags["2p16_REL_BCL11A"]:
            class_label = "Hodgkin/PMBCL-compatible immune-evasion and 2p CNA pattern"
            class_tokens.update(["lymphoma", "hodgkin", "pmbcl", "b_cell", "immune_evasion"])
            pattern_component = 20
            rationale.append("Both 9p24/JAK2-PD-L1/PD-L2-region and 2p16/REL-BCL11A-region CNA signals were detected.")
        elif flags["17p13_TP53"] and (cna_profile["is_cna_high"] or len(cna_profile.get("strong_bcell_features", [])) >= 2):
            class_label = "TP53-axis / chromosomal-instability CNA pattern"
            class_tokens.update(["tp53_axis", "chromosomal_instability", "aggressive"])
            pattern_component = 19
            rationale.append("17p13/TP53-region loss is present with high/complex CNA burden or multiple driver-region CNA features.")
        elif len(cna_profile.get("strong_bcell_features", [])) >= 2 or (flags["2p16_REL_BCL11A"] and (flags["18q21_BCL2_MALT1"] or flags["8q24_MYC"] or flags["3q27_BCL6"])):
            class_label = "B-cell lymphoma / high-grade B-cell lymphoma-like CNA pattern"
            class_tokens.update(["lymphoma", "b_cell", "large_b_cell", "dlbcl", "hgbl"])
            pattern_component = 18
            rationale.append("Multiple lymphoma-associated CNA features were detected: " + ", ".join(cna_profile.get("strong_bcell_features", [])[:8]) + ".")
        elif flags["22q_loss"] and flags["1p_loss"] and len(cna_profile.get("strong_bcell_features", [])) == 0:
            class_label = "Meningioma-compatible broad CNA pattern"
            class_tokens.update(["meningioma", "non_lymphoid", "cns"])
            pattern_component = 16
            rationale.append("22q loss and 1p loss are present without strong lymphoma-specific CNA features.")
        elif len(cna_profile.get("solid_features", [])) >= 2:
            class_label = "Solid-tumor oncogene/tumor-suppressor CNA pattern, subtype-unspecific"
            class_tokens.update(["solid_tumor", "carcinoma", "neoplastic"])
            pattern_component = 14
            rationale.append("Multiple pan-cancer solid-tumor CNA features were detected: " + ", ".join(cna_profile.get("solid_features", [])[:8]) + ".")
        elif len(cna_profile.get("strong_bcell_features", [])) >= 1:
            class_label = "Lymphoma-compatible CNA pattern, subtype-unspecific"
            class_tokens.update(["lymphoma", "b_cell"])
            pattern_component = 12
            rationale.append("At least one lymphoma-associated CNA feature was detected: " + ", ".join(cna_profile.get("strong_bcell_features", [])[:5]) + ".")
        elif "gain-dominant" in safe_str(row.get("gain_loss_direction_class", "")).lower():
            class_label = "Gain-dominant CNA pattern, tumor-type-unspecific"
            class_tokens.update(["neoplastic", "gain_dominant"])
            pattern_component = 9
            rationale.append("The profile is gain-dominant but lacks a specific canonical tumor-type CNA combination.")
        else:
            class_label = "Complex CNA pattern, tumor-type-unspecific"
            class_tokens.update(["neoplastic", "complex_cna"])
            pattern_component = 8
            rationale.append("The sample has CNA abnormalities but no highly specific tumor-type CNA pattern in the current catalog.")

    literature_component = 0
    if ks_row is not None and not ks_row.empty:
        literature_component = int(min(8, max(0, num(ks_row.get("knowledge_literature_strength", 0), 0))))
    if knowledge:
        # Use knowledge as supportive text, but do not let pan-cancer knowledge override a restricted sample_set label.
        if broad_mode or context in knowledge.lower().replace("-", "_") or context == "lymphoma":
            rationale.append("Knowledge-enrichment label: " + knowledge + ".")
    if literature_component > 0:
        rationale.append(f"PubMed/Europe-PMC influential-paper support contributed {literature_component} points because selected context-relevant references were found for detected CNA drivers; this supports interpretability but is not clinical validation.")

    penalty = 0
    if cna_profile["is_flat_or_low"]:
        penalty -= 5
    if class_label.startswith("CNA-flat"):
        canonical_component = min(canonical_component, 4)

    total = clamp(base + burden_component + canonical_component + pattern_component + literature_component + penalty)
    breakdown = {
        "base_informative_cna": base,
        "cna_burden": burden_component,
        "context_restricted": 0 if broad_mode else 1,
        "canonical_driver_tokens": canonical_component,
        "pattern_specificity": pattern_component,
        "pubmed_influential_literature_support": literature_component,
        "penalties": penalty,
        "total": total,
    }
    prob_fields = score_to_probability_fields(total)

    if broad_mode:
        token_source = class_tokens | set(cna_profile.get("tokens", set()))
    else:
        # Keep tokens context-restricted; this prevents lymphoma runs from
        # inheriting CNS/carcinoma tokens just because chr7/10 or ERBB2-like regions
        # were present.
        context_token_map = {
            "lymphoma": {"lymphoma"},
            "brain_cns": {"brain_cns", "cns"},
            "breast": {"breast"},
            "pancreas": {"pancreas", "pancreatic"},
            "colorectal": {"colorectal", "colon"},
            "leukemia": {"leukemia", "hematologic"},
        }
        token_source = class_tokens | context_token_map.get(context, {context})

    return {
        "probable_cna_classification": class_label,
        "probable_cna_score": total,
        "probable_cna_probability_estimate": prob_fields["probability_estimate"],
        "probable_cna_probability_calibration_status": prob_fields["probability_calibration_status"],
        "probable_cna_probability_method": prob_fields["probability_method"],
        "sample_set_context": context,
        "probable_cna_rationale": " ".join(rationale),
        "probable_cna_score_breakdown": "; ".join([f"{k}={v}" for k, v in breakdown.items()]),
        "probable_cna_tokens": ";".join(sorted(token_source)),
        "probable_cna_model": TOKEN_SCORE_MODEL_VERSION + f" - CNA-only context-aware pattern score using --sample_set {context}. Cross-cancer tumor-type labels are allowed only with --sample_set broad_cancer/pan_cancer. The separate probability estimate is calibrated only when a labelled calibration table is supplied.",
    }

def marker_summary_from_row(row: pd.Series) -> str:
    text_col = choose_col(pd.DataFrame(columns=row.index), ["marker_results_standardized", "markers", "ihc", "immunohistochemistry"])
    text = safe_str(row.get(text_col)) if text_col else ""
    if text:
        return text
    markers = []
    for c in row.index:
        if not str(c).lower().startswith("marker_"):
            continue
        v = row.get(c)
        try:
            f = float(v)
        except Exception:
            continue
        if pd.isna(f):
            continue
        name = str(c).replace("marker_", "").replace("_", " ")
        if f <= 0:
            markers.append(f"{name}: negative")
        elif f >= 2:
            markers.append(f"{name}: positive")
        elif f > 0:
            markers.append(f"{name}: weak/focal positive")
    return "; ".join(markers)


def token_overlap_component(path_tokens: set[str], cna_tokens: set[str]) -> tuple[int, str]:
    if not path_tokens:
        return 0, "no_pathology_tokens"
    overlap = sorted(path_tokens & cna_tokens)
    # Give partial credit for broad lineage token matches plus exact subtype tokens.
    denom = max(3, min(8, len(path_tokens)))
    component = clamp((len(overlap) / denom) * 25, 0, 25)
    return component, ",".join(overlap) if overlap else "none"


def ihc_component(path_profile: dict[str, Any], marker_text: str) -> tuple[int, str]:
    positives = []
    if path_profile.get("is_b_cell") and any(positive_marker(marker_text, m) for m in ["CD20", "CD79a", "PAX5"]):
        positives.append("B-cell IHC token match")
    if path_profile.get("is_hodgkin") and any(positive_marker(marker_text, m) for m in ["CD15", "CD30"]):
        positives.append("Hodgkin IHC token match")
    if path_profile.get("is_t_cell") and positive_marker(marker_text, "CD3"):
        positives.append("T-cell IHC token match")
    if path_profile.get("is_glioma") and any(positive_marker(marker_text, m) for m in ["GFAP", "OLIG2"]):
        positives.append("glial IHC token match")
    if path_profile.get("is_meningioma") and any(positive_marker(marker_text, m) for m in ["EMA", "PR"]):
        positives.append("meningioma IHC token match")
    if positives:
        return min(10, 5 + 2 * len(positives)), "; ".join(positives)
    return 0, "no IHC token contribution or IHC unavailable"


def agreement_call_from_score(total: int, path_profile: dict[str, Any], penalties: int = 0) -> str:
    if path_profile.get("is_lymphoma") or path_profile.get("is_b_cell") or path_profile.get("is_hodgkin"):
        if total >= 80:
            return "AGREEMENT"
        if total >= 58:
            return "PARTIAL_AGREEMENT"
        if total < 40 and penalties < 0:
            return "DISAGREEMENT_REVIEW"
        return "NOT_ASSESSABLE"
    if total >= 78:
        return "AGREEMENT_NON_LYMPHOMA"
    if total >= 55:
        return "PARTIAL_AGREEMENT_NON_LYMPHOMA"
    if total < 40 and penalties < 0:
        return "DISAGREEMENT_REVIEW"
    return "NOT_ASSESSABLE"


def agreement_summary_from_call(call: str, used_biomed: bool = False) -> str:
    prefix = "Token + biomedical-transformer semantic scoring" if used_biomed else "Token-based scoring"
    if call == "AGREEMENT":
        return f"{prefix} supports the reported pathology with CNA features that are mutually compatible."
    if call == "PARTIAL_AGREEMENT":
        return f"{prefix} finds broad compatibility, but the CNA data are not subtype-definitive."
    if call == "AGREEMENT_NON_LYMPHOMA":
        return f"{prefix} supports compatibility with the reported non-lymphoid/non-lymphoma pathology; this is not a standalone tumor subtype call."
    if call == "PARTIAL_AGREEMENT_NON_LYMPHOMA":
        return f"{prefix} supports a neoplastic or broad non-lymphoid-compatible pattern, but not a definitive lineage/subtype."
    if call == "DISAGREEMENT_REVIEW":
        return f"{prefix} suggests potential discordance or sample/pathology review because penalties outweigh supportive evidence."
    return f"{prefix} does not provide enough CNA/pathology compatibility evidence for a confident agreement call."


def build_cna_semantic_text(cna_profile: dict[str, Any], row: pd.Series, probable: dict[str, Any]) -> str:
    parts = [
        "CNA molecular pattern summary",
        safe_str(probable.get("probable_cna_classification", "")),
        safe_str(probable.get("probable_cna_rationale", "")),
        "tokens " + safe_str(probable.get("probable_cna_tokens", "")),
        "rule class " + safe_str(row.get("rule_based_cna_class", "")),
        "burden " + safe_str(row.get("cna_burden_class", "")),
        "driver flags " + safe_str(row.get("driver_region_flags", "")),
        "features " + safe_str(cna_profile.get("supporting_feature_text", "")),
        "number of CNA events " + safe_str(cna_profile.get("n_cna_events", "")),
        "altered megabases " + safe_str(cna_profile.get("altered_mb", "")),
    ]
    return ". ".join([p for p in parts if p and p.lower() != "nan"])


def parse_model_list(text: Any) -> list[str]:
    raw = safe_str(text)
    if not raw:
        return list(DEFAULT_BIOMED_MODELS)
    parts = [x.strip() for x in re.split(r"[,;]+", raw) if x.strip()]
    return parts or list(DEFAULT_BIOMED_MODELS)


_MODEL_CACHE: dict[str, Any] = {}


def _load_transformer_model(model_name: str, local_files_only: bool = False):
    if model_name in _MODEL_CACHE:
        return _MODEL_CACHE[model_name]
    from transformers import AutoModel, AutoTokenizer  # type: ignore
    tokenizer = AutoTokenizer.from_pretrained(model_name, local_files_only=local_files_only)
    model = AutoModel.from_pretrained(model_name, local_files_only=local_files_only)
    model.eval()
    _MODEL_CACHE[model_name] = (tokenizer, model)
    return tokenizer, model


def _embed_text(model_name: str, text: str, local_files_only: bool, max_tokens: int):
    import torch  # type: ignore
    tokenizer, model = _load_transformer_model(model_name, local_files_only=local_files_only)
    encoded = tokenizer(text, return_tensors="pt", truncation=True, max_length=max_tokens)
    with torch.no_grad():
        out = model(**encoded)
    hidden = out.last_hidden_state
    mask = encoded.get("attention_mask")
    if mask is None:
        return hidden.mean(dim=1).squeeze(0)
    mask = mask.unsqueeze(-1).expand(hidden.size()).float()
    return (hidden * mask).sum(dim=1).squeeze(0) / mask.sum(dim=1).clamp(min=1e-9).squeeze(0)


def run_biomed_model_trials(
    sample: str,
    path_text: str,
    cna_text: str,
    enable: bool,
    model_names: list[str],
    local_files_only: bool = False,
    max_tokens: int = 256,
) -> tuple[list[dict[str, Any]], float | None, str]:
    rows: list[dict[str, Any]] = []
    if not enable:
        return rows, None, "disabled"
    try:
        import torch  # noqa: F401  # type: ignore
        import transformers  # noqa: F401  # type: ignore
    except Exception as e:
        msg = f"transformers_or_torch_unavailable: {type(e).__name__}: {e}"
        for model_name in model_names:
            rows.append({"sample": sample, "model_name": model_name, "model_layer": BIOMED_MODEL_LAYER_VERSION, "status": "not_available", "semantic_similarity": "", "semantic_score": "", "message": msg})
        return rows, None, msg
    scores: list[float] = []
    for model_name in model_names:
        try:
            import torch  # type: ignore
            pvec = _embed_text(model_name, path_text, local_files_only, max_tokens)
            cvec = _embed_text(model_name, cna_text, local_files_only, max_tokens)
            sim = torch.nn.functional.cosine_similarity(pvec, cvec, dim=0).item()
            # Conservative mapping. Biomedical BERT embeddings often have a high
            # baseline similarity for medical text, so 0.35 is treated as neutral.
            score = max(0.0, min(100.0, 50.0 + 50.0 * ((sim - 0.35) / 0.65)))
            score = round(score, 2)
            scores.append(score)
            rows.append({"sample": sample, "model_name": model_name, "model_layer": BIOMED_MODEL_LAYER_VERSION, "status": "completed", "semantic_similarity": round(sim, 5), "semantic_score": score, "message": "pathology_text_vs_CNA_evidence_text_mean_pooled_embedding_similarity"})
        except Exception as e:
            rows.append({"sample": sample, "model_name": model_name, "model_layer": BIOMED_MODEL_LAYER_VERSION, "status": "failed", "semantic_similarity": "", "semantic_score": "", "message": f"{type(e).__name__}: {str(e)[:240]}"})
    if scores:
        return rows, round(float(sum(scores) / len(scores)), 2), "completed"
    return rows, None, "no_model_completed"


def apply_biomed_consensus_to_assessment(assess: dict[str, Any], consensus: float | None, trials: list[dict[str, Any]], path_profile: dict[str, Any]) -> dict[str, Any]:
    token_score = int(num(assess.get("agreement_score", 0)))
    completed = [r for r in trials if safe_str(r.get("status")) == "completed"]
    assess["agreement_score_token_only"] = token_score
    assess["agreement_biomed_consensus_score"] = "" if consensus is None else consensus
    assess["agreement_biomed_model_scores"] = "; ".join([f"{r.get('model_name')}={r.get('semantic_score')}" for r in completed])
    assess["agreement_biomed_model_status"] = "completed" if completed else (safe_str(trials[0].get("message")) if trials else "disabled_or_not_run")
    if consensus is None:
        prob = score_to_probability_fields(token_score)
        assess["agreement_probability_estimate"] = prob["probability_estimate"]
        assess["agreement_probability_calibration_status"] = prob["probability_calibration_status"]
        assess["agreement_probability_method"] = prob["probability_method"]
        assess["agreement_score_final_source"] = "token_only"
        return assess
    final = clamp(0.70 * token_score + 0.30 * float(consensus))
    penalties = 0
    m = re.search(r"penalties=(-?\d+)", safe_str(assess.get("agreement_score_breakdown", "")))
    if m:
        penalties = int(m.group(1))
    call = agreement_call_from_score(final, path_profile, penalties=penalties)
    assess["agreement_score"] = final
    assess["agreement_call"] = call
    assess["agreement_summary"] = agreement_summary_from_call(call, used_biomed=True)
    assess["agreement_score_breakdown"] = safe_str(assess.get("agreement_score_breakdown", "")) + f"; token_only_total={token_score}; biomedical_transformer_consensus={consensus}; final_weighted_score=0.70*token_only+0.30*biomed={final}"
    assess["agreement_score_model"] = TOKEN_SCORE_MODEL_VERSION + " + " + BIOMED_MODEL_LAYER_VERSION + " using three optional biomedical transformer models"
    prob = score_to_probability_fields(final)
    assess["agreement_probability_estimate"] = prob["probability_estimate"]
    assess["agreement_probability_calibration_status"] = prob["probability_calibration_status"]
    assess["agreement_probability_method"] = prob["probability_method"]
    assess["agreement_score_final_source"] = "token70_biomedical_transformer30"
    return assess


def assess_agreement_token_model(path_profile: dict[str, Any], cna_profile: dict[str, Any], row: pd.Series, path_text: str, marker_text: str, probable: dict[str, Any]) -> dict[str, Any]:
    path_tokens = set(path_profile.get("tokens", set()))
    probable_tokens = {t for t in safe_str(probable.get("probable_cna_tokens", "")).split(";") if t}
    probable_context = safe_str(probable.get("sample_set_context", "broad_cancer"))
    if probable_context in {"broad_cancer", "pan_cancer", "all", "all_cancers"}:
        cna_tokens = set(cna_profile.get("tokens", set())) | probable_tokens
    else:
        cna_tokens = probable_tokens
    flags = cna_profile["flags"]
    strong = cna_profile["strong_bcell_features"]
    support: list[str] = []
    caution: list[str] = []
    components: dict[str, int] = {}

    components["base_matched_pathology"] = 20
    token_component, token_overlap = token_overlap_component(path_tokens, cna_tokens)
    components["pathology_CNA_token_overlap"] = token_component
    if token_overlap != "none":
        support.append(f"Shared pathology/CNA tokens: {token_overlap}.")
    else:
        caution.append("No direct pathology/CNA class-token overlap was found.")

    # CNA biomarker support. This is deliberately numeric and auditable.
    biomarker = 0
    if path_profile["is_lymphoma"] or path_profile["is_b_cell"] or path_profile["is_hodgkin"]:
        strong = cna_profile.get("strong_bcell_features", [])
        biomarker = min(25, 7 * len(strong) + 2 * max(0, cna_profile.get("lymphoma_score", 0) - len(strong)))
        if strong:
            support.append("Lymphoma/B-cell CNA tokens detected: " + ", ".join(strong[:10]) + ".")
        else:
            caution.append("No strong lymphoma/B-cell CNA token was detected by the current catalog.")
        if path_profile["is_hodgkin"] and (flags.get("9p24_JAK2_PDL1_PDL2") or flags.get("2p16_REL_BCL11A")):
            biomarker = max(biomarker, 16)
            support.append("Hodgkin-compatible 9p24 or 2p16/REL-region CNA evidence is present.")
        if path_profile["is_follicular"] and (flags.get("18q21_BCL2_MALT1") or flags.get("3q27_BCL6") or flags.get("1q_gain")):
            biomarker = max(biomarker, 17)
            support.append("Follicular/germinal-center-compatible CNA tokens are present.")
        if path_profile["is_cll_sll"] and cna_profile.get("cll_features"):
            biomarker = max(biomarker, 17)
            support.append("CLL/SLL-compatible CNA tokens are present: " + ", ".join(cna_profile.get("cll_features", [])) + ".")
    elif path_profile.get("is_breast"):
        feats = cna_profile.get("breast_features", [])
        biomarker = min(25, 7 * len(feats))
        if flags.get("17q12_ERBB2"):
            biomarker = max(biomarker, 22)
        if feats:
            support.append("Breast/epithelial carcinoma-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_colorectal"):
        feats = cna_profile.get("colorectal_features", [])
        biomarker = min(25, 7 * len(feats))
        if feats:
            support.append("Colorectal-like chromosomal-instability CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_pancreatic"):
        feats = cna_profile.get("pancreatic_features", [])
        biomarker = min(25, 7 * len(feats))
        if feats:
            support.append("Pancreaticobiliary-compatible tumor-suppressor CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_lung"):
        feats = cna_profile.get("lung_features", [])
        biomarker = min(25, 7 * len(feats))
        if feats:
            support.append("Lung carcinoma-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_prostate"):
        feats = cna_profile.get("prostate_features", [])
        biomarker = min(25, 7 * len(feats))
        if feats:
            support.append("Prostate carcinoma-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_ovarian") or path_profile.get("is_endometrial"):
        feats = cna_profile.get("ovarian_features", []) + cna_profile.get("solid_features", [])[:2]
        biomarker = min(25, 6 * len(set(feats)))
        if feats:
            support.append("Gynecologic carcinoma-compatible CNA tokens are present: " + ", ".join(list(dict.fromkeys(feats))[:10]) + ".")
    elif path_profile.get("is_gastric_esophageal"):
        feats = cna_profile.get("gastric_features", [])
        biomarker = min(25, 7 * len(feats))
        if feats:
            support.append("Gastric/esophageal carcinoma-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_renal"):
        feats = cna_profile.get("renal_features", [])
        biomarker = min(25, 8 * len(feats))
        if feats:
            support.append("Renal-cell carcinoma-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_urothelial"):
        feats = cna_profile.get("urothelial_features", [])
        biomarker = min(25, 7 * len(feats))
        if feats:
            support.append("Urothelial carcinoma-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_sarcoma"):
        feats = cna_profile.get("sarcoma_features", [])
        biomarker = min(25, 8 * len(feats))
        if feats:
            support.append("Sarcoma/GIST-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_melanoma"):
        feats = cna_profile.get("melanoma_features", [])
        biomarker = min(25, 7 * len(feats))
        if feats:
            support.append("Melanoma-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_liver"):
        feats = cna_profile.get("liver_features", [])
        biomarker = min(25, 7 * len(feats))
        if feats:
            support.append("Liver/HCC-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_germ_cell"):
        feats = cna_profile.get("germ_cell_features", [])
        biomarker = min(25, 10 * len(feats))
        if feats:
            support.append("Germ-cell tumor-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_myeloma"):
        feats = cna_profile.get("myeloma_features", [])
        biomarker = min(25, 7 * len(feats))
        if feats:
            support.append("Myeloma/plasma-cell neoplasm-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_neuroblastoma"):
        feats = cna_profile.get("neuroblastoma_features", [])
        biomarker = min(25, 9 * len(feats))
        if feats:
            support.append("Neuroblastoma-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_neuroendocrine"):
        feats = cna_profile.get("solid_features", [])
        biomarker = min(18, 4 * len(feats)) if feats else (8 if cna_profile["is_cna_high"] else 0)
        if feats:
            support.append("Neuroendocrine-context pan-cancer CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile.get("is_leukemia"):
        feats = cna_profile.get("leukemia_features", [])
        biomarker = min(25, 8 * len(feats))
        if feats:
            support.append("Leukemia/MDS-compatible CNA tokens are present: " + ", ".join(feats[:10]) + ".")
    elif path_profile["is_meningioma"]:
        biomarker = min(20, 10 * len(cna_profile.get("meningioma_features", [])))
        if cna_profile.get("meningioma_features"):
            support.append("Meningioma-compatible CNA tokens are present: " + ", ".join(cna_profile.get("meningioma_features", [])) + ".")
    elif path_profile["is_glioma"]:
        biomarker = min(25, 7 * len(cna_profile.get("cns_glioma_features", [])))
        if cna_profile.get("cns_glioma_features"):
            support.append("Glioma/CNS-tumor-compatible CNA tokens are present: " + ", ".join(cna_profile.get("cns_glioma_features", [])) + ".")
    elif path_profile["is_benign_cyst"]:
        biomarker = 45 if cna_profile["is_flat_or_low"] else 0
        if cna_profile["is_flat_or_low"]:
            support.append("Benign/non-neoplastic pathology is compatible with a CNA-flat or low-CNA profile.")
    elif path_profile["is_carcinoma"]:
        feats = cna_profile.get("solid_features", [])
        biomarker = min(20, 5 * len(feats)) if feats else (8 if cna_profile["is_cna_high"] else 0)
        if feats:
            support.append("Pan-cancer solid-tumor CNA tokens are present: " + ", ".join(feats[:10]) + ".")
        elif biomarker:
            support.append("A complex CNA profile supports a neoplastic process, although it is not carcinoma-subtype specific.")
    components["diagnosis_specific_CNA_biomarkers"] = biomarker

    if cna_profile["is_flat_or_low"]:
        burden_component = 10 if path_profile["is_benign_cyst"] else (4 if path_profile["is_hodgkin"] else 0)
    elif cna_profile["is_cna_high"]:
        burden_component = 10 if not path_profile["is_benign_cyst"] else -8
    else:
        burden_component = 5
    components["CNA_burden_context"] = int(burden_component)

    ihc, ihc_reason = ihc_component(path_profile, marker_text)
    components["IHC_token_support"] = ihc
    if ihc:
        support.append(ihc_reason + ".")

    penalties = 0
    if path_profile["is_benign_cyst"] and cna_profile["is_cna_high"]:
        penalties -= 25
        caution.append("High/complex CNA burden is unexpected for a benign cyst/non-neoplastic pathology label.")
    if (not path_profile.get("is_lymphoma")) and (path_profile["is_glioma"] or path_profile["is_meningioma"] or path_profile["is_carcinoma"] or path_profile.get("is_sarcoma")) and cna_profile.get("strong_bcell_features"):
        penalties -= 20
        caution.append("Pathology is non-lymphoid, but lymphoma-associated CNA tokens are present.")
    if (path_profile["is_lymphoma"] or path_profile["is_b_cell"]) and (not strong) and (not cna_profile["is_flat_or_low"]):
        # In a lymphoma-restricted run, absence of a cataloged lymphoma driver CNA is weak/
        # non-informative rather than discordant: low-pass WGS often captures broad CNA burden
        # without SNVs, rearrangements, methylation, RNA, or IHC-definitive markers. Penalize
        # only in broad_cancer mode where all lineages are competing simultaneously.
        if probable_context in {"broad_cancer", "pan_cancer", "all", "all_cancers"}:
            penalties -= 10
        caution.append("Reported lymphoma lacks strong matching lymphoma-associated CNA tokens despite a non-flat CNA profile; in a lymphoma-restricted run this is treated as non-definitive rather than discordant.")
    if path_profile["is_t_cell"] and not (path_profile.get("is_b_cell") or path_profile.get("is_hodgkin")) and strong:
        penalties -= 10
        caution.append("T-cell pathology is not well supported by B-cell lymphoma CNA tokens.")
    components["penalties"] = penalties

    total = clamp(sum(components.values()))
    call = agreement_call_from_score(total, path_profile, penalties=penalties)
    summary = agreement_summary_from_call(call, used_biomed=False)

    breakdown = "; ".join([f"{k}={v}" for k, v in components.items()] + [f"total={total}"])
    prob_fields = score_to_probability_fields(total)
    rationale = " ".join(support + caution).strip()
    if not rationale:
        rationale = "The score is driven by token overlap and CNA burden components, but no highly specific supporting feature was available."
    return {
        "agreement_call": call,
        "agreement_score": total,
        "agreement_summary": summary,
        "agreement_rationale": rationale,
        "supporting_evidence": "; ".join(support),
        "cautionary_evidence": "; ".join(caution),
        "agreement_score_breakdown": breakdown,
        "agreement_score_model": TOKEN_SCORE_MODEL_VERSION,
        "agreement_score_token_only": total,
        "agreement_score_final_source": "token_only",
        "agreement_probability_estimate": prob_fields["probability_estimate"],
        "agreement_probability_calibration_status": prob_fields["probability_calibration_status"],
        "agreement_probability_method": prob_fields["probability_method"],
        "agreement_biomed_consensus_score": "",
        "agreement_biomed_model_scores": "",
        "agreement_biomed_model_status": "not_run",
        "agreement_token_overlap": token_overlap,
        "agreement_pathology_tokens": ";".join(sorted(path_tokens)),
        "agreement_cna_tokens": ";".join(sorted(cna_tokens)),
    }


def build_probability_calibrator(table_path: str, score_col: str = "", label_col: str = ""):
    """Return a callable calibrator if a labelled validation table is provided.

    The table should contain a numeric score column and a binary outcome column
    where 1 means agreement/true-supportive and 0 means non-agreement. Without
    such a labelled table, a true calibrated probability cannot be estimated.
    """
    if not safe_str(table_path):
        return None, "heuristic_sigmoid_uncalibrated_no_reference_labels", "No labelled calibration table was supplied."
    p = Path(table_path)
    if not p.exists() or p.stat().st_size == 0:
        return None, "heuristic_sigmoid_uncalibrated_calibration_table_missing", f"Calibration table not found or empty: {p}"
    try:
        df = read_pathology_table(p)
    except Exception as e:
        return None, "heuristic_sigmoid_uncalibrated_calibration_table_unreadable", f"Could not read calibration table: {e}"
    if df.empty:
        return None, "heuristic_sigmoid_uncalibrated_calibration_table_empty", "Calibration table was empty."
    s_col = choose_col(df, [score_col] if score_col else ["score", "agreement_score", "probable_cna_score", "model_score"])
    y_col = choose_col(df, [label_col] if label_col else ["label", "true_label", "agreement_true", "is_agreement", "outcome"])
    if not s_col or not y_col:
        return None, "heuristic_sigmoid_uncalibrated_calibration_columns_missing", "Calibration table must contain score and binary label columns."
    x = pd.to_numeric(df[s_col], errors="coerce")
    y = pd.to_numeric(df[y_col], errors="coerce")
    ok = x.notna() & y.notna()
    x = x[ok].astype(float)
    y = (y[ok].astype(float) > 0).astype(int)
    if len(x) < 6 or y.nunique() < 2:
        return None, "heuristic_sigmoid_uncalibrated_calibration_insufficient_labels", "Calibration requires at least 6 rows and both positive/negative labels."
    try:
        from sklearn.linear_model import LogisticRegression  # type: ignore
        import numpy as np  # type: ignore
        clf = LogisticRegression(solver="lbfgs")
        clf.fit(x.to_numpy().reshape(-1, 1), y.to_numpy())
        def calibrate(v: Any) -> float:
            vv = float(num(v, 0.0))
            return round(float(clf.predict_proba(np.array([[vv]], dtype=float))[0, 1]), 3)
        return calibrate, f"platt_logistic_calibrated_from_user_table_n={len(x)}", f"Logistic calibration from {p.name}, score_col={s_col}, label_col={y_col}."
    except Exception as e:
        return None, "heuristic_sigmoid_uncalibrated_calibration_fit_failed", f"Could not fit logistic calibrator: {e}"


def probability_fields_for_score(score: Any, calibrator, calibration_status: str, calibration_method: str) -> dict[str, Any]:
    if calibrator is not None:
        try:
            return {
                "probability_estimate": calibrator(score),
                "probability_calibration_status": calibration_status,
                "probability_method": calibration_method,
            }
        except Exception:
            pass
    fields = score_to_probability_fields(score, calibration_status=calibration_status)
    fields["probability_method"] = calibration_method + "; fallback: " + fields["probability_method"]
    return fields


def write_status(pathology: pd.DataFrame, out: pd.DataFrame, path_cols: dict[str, str | None], pathology_path: str | Path) -> None:
    matched = int((out.get("pathology_match_status", pd.Series(dtype=str)) == "matched").sum()) if not out.empty else 0
    total = int(len(out)) if out is not None else 0
    probable = int(out.get("probable_cna_classification", pd.Series(dtype=str)).astype(str).ne("").sum()) if out is not None and not out.empty and "probable_cna_classification" in out.columns else 0
    lines = [
        "PATHOLOGY_CONCORDANCE status",
        f"pathology_file={Path(pathology_path)}",
        f"pathology_rows={len(pathology)}",
        f"samples_total={total}",
        f"matched_samples={matched}",
        f"probable_cna_classifications={probable}",
        f"score_model={TOKEN_SCORE_MODEL_VERSION}",
        f"biomedical_model_layer={BIOMED_MODEL_LAYER_VERSION}",
        "matched_columns=" + json.dumps(path_cols, ensure_ascii=False, default=str),
    ]
    if total and matched == 0 and not pathology.empty:
        lines.append("warning=no_samples_matched; check --pathology_sample_col, sample IDs, or illumina_sample_id values")
    if pathology.empty:
        lines.append("note=pathology table was not provided or was empty; reports will show CNA-only probable classification and omit pathology agreement scoring")
    Path("pathology_status.txt").write_text("\n".join(lines) + "\n")


def main() -> None:
    ap = argparse.ArgumentParser(description="Assess optional pathology-vs-CNA agreement and CNA-only probable classification for CNA reports.")
    ap.add_argument("--classification", required=True)
    ap.add_argument("--clean-events", required=True)
    ap.add_argument("--driver-hits", required=True)
    ap.add_argument("--sample-knowledge-summary", required=True)
    ap.add_argument("--sample-set", default="pan_cancer", help="Cancer/sample-set context supplied by Nextflow --sample_set.")
    ap.add_argument("--pathology", required=True)
    ap.add_argument("--pathology-sample-col", default="", help="Optional explicit pathology sample ID column, e.g. illumina_sample_id.")
    ap.add_argument("--pathology-case-col", default="", help="Optional explicit pathology case/accession column.")
    ap.add_argument("--pathology-diagnosis-col", default="", help="Optional explicit final diagnosis column.")
    ap.add_argument("--enable-biomed-models", default="false", help="If true and pathology is provided, attempt three biomedical transformer language models for semantic support scoring.")
    ap.add_argument("--biomed-models", default=",".join(DEFAULT_BIOMED_MODELS), help="Comma-separated Hugging Face model names to try for biomedical semantic scoring.")
    ap.add_argument("--biomed-local-files-only", default="false", help="Use only locally cached Hugging Face models; avoids web download attempts.")
    ap.add_argument("--biomed-max-tokens", type=int, default=256)
    ap.add_argument("--score-calibration-table", default="", help="Optional labelled validation table to calibrate score-to-probability externally; otherwise the probability estimate is heuristic/uncalibrated.")
    ap.add_argument("--score-calibration-score-col", default="")
    ap.add_argument("--score-calibration-label-col", default="")
    args = ap.parse_args()

    classification = read_tsv(args.classification)
    clean_events = read_tsv(args.clean_events)
    driver_hits = read_tsv(args.driver_hits)
    ks = read_tsv(args.sample_knowledge_summary)
    pathology = read_pathology_table(args.pathology)
    enable_biomed = safe_str(args.enable_biomed_models).lower() in {"true", "1", "yes", "y", "on"}
    biomed_models = parse_model_list(args.biomed_models)
    biomed_local_files_only = safe_str(args.biomed_local_files_only).lower() in {"true", "1", "yes", "y", "on"}
    biomed_max_tokens = int(max(64, min(512, args.biomed_max_tokens)))
    model_trial_rows: list[dict[str, Any]] = []
    calibrator, calibration_status, calibration_method = build_probability_calibrator(
        args.score_calibration_table,
        score_col=args.score_calibration_score_col,
        label_col=args.score_calibration_label_col,
    )

    if classification.empty or "sample" not in classification.columns:
        out_empty = pd.DataFrame()
        out_empty.to_csv("pathology_concordance.tsv", sep="\t", index=False)
        pd.DataFrame().to_csv("pathology_records_matched.tsv", sep="\t", index=False)
        pd.DataFrame(columns=["sample", "model_name", "model_layer", "status", "semantic_similarity", "semantic_score", "message"]).to_csv("pathology_model_trials.tsv", sep="\t", index=False)
        Path("pathology_concordance_metrics.json").write_text(json.dumps({"samples": 0, "pathology_rows": len(pathology), "score_model": TOKEN_SCORE_MODEL_VERSION, "biomed_model_layer": BIOMED_MODEL_LAYER_VERSION, "sample_set": canonical_sample_set(args.sample_set)}, indent=2))
        write_status(pathology, out_empty, {}, args.pathology)
        return

    for df in [classification, clean_events, driver_hits, ks]:
        if not df.empty and "sample" in df.columns:
            df["sample"] = df["sample"].astype(str)

    explicit_sample_col = safe_str(args.pathology_sample_col)
    explicit_case_col = safe_str(args.pathology_case_col)
    explicit_dx_col = safe_str(args.pathology_diagnosis_col)

    def explicit_or_choose(explicit: str, candidates: list[str]) -> str | None:
        if explicit:
            found = choose_col(pathology, [explicit])
            if not found:
                raise SystemExit(f"Requested pathology column not found: {explicit}")
            return found
        return choose_col(pathology, candidates)

    path_cols = {
        "sample_id": explicit_or_choose(explicit_sample_col, ["illumina_sample_id", "sample", "sample_id", "sequencing_sample_id", "cna_sample", "library_id"]),
        "case_code": explicit_or_choose(explicit_case_col, ["case_code", "case", "accession", "accession_id", "pathology_case", "surgical_pathology"]),
        "final_diagnosis": explicit_or_choose(explicit_dx_col, ["final_diagnosis", "diagnosis", "pathology_diagnosis", "final diagnosis"]),
        "category1": choose_col(pathology, ["diagnosis_category_1", "diagnosis_category", "category", "diagnosis class"]),
        "category2": choose_col(pathology, ["diagnosis_category_2", "diagnosis_category_secondary"]),
        "clinical1": choose_col(pathology, ["clinical_diagnosis_1", "clinical_diagnosis", "clinical diagnosis"]),
        "clinical2": choose_col(pathology, ["clinical_diagnosis_2"]),
        "organ": choose_col(pathology, ["specimen_organ", "organ", "specimen", "tissue"]),
        "site1": choose_col(pathology, ["anatomical_site_1", "anatomical_site", "site", "sample_site"]),
        "site2": choose_col(pathology, ["anatomical_site_2"]),
        "markers": choose_col(pathology, ["marker_results_standardized", "ihc", "immunohistochemistry", "marker_results", "markers"]),
        "microscopic": choose_col(pathology, ["microscopic_summary_en", "microscopic", "microscopic_summary"]),
        "macroscopic": choose_col(pathology, ["macroscopic_summary_en", "macroscopic", "macroscopic_summary"]),
        "age": choose_col(pathology, ["age_years", "age"]),
        "sex": choose_col(pathology, ["sex", "gender"]),
        "report_datetime": choose_col(pathology, ["report_datetime", "report_date", "date_reported"]),
    }

    match_by_id: dict[str, int] = {}
    if not pathology.empty:
        for idx, prow in pathology.iterrows():
            for key in [path_cols["sample_id"], path_cols["case_code"]]:
                if key and key in pathology.columns:
                    for n in identifier_tokens(prow.get(key)):
                        if n and n not in match_by_id:
                            match_by_id[n] = idx

    rows: list[dict[str, Any]] = []
    matched_records: list[dict[str, Any]] = []
    for _, crow in classification.sort_values("sample").iterrows():
        sample = safe_str(crow.get("sample"))
        ev = clean_events[clean_events["sample"] == sample].copy() if not clean_events.empty and "sample" in clean_events.columns else pd.DataFrame()
        dh = driver_hits[driver_hits["sample"] == sample].copy() if not driver_hits.empty and "sample" in driver_hits.columns else pd.DataFrame()
        ksr = pd.Series(dtype=object)
        if not ks.empty and "sample" in ks.columns:
            m = ks[ks["sample"].astype(str) == sample]
            if not m.empty:
                ksr = m.iloc[0]
        cprof = infer_cna_profile(crow, ev, dh, ksr)
        probable = cna_probable_classification(cprof, crow, ksr, sample_set=args.sample_set)
        pprob = probability_fields_for_score(probable.get("probable_cna_score", 0), calibrator, calibration_status, calibration_method)
        probable["probable_cna_probability_estimate"] = pprob["probability_estimate"]
        probable["probable_cna_probability_calibration_status"] = pprob["probability_calibration_status"]
        probable["probable_cna_probability_method"] = pprob["probability_method"]

        base = {
            "sample": sample,
            "pathology_match_status": "pathology_not_provided" if pathology.empty else "unmatched",
            "pathology_case_code": "",
            "pathology_sample_id": "",
            "pathology_final_diagnosis": "",
            "pathology_diagnosis_category_1": "",
            "pathology_diagnosis_category_2": "",
            "pathology_clinical_diagnosis": "",
            "pathology_specimen_organ": "",
            "pathology_anatomical_site": "",
            "pathology_lineage": "not_available",
            "pathology_subtype_inferred": "not_available",
            "pathology_ihc_highlights": "",
            "pathology_marker_summary": "",
            "agreement_call": "PATHOLOGY_NOT_PROVIDED" if pathology.empty else "NO_MATCH",
            "agreement_score": "" if pathology.empty else 0,
            "agreement_summary": "No pathology table was provided; only CNA-based probable classification was computed." if pathology.empty else "No pathology row matched this CNA sample.",
            "agreement_rationale": "Provide --pathology with a table containing illumina_sample_id or sample/case identifiers to calculate pathology agreement." if pathology.empty else "Sample was not found in the pathology table using illumina_sample_id, sample_id, sample, library_id, or case_code columns.",
            "supporting_evidence": "",
            "cautionary_evidence": "",
            "agreement_score_breakdown": "" if pathology.empty else "base_matched_pathology=0; total=0",
            "agreement_score_model": "" if pathology.empty else TOKEN_SCORE_MODEL_VERSION,
            "agreement_score_token_only": "" if pathology.empty else 0,
            "agreement_score_final_source": "" if pathology.empty else "token_only_no_match",
            "agreement_probability_estimate": "" if pathology.empty else 0,
            "agreement_probability_calibration_status": "" if pathology.empty else "not_calculated_no_match",
            "agreement_probability_method": "" if pathology.empty else "not_calculated_no_match",
            "agreement_biomed_consensus_score": "",
            "agreement_biomed_model_scores": "",
            "agreement_biomed_model_status": "not_run_no_matched_pathology" if not pathology.empty else "not_run_no_pathology",
            "agreement_token_overlap": "",
            "agreement_pathology_tokens": "",
            "agreement_cna_tokens": probable.get("probable_cna_tokens", ""),
            "cna_knowledge_pattern": safe_str(ksr.get("knowledge_refined_class")) if not ksr.empty else "",
            "cna_rule_based_class": safe_str(crow.get("rule_based_cna_class", "")),
            "cna_burden_class": safe_str(crow.get("cna_burden_class", "")),
            "cna_driver_region_flags": safe_str(crow.get("driver_region_flags", "")),
        }
        base.update(probable)

        if pathology.empty:
            rows.append(base)
            continue

        idx = match_by_id.get(norm_id(sample))
        if idx is None:
            ns = norm_id(sample)
            for token, row_idx in match_by_id.items():
                if ns and (ns == token or token.endswith(ns) or ns.endswith(token)):
                    idx = row_idx
                    break
        if idx is None:
            rows.append(base)
            continue

        prow = pathology.loc[idx]
        marker_text = marker_summary_from_row(prow)
        final_dx = safe_str(prow.get(path_cols["final_diagnosis"])) if path_cols["final_diagnosis"] else ""
        cat1 = safe_str(prow.get(path_cols["category1"])) if path_cols["category1"] else ""
        cat2 = safe_str(prow.get(path_cols["category2"])) if path_cols["category2"] else ""
        clinical = concat_cols(prow, [path_cols["clinical1"], path_cols["clinical2"]])
        organ = safe_str(prow.get(path_cols["organ"])) if path_cols["organ"] else ""
        site = concat_cols(prow, [path_cols["site1"], path_cols["site2"]])
        microscopic = safe_str(prow.get(path_cols["microscopic"])) if path_cols["microscopic"] else ""
        path_text = " ; ".join([final_dx, cat1, cat2, clinical, organ, site, marker_text, microscopic])
        pprof = infer_pathology_profile(path_text, marker_text)
        assess = assess_agreement_token_model(pprof, cprof, crow, path_text, marker_text, probable)
        cna_semantic_text = build_cna_semantic_text(cprof, crow, probable)
        trials, consensus, _biomed_status = run_biomed_model_trials(
            sample=sample,
            path_text=path_text,
            cna_text=cna_semantic_text,
            enable=enable_biomed,
            model_names=biomed_models,
            local_files_only=biomed_local_files_only,
            max_tokens=biomed_max_tokens,
        )
        model_trial_rows.extend(trials)
        assess = apply_biomed_consensus_to_assessment(assess, consensus, trials, pprof)
        aprob = probability_fields_for_score(assess.get("agreement_score", 0), calibrator, calibration_status, calibration_method)
        assess["agreement_probability_estimate"] = aprob["probability_estimate"]
        assess["agreement_probability_calibration_status"] = aprob["probability_calibration_status"]
        assess["agreement_probability_method"] = aprob["probability_method"]

        base.update({
            "pathology_match_status": "matched",
            "pathology_case_code": safe_str(prow.get(path_cols["case_code"])) if path_cols["case_code"] else "",
            "pathology_sample_id": safe_str(prow.get(path_cols["sample_id"])) if path_cols["sample_id"] else sample,
            "pathology_final_diagnosis": final_dx,
            "pathology_diagnosis_category_1": cat1,
            "pathology_diagnosis_category_2": cat2,
            "pathology_clinical_diagnosis": clinical,
            "pathology_specimen_organ": organ,
            "pathology_anatomical_site": site,
            "pathology_lineage": pprof["lineage"],
            "pathology_subtype_inferred": pprof["subtype"],
            "pathology_ihc_highlights": pprof["ihc_highlights"],
            "pathology_marker_summary": marker_text,
            "agreement_call": assess["agreement_call"],
            "agreement_score": assess["agreement_score"],
            "agreement_summary": assess["agreement_summary"],
            "agreement_rationale": assess["agreement_rationale"],
            "supporting_evidence": assess["supporting_evidence"],
            "cautionary_evidence": assess["cautionary_evidence"],
            "agreement_score_breakdown": assess["agreement_score_breakdown"],
            "agreement_score_model": assess["agreement_score_model"],
            "agreement_score_token_only": assess.get("agreement_score_token_only", ""),
            "agreement_score_final_source": assess.get("agreement_score_final_source", ""),
            "agreement_probability_estimate": assess.get("agreement_probability_estimate", ""),
            "agreement_probability_calibration_status": assess.get("agreement_probability_calibration_status", ""),
            "agreement_probability_method": assess.get("agreement_probability_method", ""),
            "agreement_biomed_consensus_score": assess.get("agreement_biomed_consensus_score", ""),
            "agreement_biomed_model_scores": assess.get("agreement_biomed_model_scores", ""),
            "agreement_biomed_model_status": assess.get("agreement_biomed_model_status", ""),
            "agreement_token_overlap": assess["agreement_token_overlap"],
            "agreement_pathology_tokens": assess["agreement_pathology_tokens"],
            "agreement_cna_tokens": assess["agreement_cna_tokens"],
        })
        rows.append(base)
        rec = {"sample": sample}
        for c in pathology.columns:
            rec[c] = prow.get(c)
        matched_records.append(rec)

    out = pd.DataFrame(rows)
    out.to_csv("pathology_concordance.tsv", sep="\t", index=False)
    pd.DataFrame(matched_records).to_csv("pathology_records_matched.tsv", sep="\t", index=False)
    if not model_trial_rows:
        model_trial_rows = [{"sample": "", "model_name": "", "model_layer": BIOMED_MODEL_LAYER_VERSION, "status": "not_run", "semantic_similarity": "", "semantic_score": "", "message": "No matched pathology rows or --enable-biomed-models false."}]
    pd.DataFrame(model_trial_rows).to_csv("pathology_model_trials.tsv", sep="\t", index=False)
    metrics = {
        "samples": len(classification),
        "pathology_rows": len(pathology),
        "matched_samples": int((out["pathology_match_status"] == "matched").sum()) if not out.empty else 0,
        "agreement_calls": out["agreement_call"].value_counts(dropna=False).to_dict() if not out.empty else {},
        "probable_cna_classifications": out["probable_cna_classification"].value_counts(dropna=False).to_dict() if not out.empty and "probable_cna_classification" in out.columns else {},
        "matched_columns": path_cols,
        "score_model": TOKEN_SCORE_MODEL_VERSION,
        "biomed_model_layer": BIOMED_MODEL_LAYER_VERSION,
        "biomed_models_requested": biomed_models,
        "biomed_models_enabled": enable_biomed,
        "biomed_model_trials": len(model_trial_rows),
        "probability_calibration_status": calibration_status,
        "probability_calibration_method": calibration_method,
        "sample_set": canonical_sample_set(args.sample_set),
    }
    Path("pathology_concordance_metrics.json").write_text(json.dumps(metrics, indent=2, default=str))
    write_status(pathology, out, path_cols, args.pathology)


if __name__ == "__main__":
    main()
