#!/usr/bin/env python3
"""Knowledge enrichment for cancer-agnostic CNA-only reports.

This module deliberately does not call a paid LLM or generate unsupported
clinical claims.  It extends the existing CNA classification with:

1. a small built-in pan-cancer CNA knowledge dictionary,
2. optional public literature enrichment through Europe PMC / PubMed-indexed
   metadata, and
3. optional Hugging Face biomedical NER over retrieved abstracts when enabled, and
4. optional local Hugging Face LLM-style literature synthesis over PubMed/Europe-PMC
   abstracts with deterministic PubMed-text fallback if models are unavailable.

The outputs are TSV/JSON files consumed by the PDF report generator.  The
pipeline remains useful offline; internet failures are recorded but are not
fatal by default.
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

import pandas as pd

try:
    import requests
except Exception:  # pragma: no cover - handled at runtime
    requests = None


MISSING_STRINGS = {"", "none", "none_detected", "none_detected_or_not_run", "nan", "na", "n/a", "null"}

# Lightweight, conservative CNA knowledge.  This is intentionally phrased as
# research interpretation rather than diagnosis.  PMIDs listed here are seeds
# for the PDF reference section; the web-enrichment step can add more.
BUILTIN_FEATURE_KB: dict[str, dict[str, Any]] = {
    "1q_gain": {
        "genes": "MCL1/BCL9/MDM4-region",
        "display": "Broad 1q copy gain",
        "category": "broad aneuploidy / survival-axis CNA",
        "biological_interpretation": "Broad 1q gain is a recurrent aneuploidy-type CNA in several hematologic malignancies and can indicate increased copy number of survival or transcriptional-regulatory regions. It is not diagnostic by itself.",
        "classification_hint": "Supports a CNA-positive, aneuploidy-enriched lymphoma profile when present with other driver-region events.",
        "caveat": "Interpret with tumor purity, ploidy, and orthogonal markers.",
        "seed_pmids": [],
        "tier": "CNA-context",
    },
    "1p_loss": {
        "genes": "TNFRSF14/CD58-region",
        "display": "1p loss region",
        "category": "tumor-suppressor / immune-interaction CNA",
        "biological_interpretation": "1p losses may include loci relevant to B-cell lymphoma biology, immune interaction, and tumor-suppressor functions. The exact gene affected depends on event boundaries.",
        "classification_hint": "Adds support to a deletion-rich CNA pattern, especially when combined with 6q, 9p21, 17p, or 22q losses.",
        "caveat": "Low-pass WGS CNA does not specify biallelic inactivation or mutation status.",
        "seed_pmids": [],
        "tier": "CNA-context",
    },
    "2p16_REL_BCL11A_gain_amp": {
        "genes": "REL/BCL11A",
        "display": "2p16-p15 REL/BCL11A gain or amplification",
        "category": "B-cell lymphoma oncogenic amplification region",
        "biological_interpretation": "Gain or amplification of the 2p16-p15 region can involve REL and BCL11A and is a recurrent B-cell lymphoma CNA. REL amplification is linked to NF-kB pathway biology in several lymphoma contexts.",
        "classification_hint": "Supports a B-cell lymphoma driver-CNA pattern and may contribute to amplification-rich groups when high-level.",
        "caveat": "This is not equivalent to gene expression or pathway activation; confirm with pathology and molecular data.",
        "seed_pmids": ["11507094", "15077154"],
        "tier": "driver-CNA",
    },
    "3q27_BCL6_alteration": {
        "genes": "BCL6",
        "display": "3q27/BCL6-region CNA",
        "category": "BCL6 locus region",
        "biological_interpretation": "The BCL6 locus is a central lymphoma gene, most classically altered by rearrangement or mutation. CNA around 3q27 can flag this region but does not establish BCL6 rearrangement.",
        "classification_hint": "Correlate with BCL6 FISH/IHC or sequencing if lymphoma subtype classification requires it.",
        "caveat": "CNA-only evidence is weaker than translocation/SV evidence for BCL6-driven classification.",
        "seed_pmids": [],
        "tier": "supportive-CNA",
    },
    "6q21_PRDM1_loss": {
        "genes": "PRDM1",
        "display": "6q21 PRDM1-region loss",
        "category": "tumor-suppressor deletion region",
        "biological_interpretation": "6q21 loss can affect PRDM1/BLIMP1-region biology, relevant to plasma-cell differentiation and B-cell lymphoma tumor-suppressor programs.",
        "classification_hint": "Supports a deletion-rich B-cell lymphoma CNA pattern, especially with TNFAIP3, CDKN2A/B, or TP53-region loss.",
        "caveat": "CNA loss does not prove gene mutation or complete functional loss.",
        "seed_pmids": ["17558395", "17558399"],
        "tier": "driver-CNA",
    },
    "6q23_TNFAIP3_loss": {
        "genes": "TNFAIP3",
        "display": "6q23 TNFAIP3-region loss",
        "category": "NF-kB negative-regulator deletion region",
        "biological_interpretation": "6q23 loss can involve TNFAIP3/A20, a negative regulator of NF-kB signaling recurrently altered in B-cell lymphomas.",
        "classification_hint": "Supports a deletion-rich / NF-kB dysregulation CNA pattern when combined with other driver CNAs.",
        "caveat": "Confirm biallelic loss or mutation when clinically relevant.",
        "seed_pmids": ["19412164", "19412172"],
        "tier": "driver-CNA",
    },
    "7_gain": {
        "genes": "chr7 broad",
        "display": "Chromosome 7 gain/amplification pattern",
        "category": "broad aneuploidy pattern",
        "biological_interpretation": "Broad chromosome 7 gain is an aneuploidy-type CNA. It is useful for cohort stratification but is not a specific lymphoma subtype marker by itself.",
        "classification_hint": "Adds to CNA burden/aneuploidy classification.",
        "caveat": "Interpret with ploidy and whole-genome CNA context.",
        "seed_pmids": [],
        "tier": "CNA-context",
    },
    "8q24_MYC_gain_amp": {
        "genes": "MYC",
        "display": "8q24/MYC-region gain or amplification",
        "category": "MYC-region oncogenic CNA",
        "biological_interpretation": "8q24 gain/amplification can increase copy number of the MYC region, a major lymphoma oncogene. This should be distinguished from MYC rearrangement.",
        "classification_hint": "Supports an oncogene-gain/amplification-rich CNA pattern and should trigger correlation with MYC FISH/IHC/SV data when available.",
        "caveat": "CNA gain does not prove MYC rearrangement or MYC protein overexpression.",
        "seed_pmids": ["18195093", "25216682"],
        "tier": "driver-CNA",
    },
    "9p24_JAK2_PDL1_PDL2_gain_amp": {
        "genes": "JAK2/CD274/PDCD1LG2",
        "display": "9p24 JAK2/PD-L1/PD-L2 gain or amplification",
        "category": "immune-evasion / JAK-STAT CNA",
        "biological_interpretation": "9p24 copy gain/amplification can involve JAK2 and the PD-L1/PD-L2 locus, a region important in immune-evasion biology in selected lymphoma entities.",
        "classification_hint": "When strong and focal, this can raise consideration of PMBCL/classic Hodgkin-like immune-evasion biology, but CNA alone is not diagnostic.",
        "caveat": "Confirm with histology, CD30/PD-L1 expression, SVs, and entity-specific pathology.",
        "seed_pmids": ["17955649", "22461648", "26610334"],
        "tier": "driver-CNA/actionability-context",
    },
    "9p21_CDKN2A_B_loss": {
        "genes": "CDKN2A/CDKN2B",
        "display": "9p21 CDKN2A/CDKN2B loss",
        "category": "cell-cycle tumor-suppressor deletion",
        "biological_interpretation": "9p21 deletion can affect CDKN2A/CDKN2B, key cell-cycle tumor-suppressor loci. Deep loss is more suggestive of stronger biological impact than low-level loss.",
        "classification_hint": "Supports tumor-suppressor loss/deletion-rich and higher-risk CNA patterns when accompanied by high CNA burden or TP53-axis events.",
        "caveat": "Assess focality and confirm with orthogonal testing if needed.",
        "seed_pmids": ["18636101", "25216682"],
        "tier": "driver-CNA",
    },
    "10q23_PTEN_loss": {
        "genes": "PTEN",
        "display": "10q23/PTEN-region loss",
        "category": "PI3K/AKT pathway tumor-suppressor CNA",
        "biological_interpretation": "10q23 loss may involve PTEN, a negative regulator of PI3K/AKT pathway signaling.",
        "classification_hint": "Adds evidence for tumor-suppressor/pathway-loss CNA biology, especially with other deletions.",
        "caveat": "CNA loss alone does not establish PTEN protein loss or pathway activation.",
        "seed_pmids": [],
        "tier": "supportive-CNA",
    },
    "11q_loss": {
        "genes": "11q distal genes",
        "display": "Distal 11q deletion pattern",
        "category": "broad deletion pattern",
        "biological_interpretation": "Distal 11q loss is a recurrent structural/CNA pattern in several lymphoid neoplasms and is useful for pattern recognition.",
        "classification_hint": "Supports deletion-rich CNA stratification; entity-specific meaning requires pathology and other genomic lesions.",
        "caveat": "CNA-only result does not infer a specific 11q-aberrant lymphoma subtype.",
        "seed_pmids": [],
        "tier": "CNA-context",
    },
    "12q15_MDM2_CDK4_gain_amp": {
        "genes": "MDM2/CDK4-region",
        "display": "12q15 MDM2/CDK4-region gain or amplification",
        "category": "cell-cycle/p53-axis copy gain region",
        "biological_interpretation": "12q15 gain/amplification can involve MDM2/CDK4-region biology, relevant to p53 and cell-cycle control, although this event is not lymphoma-specific.",
        "classification_hint": "Adds to amplification-rich and cell-cycle CNA features; interpret with tumor type and focality.",
        "caveat": "Broad gains may not imply focal MDM2 or CDK4 amplification.",
        "seed_pmids": [],
        "tier": "supportive-CNA",
    },
    "13q14_loss": {
        "genes": "RB1/DLEU-region",
        "display": "13q14 loss region",
        "category": "cell-cycle / noncoding RNA region CNA",
        "biological_interpretation": "13q14 loss can affect RB1/DLEU-region biology, depending on boundaries, and is a recurrent deletion in lymphoid malignancies.",
        "classification_hint": "Supports a deletion-rich CNA profile.",
        "caveat": "Check whether RB1 itself is included and whether loss is focal or broad.",
        "seed_pmids": [],
        "tier": "supportive-CNA",
    },
    "15q21_B2M_loss": {
        "genes": "B2M-region",
        "display": "15q21/B2M-region loss",
        "category": "antigen-presentation CNA",
        "biological_interpretation": "Loss of the B2M region may suggest altered MHC class I antigen-presentation biology when confirmed.",
        "classification_hint": "Can support immune-evasion biology, especially with 9p24 events or other immune-related alterations.",
        "caveat": "CNA region may be broad; confirm B2M status with sequencing/IHC if clinically relevant.",
        "seed_pmids": ["26872778"],
        "tier": "driver-CNA/context-dependent",
    },
    "16p13_CIITA_loss": {
        "genes": "CIITA-region",
        "display": "16p13/CIITA-region loss",
        "category": "antigen-presentation / transcriptional-regulation CNA",
        "biological_interpretation": "CIITA-region alteration can be relevant to antigen presentation and immune-evasion biology in selected lymphoma settings.",
        "classification_hint": "Supportive immune-evasion CNA flag; correlation with HLA/MHC expression is recommended.",
        "caveat": "CNA loss is not equivalent to CIITA rearrangement or expression loss.",
        "seed_pmids": [],
        "tier": "supportive-CNA",
    },
    "17p13_TP53_loss": {
        "genes": "TP53",
        "display": "17p13/TP53-region loss",
        "category": "TP53-axis tumor-suppressor CNA",
        "biological_interpretation": "17p13 loss can involve TP53 and is a major marker of TP53-axis disruption when accompanied by mutation or biallelic inactivation.",
        "classification_hint": "In CNA-high DLBCL-like cohorts, TP53-region loss plus complex CNA can support an A53-like / TP53-axis CNA pattern, but formal assignment requires mutation/SV/pathology context.",
        "caveat": "Do not call TP53 mutation from CNA loss alone.",
        "seed_pmids": ["29641966", "32196109"],
        "tier": "driver-CNA/high-risk-context",
    },
    "18q21_BCL2_MALT1_gain_amp": {
        "genes": "BCL2/MALT1",
        "display": "18q21 BCL2/MALT1 gain or amplification",
        "category": "BCL2/MALT1-region oncogenic CNA",
        "biological_interpretation": "18q21 gain/amplification can involve BCL2 and MALT1-region biology, relevant to B-cell survival and NF-kB-associated signaling contexts.",
        "classification_hint": "Supports a B-cell lymphoma oncogene-gain CNA profile; correlate with BCL2 rearrangement/IHC when available.",
        "caveat": "CNA gain does not establish BCL2 translocation or protein expression.",
        "seed_pmids": ["25216682"],
        "tier": "driver-CNA",
    },
    "19_loss": {
        "genes": "chr19 broad",
        "display": "Chromosome 19 deletion pattern",
        "category": "broad deletion pattern",
        "biological_interpretation": "Broad chromosome 19 loss contributes to overall CNA burden and deletion-rich structure.",
        "classification_hint": "Useful mainly for cohort stratification.",
        "caveat": "Broad event; specific genes require boundary-level review.",
        "seed_pmids": [],
        "tier": "CNA-context",
    },
    "22q_loss": {
        "genes": "chr22 broad",
        "display": "22q deletion pattern",
        "category": "broad deletion pattern",
        "biological_interpretation": "22q loss is a broad deletion pattern that can contribute to tumor-suppressor-loss and aneuploidy profiles.",
        "classification_hint": "Supports deletion-rich CNA stratification; not diagnostic by itself.",
        "caveat": "Specific driver interpretation depends on boundaries and tumor context.",
        "seed_pmids": [],
        "tier": "CNA-context",
    },
}


# Additional compact knowledge entries for the expanded pan-cancer catalog.
BUILTIN_FEATURE_KB.update({
    "2p24_MYCN_gain_amp": {"genes": "MYCN", "display": "2p24/MYCN gain or amplification", "category": "MYCN oncogene amplification context", "biological_interpretation": "2p24 gain/amplification can involve MYCN, a key oncogene in neuroblastoma and selected embryonal tumors. CNA evidence should be correlated with tumor type and orthogonal MYCN testing.", "classification_hint": "Supports neuroblastoma/embryonal tumor CNA context when pathology and site are compatible.", "caveat": "Low-pass WGS suggests copy gain but should be confirmed if clinical actionability is needed.", "seed_pmids": [], "tier": "driver-CNA/actionability-context"},
    "5p15_TERT_gain_amp": {"genes": "TERT/CLPTM1L", "display": "5p15/TERT-region gain", "category": "telomerase-region CNA", "biological_interpretation": "5p15 gain can include the TERT locus and contributes to an oncogene-gain CNA context in several carcinomas.", "classification_hint": "Supportive pan-cancer CNA feature; not tumor-type specific by itself.", "caveat": "CNA gain is not equivalent to TERT promoter mutation or expression.", "seed_pmids": [], "tier": "supportive-CNA"},
    "7p11_EGFR_gain_amp": {"genes": "EGFR", "display": "7p11/EGFR-region gain or amplification", "category": "EGFR oncogene CNA", "biological_interpretation": "EGFR-region gain/amplification is relevant in glioma, lung carcinoma, and other cancers depending on histology.", "classification_hint": "Supports EGFR/chr7 glioma-like or carcinoma-context CNA patterns when pathology is compatible.", "caveat": "Does not determine EGFR mutation, fusion, or protein activation status.", "seed_pmids": [], "tier": "driver-CNA/actionability-context"},
    "7q31_MET_gain_amp": {"genes": "MET", "display": "7q31/MET-region gain or amplification", "category": "receptor-tyrosine-kinase CNA", "biological_interpretation": "MET copy gain/amplification can be relevant in lung, gastric, renal and other tumors.", "classification_hint": "Supports receptor-tyrosine-kinase gain context; confirm focality and tumor context.", "caveat": "Broad chromosome 7 gain can include MET without focal MET amplification.", "seed_pmids": [], "tier": "driver-CNA/actionability-context"},
    "10q26_FGFR2_gain_amp": {"genes": "FGFR2", "display": "10q26/FGFR2-region gain or amplification", "category": "FGFR2 oncogene CNA", "biological_interpretation": "FGFR2 copy gain/amplification is a context-dependent oncogene event reported in gastric and other carcinomas.", "classification_hint": "Supports upper-GI/pancreatobiliary receptor-tyrosine-kinase CNA context when pathology is compatible.", "caveat": "CNA gain does not prove FGFR2 fusion or activating mutation.", "seed_pmids": [], "tier": "driver-CNA/actionability-context"},
    "11q13_CCND1_FGF_gain_amp": {"genes": "CCND1/FGF3/FGF4/FGF19", "display": "11q13 CCND1/FGF-region gain or amplification", "category": "cell-cycle/FGF amplification region", "biological_interpretation": "11q13 copy gain/amplification can involve CCND1 and FGF genes and is relevant across breast, head-and-neck, esophageal, urothelial, and other tumors.", "classification_hint": "Supports cell-cycle/FGF-amplification CNA context; tumor type determines meaning.", "caveat": "Broad gain may not imply focal CCND1 amplification.", "seed_pmids": [], "tier": "driver-CNA/actionability-context"},
    "17q12_ERBB2_gain_amp": {"genes": "ERBB2/GRB7", "display": "17q12/ERBB2-HER2 gain or amplification", "category": "HER2 oncogene CNA", "biological_interpretation": "ERBB2/HER2-region gain or amplification is highly relevant in breast and gastric/GEJ carcinomas and selected other tumors.", "classification_hint": "Supports HER2-amplified carcinoma-compatible CNA pattern when pathology is compatible.", "caveat": "Clinical HER2 status requires validated IHC/ISH or clinical-grade molecular confirmation.", "seed_pmids": [], "tier": "driver-CNA/actionability-context"},
    "19q12_CCNE1_gain_amp": {"genes": "CCNE1", "display": "19q12/CCNE1-region gain or amplification", "category": "cell-cycle amplification region", "biological_interpretation": "CCNE1 amplification is a recurrent cell-cycle CNA in high-grade serous ovarian carcinoma and selected endometrial/gastric cancers.", "classification_hint": "Supports ovarian/gynecologic or upper-GI cell-cycle CNA context when pathology is compatible.", "caveat": "Interpret with tumor type and focality.", "seed_pmids": [], "tier": "driver-CNA/actionability-context"},
    "12p_gain_germ_cell_context": {"genes": "KRAS/CCND2/12p broad", "display": "12p gain", "category": "germ-cell tumor CNA context", "biological_interpretation": "12p gain, including i(12p)-like patterns, is a classic germ-cell tumor-associated CNA pattern.", "classification_hint": "Supports germ-cell tumor CNA context when pathology/site is compatible.", "caveat": "Low-pass WGS indicates copy gain but not isochromosome structure.", "seed_pmids": [], "tier": "driver-CNA/context-dependent"},
    "Xq12_AR_gain_amp": {"genes": "AR", "display": "Xq12/AR-region gain or amplification", "category": "androgen-receptor CNA context", "biological_interpretation": "AR copy gain/amplification is relevant in prostate carcinoma biology and treatment resistance contexts.", "classification_hint": "Supports prostate carcinoma CNA context when pathology is compatible.", "caveat": "Clinical interpretation requires tumor context and AR expression/therapy history.", "seed_pmids": [], "tier": "driver-CNA/actionability-context"},
    "22q_loss": {"genes": "NF2/SMARCB1-region", "display": "22q deletion pattern", "category": "broad deletion / CNS-meningioma context", "biological_interpretation": "22q loss can include NF2/SMARCB1 regions and is relevant in meningioma and multiple tumor contexts.", "classification_hint": "Supports meningioma-compatible broad CNA pattern when paired with appropriate CNS pathology.", "caveat": "Specific gene involvement depends on event boundaries.", "seed_pmids": [], "tier": "CNA-context"},
})

# Aliases used by the classifier's compact flag names.
FEATURE_ALIASES = {
    "6q_loss_PRDM1_TNFAIP3_axis": ["6q21_PRDM1_loss", "6q23_TNFAIP3_loss"],
    "chr7_gain_pattern": ["7_gain"],
    "1q_gain_pattern": ["1q_gain"],
    "1p_loss_pattern": ["1p_loss"],
    "22q_loss_pattern": ["22q_loss"],
    "19_loss": ["19_loss"],
    "2p16_REL_BCL11A_gain_amp": ["2p16_REL_BCL11A_gain_amp"],
    "9p21_CDKN2A_B_loss": ["9p21_CDKN2A_B_loss"],
    "17p13_TP53_loss": ["17p13_TP53_loss"],
    "18q21_BCL2_MALT1_gain_amp": ["18q21_BCL2_MALT1_gain_amp"],
    "8q24_MYC_gain_amp": ["8q24_MYC_gain_amp"],
    "2p24_MYCN_gain_amp": ["2p24_MYCN_gain_amp"],
    "7p11_EGFR_gain_amp": ["7p11_EGFR_gain_amp"],
    "7q31_MET_gain_amp": ["7q31_MET_gain_amp"],
    "10q26_FGFR2_gain_amp": ["10q26_FGFR2_gain_amp"],
    "11q13_CCND1_FGF_gain_amp": ["11q13_CCND1_FGF_gain_amp"],
    "17q12_ERBB2_HER2_gain_amp": ["17q12_ERBB2_gain_amp"],
    "19q12_CCNE1_gain_amp": ["19q12_CCNE1_gain_amp"],
    "12p_gain_germ_cell_pattern": ["12p_gain_germ_cell_context"],
    "Xq12_AR_gain_amp": ["Xq12_AR_gain_amp"],
    "10q23_PTEN_loss": ["10q23_PTEN_loss"],
    "9p24_JAK2_PDL1_PDL2_gain_amp": ["9p24_JAK2_PDL1_PDL2_gain_amp"],
    "15q21_B2M_loss": ["15q21_B2M_loss"],
    "12q15_MDM2_CDK4_gain_amp": ["12q15_MDM2_CDK4_gain_amp"],
    "3q27_BCL6_alteration": ["3q27_BCL6_alteration"],
    "11q_loss": ["11q_loss"],
    "13q14_loss": ["13q14_loss"],
    "16p13_CIITA_loss": ["16p13_CIITA_loss"],
}


# Additional pan-cancer aliases introduced in v6.  These map report-level flags
# back to catalog feature IDs so knowledge summaries remain stable.
FEATURE_ALIASES.update({
    "3p_loss_pattern": ["3p_loss"],
    "3q26_PIK3CA_SOX2_TERC_gain_amp": ["3q26_PIK3CA_SOX2_TERC_gain_amp"],
    "4q12_KIT_PDGFRA_KDR_gain_amp": ["4q12_KIT_PDGFRA_KDR_gain_amp"],
    "4q_loss_pattern": ["4q_loss"],
    "5q_loss_MDS_AML_pattern": ["5q_loss_MDS_AML"],
    "7q_loss_pattern": ["7q_loss"],
    "7p11_EGFR_gain_amp": ["7p11_EGFR_gain_amp"],
    "8p_loss_pattern": ["8p_loss"],
    "10_loss_GBM_context": ["10_loss_GBM_context"],
    "chr10_loss_pattern": ["10_loss_GBM_context"],
    "11q13_CCND1_FGF_gain_amp": ["11q13_CCND1_FGF_gain_amp"],
    "12p13_ETV6_loss": ["12p13_ETV6_loss"],
    "13q14_RB1_region_loss": ["13q14_loss"],
    "13q_gain_pattern": ["13q_gain_colon_context"],
    "16q_loss_pattern": ["16q_loss_breast_context"],
    "17q12_ERBB2_HER2_gain_amp": ["17q12_ERBB2_gain_amp"],
    "18q_loss_SMAD4_DCC_pattern": ["18q_loss_SMAD4_DCC"],
    "20q_gain_pattern": ["20q_gain_colon_context"],
    "21q_RUNX1_region_CNA": ["21q_RUNX1_region_CNA"],
})

BUILTIN_FEATURE_KB.update({
    "3p_loss": {
        "genes": "FHIT/VHL-region", "display": "3p loss region", "category": "tumor-suppressor / broad deletion CNA",
        "biological_interpretation": "Broad 3p loss is a recurrent tumor-suppressor-region CNA in multiple solid tumors. It is useful as pan-cancer context but not tumor-type definitive by itself.",
        "classification_hint": "Supports deletion-rich solid-tumor CNA context when combined with other carcinoma-associated CNAs.",
        "caveat": "Low-pass WGS cannot determine the exact gene-level mechanism or mutation status.", "seed_pmids": [], "tier": "CNA-context",
    },
    "3q26_PIK3CA_SOX2_TERC_gain_amp": {
        "genes": "PIK3CA/SOX2/TERC-region", "display": "3q26 oncogene-region gain/amplification", "category": "pan-cancer oncogene-region CNA",
        "biological_interpretation": "3q26-region copy gain can involve oncogenic regulatory regions including PIK3CA/SOX2/TERC depending on event boundaries and is seen in several epithelial cancers.",
        "classification_hint": "Supports oncogene-gain CNA biology; interpretation depends on tumor type and focality.",
        "caveat": "Does not prove gene activation or mutation.", "seed_pmids": [], "tier": "supportive-CNA",
    },
    "4q12_KIT_PDGFRA_KDR_gain_amp": {
        "genes": "KIT/PDGFRA/KDR", "display": "4q12 KIT/PDGFRA/KDR-region gain or amplification", "category": "RTK-region CNA",
        "biological_interpretation": "4q12 gain/amplification can include receptor tyrosine kinase genes KIT, PDGFRA, and KDR. It can be relevant in glioma and other tumors when focal and high-level.",
        "classification_hint": "Supports RTK-amplified CNA context; confirm focality and protein/sequence context.",
        "caveat": "Broad gain is weaker evidence than focal high-level amplification.", "seed_pmids": [], "tier": "driver-CNA/actionability-context",
    },
    "5q_loss_MDS_AML": {
        "genes": "EGR1/APC/NPM1-region", "display": "5q loss / myeloid-neoplasm-associated region", "category": "myeloid/leukemia-associated deletion region",
        "biological_interpretation": "5q loss is a classic cytogenetic event in myelodysplastic syndromes and myeloid leukemias, though the exact significance depends on boundaries, karyotype, and clinical setting.",
        "classification_hint": "Supports leukemia/MDS-compatible CNA context when pathology or blood/bone marrow source is compatible.",
        "caveat": "Does not replace karyotype, FISH, fusion testing, or mutation profiling.", "seed_pmids": [], "tier": "driver-CNA/context-dependent",
    },
    "7q_loss": {
        "genes": "chr7q broad", "display": "Chromosome 7q deletion pattern", "category": "myeloid/leukemia-associated deletion region",
        "biological_interpretation": "7q loss or monosomy 7-like CNA is important in myeloid neoplasms and can also occur in other tumors.",
        "classification_hint": "Supports myeloid/MDS/leukemia-compatible CNA context when sample source/pathology is compatible.",
        "caveat": "Requires integration with blood/bone marrow morphology, cytogenetics, and SNV/fusion testing.", "seed_pmids": [], "tier": "driver-CNA/context-dependent",
    },
    "7p11_EGFR_gain_amp": {
        "genes": "EGFR", "display": "7p11 EGFR-region gain or amplification", "category": "RTK oncogene-region CNA",
        "biological_interpretation": "EGFR-region amplification is an important RTK CNA in glioblastoma and selected epithelial tumors. In glioma, it is often interpreted together with chromosome 7 gain and chromosome 10 loss.",
        "classification_hint": "Supports EGFR/RTK-amplified or glioma-like CNA context depending on pathology.",
        "caveat": "Confirm with focality, orthogonal amplification testing, and entity-specific molecular workup.", "seed_pmids": [], "tier": "driver-CNA/actionability-context",
    },
    "8p_loss": {
        "genes": "8p broad", "display": "8p deletion pattern", "category": "broad deletion CNA",
        "biological_interpretation": "8p loss is a recurrent broad CNA in several epithelial cancers and contributes to deletion-rich pan-cancer patterns.",
        "classification_hint": "Supports solid-tumor CNA context when present with other carcinoma-like CNAs.",
        "caveat": "Not tumor-type definitive by itself.", "seed_pmids": [], "tier": "CNA-context",
    },
    "10_loss_GBM_context": {
        "genes": "chr10 broad", "display": "Chromosome 10 loss pattern", "category": "CNS glioma-associated CNA context",
        "biological_interpretation": "Chromosome 10 loss, especially with chromosome 7 gain and/or EGFR amplification, is a characteristic CNA pattern in glioblastoma-like high-grade glioma contexts.",
        "classification_hint": "Supports CNS glioma-like CNA context when pathology/site is compatible.",
        "caveat": "CNA alone cannot assign WHO CNS tumor class or methylation class.", "seed_pmids": [], "tier": "driver-CNA/context-dependent",
    },
    "11q13_CCND1_FGF_gain_amp": {
        "genes": "CCND1/FGF3/FGF4/FGF19", "display": "11q13 CCND1/FGF-region gain or amplification", "category": "cell-cycle/oncogene CNA",
        "biological_interpretation": "11q13 gain/amplification can involve CCND1 and FGF-region genes and is recurrent in breast cancer, head/neck cancers, and selected lymphoid neoplasms.",
        "classification_hint": "Supports CCND1/11q13-amplified CNA context; tumor-type interpretation depends on pathology.",
        "caveat": "Not equivalent to CCND1 rearrangement or overexpression.", "seed_pmids": [], "tier": "driver-CNA/context-dependent",
    },
    "12p13_ETV6_loss": {
        "genes": "ETV6", "display": "12p13 ETV6-region loss", "category": "leukemia-associated region",
        "biological_interpretation": "12p13/ETV6-region loss can be relevant in lymphoid or myeloid leukemias depending on disease context.",
        "classification_hint": "Supports leukemia-compatible CNA context when pathology/sample source is compatible.",
        "caveat": "Does not detect ETV6 fusions or sequence variants.", "seed_pmids": [], "tier": "supportive-CNA/context-dependent",
    },
    "13q_gain_colon_context": {
        "genes": "chr13 broad", "display": "Chromosome 13 gain pattern", "category": "colorectal/solid-tumor CNA context",
        "biological_interpretation": "Chromosome 13 gain is a recurrent broad CNA in colorectal and other solid tumors, commonly interpreted in combination with 20q gain, 18q loss, 17p loss, and 8q gain.",
        "classification_hint": "Supports colorectal-like chromosomal instability context when paired with 20q gain and 18q/17p losses.",
        "caveat": "Not specific in isolation.", "seed_pmids": [], "tier": "CNA-context",
    },
    "16q_loss_breast_context": {
        "genes": "CDH1/WWOX-region", "display": "16q loss pattern", "category": "breast/solid-tumor CNA context",
        "biological_interpretation": "16q loss is a recurrent broad CNA in breast cancer and other tumors, especially certain luminal/lobular contexts.",
        "classification_hint": "Supports breast-like CNA context when combined with 1q gain, 8q gain, 11q13 amplification, or ERBB2 amplification.",
        "caveat": "Not diagnostic without pathology and receptor/IHC context.", "seed_pmids": [], "tier": "CNA-context",
    },
    "17q12_ERBB2_gain_amp": {
        "genes": "ERBB2/GRB7", "display": "17q12 ERBB2/HER2-region gain or amplification", "category": "actionability-context oncogene CNA",
        "biological_interpretation": "17q12 gain/amplification can involve ERBB2/HER2, a clinically important oncogene in breast, gastric, and other cancers when confirmed and interpreted in disease context.",
        "classification_hint": "Supports HER2/ERBB2-amplified CNA context; confirm with clinical-grade HER2 testing where relevant.",
        "caveat": "Low-pass WGS CNA is not a substitute for approved HER2 IHC/ISH or validated clinical assay where treatment decisions are considered.", "seed_pmids": [], "tier": "driver-CNA/actionability-context",
    },
    "18q_loss_SMAD4_DCC": {
        "genes": "SMAD4/DCC", "display": "18q loss / SMAD4-DCC region", "category": "colorectal/pancreatic tumor-suppressor CNA",
        "biological_interpretation": "18q loss can involve SMAD4/DCC-region biology and is common in colorectal and pancreatic ductal adenocarcinoma contexts.",
        "classification_hint": "Supports colorectal/pancreatic-like tumor-suppressor loss context with 9p21, 17p, 8q, or 20q changes.",
        "caveat": "CNA loss does not determine SMAD4 protein loss or mutation status.", "seed_pmids": [], "tier": "driver-CNA/context-dependent",
    },
    "20q_gain_colon_context": {
        "genes": "AURKA/ZNF217/20q broad", "display": "20q gain pattern", "category": "colorectal/solid-tumor CNA context",
        "biological_interpretation": "20q gain is a common chromosomal-instability CNA in colorectal cancer and other epithelial tumors.",
        "classification_hint": "Supports colorectal-like CNA context when combined with 18q loss, 17p loss, 8q gain, and/or 13q gain.",
        "caveat": "Not specific alone.", "seed_pmids": [], "tier": "CNA-context",
    },
    "21q_RUNX1_region_CNA": {
        "genes": "RUNX1", "display": "21q RUNX1-region CNA", "category": "leukemia-associated region",
        "biological_interpretation": "RUNX1-region CNA can be relevant in leukemias, but sequence variants and fusions require separate assays.",
        "classification_hint": "Supports leukemia-compatible context when blood/bone marrow pathology is compatible.",
        "caveat": "Low-pass WGS cannot detect RUNX1 point mutations or many fusions.", "seed_pmids": [], "tier": "supportive-CNA/context-dependent",
    },
})


def read_tsv(path: str | Path, index_col=None) -> pd.DataFrame:
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return pd.DataFrame()
    return pd.read_csv(p, sep="\t", index_col=index_col)


def safe_str(value: Any) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


def split_flags(value: Any) -> list[str]:
    s = safe_str(value).strip()
    if s.lower() in MISSING_STRINGS:
        return []
    return [p.strip() for p in re.split(r"[;,]", s) if p.strip() and p.strip().lower() not in MISSING_STRINGS]




def split_field(value: Any) -> list[str]:
    """Backward-compatible alias used by summary/literature aggregation."""
    return split_flags(value)


def split_genes(value: Any) -> list[str]:
    s = safe_str(value)
    # Keep gene symbols only; region labels like "chr7 broad" are still allowed but not searched as genes.
    parts = [p.strip() for p in re.split(r"[/,;|]", s) if p.strip()]
    return parts


def normalize_feature_id(feature: str) -> str:
    return safe_str(feature).strip().replace("driver__", "")


def feature_ids_from_flag(flag: str) -> list[str]:
    flag = normalize_feature_id(flag)
    if flag in FEATURE_ALIASES:
        return FEATURE_ALIASES[flag]
    if flag in BUILTIN_FEATURE_KB:
        return [flag]
    return [flag]


def infer_refined_class(row: pd.Series, features: Iterable[str], cancer_type: str = "broad_cancer") -> tuple[str, str]:
    """Knowledge-level CNA pattern label.

    This mirrors the pathology-concordance layer: broad_cancer allows all
    pan-cancer labels; specific sample_set values restrict labels to the current
    context so lymphoma runs do not become CNS/breast/etc. merely because of
    generic pan-cancer CNA overlap.
    """
    fset = set(features)
    rule = safe_str(row.get("rule_based_cna_class", "CNA_pattern"))
    burden = safe_str(row.get("cna_burden_class", ""))
    n_events = int(float(row.get("n_cna_events", 0) or 0))
    context = canonical_sample_set(cancer_type)
    broad_mode = context in {"broad_cancer", "pan_cancer", "all", "all_cancers"}

    has = lambda f: f in fset
    has_6q = has("6q21_PRDM1_loss") or has("6q23_TNFAIP3_loss")
    if n_events == 0 or "flat" in burden.lower() or rule == "CNA-flat":
        return "CNA-flat / no high-confidence driver CNA", "No high-confidence CNA was available under the current thresholds. This does not exclude balanced translocations, SNVs, indels, epigenetic class, expression-defined class, or low-purity/subclonal CNA."

    if context == "lymphoma" and not broad_mode:
        if has("9p24_JAK2_PDL1_PDL2_gain_amp") and (has("2p16_REL_BCL11A_gain_amp") or has("18q21_BCL2_MALT1_gain_amp")):
            return "Lymphoma-context immune-evasion CNA pattern", "Because --sample_set lymphoma was supplied, knowledge interpretation is restricted to lymphoma. 9p24 gain/amplification with additional lymphoma-associated driver CNAs suggests immune-evasion/JAK-STAT-region biology; correlate with morphology, CD30/PD-L1 expression, EBV status, and SV data."
        if has("17p13_TP53_loss") and ("ultra" in burden.lower() or "high" in burden.lower()):
            return "Lymphoma-context CNA-high TP53-axis candidate pattern", "Because --sample_set lymphoma was supplied, the TP53-axis interpretation is kept in lymphoma context. Complex CNA plus TP53-region loss supports a lymphoma CNA-high/TP53-axis candidate pattern but is not a formal subtype call without TP53 mutation and pathology."
        if has("2p16_REL_BCL11A_gain_amp") and has("18q21_BCL2_MALT1_gain_amp"):
            return "Lymphoma-context B-cell oncogene-gain CNA pattern", "Combined 2p16 and 18q21 gains/amplifications support a B-cell lymphoma oncogene-gain CNA pattern; correlate with REL/BCL2 expression and rearrangement data."
        if has("8q24_MYC_gain_amp") and (has("18q21_BCL2_MALT1_gain_amp") or has("2p16_REL_BCL11A_gain_amp") or has("3q27_BCL6_alteration")):
            return "Lymphoma-context multi-oncogene gain/amplification CNA pattern", "MYC-region gain with additional lymphoma oncogene-region gains indicates an amplification/gain-rich lymphoma-context profile. Tumor subtype requires pathology and orthogonal genomic data."
        if has("9p21_CDKN2A_B_loss") and (has_6q or has("10q23_PTEN_loss") or has("1p_loss")):
            return "Lymphoma-context deletion-rich tumor-suppressor CNA pattern", "CDKN2A/B-region loss with additional lymphoma-relevant suppressor-region deletions supports a deletion-rich lymphoma CNA profile; it is supportive, not subtype-definitive."
        if "gain-dominant" in safe_str(row.get("gain_loss_direction_class", "")):
            return "Lymphoma-context gain-dominant CNA pattern", "The CNA profile is dominated by gains, but --sample_set lymphoma suppresses non-lymphoma labels. Driver-region interpretation depends on which lymphoma oncogene loci are included."
        return "Lymphoma-context CNA pattern, subtype-unspecified", "--sample_set lymphoma was supplied. CNA abnormalities are present, but the current catalog did not identify a highly specific lymphoma CNA combination."

    if not broad_mode:
        # Context-specific but not lymphoma.  Keep the label within the requested context.
        return f"{context} context CNA pattern", f"--sample_set {context} was supplied, so knowledge interpretation is constrained to that context. Driver-region interpretation is supportive and should be integrated with pathology and orthogonal molecular tests."

    # Broad cancer mode: allow pan-cancer labels.
    if has("17q12_ERBB2_gain_amp"):
        return "ERBB2/HER2-amplified CNA pattern", "17q12 ERBB2/HER2-region gain/amplification supports a HER2-amplified CNA context. Confirm with clinical-grade HER2 testing and integrate with tumor type."
    if has("7p11_EGFR_gain_amp") or (has("7_gain") and has("10_loss_GBM_context")):
        return "EGFR/chr7/chr10 CNS-glioma-like CNA pattern", "EGFR-region gain/amplification or chromosome 7 gain with chromosome 10 loss supports a CNS high-grade glioma-like CNA context when pathology/site is compatible."
    if has("5q_loss_MDS_AML") or has("7q_loss") or has("12p13_ETV6_loss") or has("21q_RUNX1_region_CNA"):
        return "Leukemia/MDS-compatible CNA pattern", "5q/7q/12p13/21q-region CNAs can support leukemia or myeloid/MDS-compatible context when sample source and pathology are compatible; fusions and mutations require separate testing."
    if has("20q_gain_colon_context") and (has("18q_loss_SMAD4_DCC") or has("8q24_MYC_gain_amp") or has("13q_gain_colon_context") or has("17p13_TP53_loss")):
        return "Colorectal-like chromosomal-instability CNA pattern", "20q gain with colorectal-type co-events such as 18q loss, 8q gain/MYC-region gain, 13q gain, or 17p loss supports a colorectal-like CIN context when pathology is compatible."
    if has("18q_loss_SMAD4_DCC") and has("9p21_CDKN2A_B_loss") and has("17p13_TP53_loss"):
        return "Pancreatic/colorectal tumor-suppressor-loss CNA pattern", "Co-occurring 9p21/CDKN2A-B, 17p/TP53 and 18q/SMAD4-DCC-region losses support a carcinoma tumor-suppressor-loss context such as pancreatic or colorectal carcinoma when pathology is compatible."
    if has("11q13_CCND1_FGF_gain_amp") and (has("1q_gain") or has("16q_loss_breast_context") or has("17q12_ERBB2_gain_amp") or has("8q24_MYC_gain_amp")):
        return "Breast/solid-tumor 11q13-amplified CNA pattern", "11q13 CCND1/FGF-region gain/amplification with breast/solid-tumor CNA context supports an 11q13-amplified epithelial-tumor pattern when pathology is compatible."
    if has("17p13_TP53_loss") and ("ultra" in burden.lower() or "high" in burden.lower()):
        return "CNA-high TP53-axis candidate pattern", "Complex CNA plus TP53-region loss supports a TP53-axis/chromosomal-instability pattern. This is not a formal subtype call without TP53 mutation, histology, and integrated molecular classification."
    if has("9p24_JAK2_PDL1_PDL2_gain_amp") and (has("2p16_REL_BCL11A_gain_amp") or has("18q21_BCL2_MALT1_gain_amp")):
        return "Immune-evasion enriched lymphoma-compatible CNA pattern", "9p24 gain/amplification with additional lymphoma-associated driver CNAs suggests immune-evasion/JAK-STAT-region biology; correlate with morphology, CD30/PD-L1 expression, EBV status, and SV data."
    if has("2p16_REL_BCL11A_gain_amp") and has("18q21_BCL2_MALT1_gain_amp"):
        return "B-cell lymphoma oncogene-gain CNA pattern", "Combined 2p16 and 18q21 gains/amplifications support a B-cell lymphoma oncogene-gain CNA pattern; correlate with REL/BCL2 expression and rearrangement data."
    if has("8q24_MYC_gain_amp") and (has("18q21_BCL2_MALT1_gain_amp") or has("2p16_REL_BCL11A_gain_amp") or has("11q13_CCND1_FGF_gain_amp")):
        return "Multi-oncogene gain/amplification CNA pattern", "MYC-region gain with additional oncogene-region gains indicates an amplification/gain-rich profile. Tumor-type assignment requires pathology and orthogonal genomic data."
    if has("9p21_CDKN2A_B_loss") and (has_6q or has("10q23_PTEN_loss") or has("1p_loss") or has("18q_loss_SMAD4_DCC")):
        return "Deletion-rich tumor-suppressor CNA pattern", "CDKN2A/B-region loss with additional suppressor-region deletions supports a deletion-rich CNA profile. This can be biologically important but does not define a subtype by itself."
    if "gain-dominant" in safe_str(row.get("gain_loss_direction_class", "")):
        return "Gain-dominant CNA pattern", "The CNA profile is dominated by gains. Driver-region interpretation depends on which oncogene loci are included."
    if "deletion" in safe_str(row.get("gain_loss_direction_class", "")).lower() or "loss" in safe_str(row.get("gain_loss_direction_class", "")).lower():
        return "Deletion-dominant CNA pattern", "The CNA profile is dominated by losses. Tumor-suppressor-region involvement should be reviewed with orthogonal data."
    return rule.replace("_", " "), "CNA-based pattern retained from the rule-based classifier. Literature-derived annotations are supportive only."




class LiteratureClient:
    def __init__(self, cache_dir: Path, timeout: float = 20, user_agent: str | None = None, sleep: float = 0.25):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.user_agent = user_agent or "OncoTracerAI-CNA-knowledge-enrichment/1.0 (research; contact: user-provided)"
        self.sleep = sleep
        self.session = requests.Session() if requests else None
        if self.session:
            self.session.headers.update({"User-Agent": self.user_agent})
        self.errors: list[str] = []
        self.disabled = False
        self.consecutive_errors = 0

    def _key(self, payload: str) -> Path:
        h = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
        return self.cache_dir / f"{h}.json"

    def get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        if self.disabled:
            return None
        if self.session is None:
            self.errors.append("requests_not_available")
            self.disabled = True
            return None
        full_key = url + "?" + urlencode(sorted((k, str(v)) for k, v in params.items()))
        cp = self._key(full_key)
        if cp.exists():
            try:
                return json.loads(cp.read_text())
            except Exception:
                cp.unlink(missing_ok=True)
        try:
            resp = self.session.get(url, params=params, timeout=self.timeout)
            resp.raise_for_status()
            data = resp.json()
            cp.write_text(json.dumps(data, indent=2, ensure_ascii=False))
            self.consecutive_errors = 0
            if self.sleep:
                time.sleep(self.sleep)
            return data
        except Exception as exc:
            self.consecutive_errors += 1
            self.errors.append(f"{url}: {type(exc).__name__}: {exc}")
            # Avoid waiting on dozens of timeouts when the server has no internet.
            if self.consecutive_errors >= 2:
                self.disabled = True
                self.errors.append("web_enrichment_disabled_after_repeated_connection_errors")
            return None

    def europepmc_search(self, query: str, page_size: int = 6, sort: str = "CITED desc") -> list[dict[str, Any]]:
        """Search Europe PMC. Uses cache, returns metadata and abstracts when available."""
        url = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
        params = {
            "query": query,
            "format": "json",
            "pageSize": str(page_size),
            "resultType": "core",
            "sort": sort,
        }
        data = self.get_json(url, params)
        if not data:
            return []
        results = (((data or {}).get("resultList") or {}).get("result") or [])
        out = []
        for r in results:
            out.append({
                "source": "EuropePMC",
                "pmid": safe_str(r.get("pmid")),
                "pmcid": safe_str(r.get("pmcid")),
                "doi": safe_str(r.get("doi")),
                "title": safe_str(r.get("title")),
                "journal": safe_str(r.get("journalTitle")),
                "year": safe_str(r.get("pubYear")),
                "authors": safe_str(r.get("authorString")),
                "cited_by_count": safe_str(r.get("citedByCount")),
                "abstract": safe_str(r.get("abstractText")),
                "url": ("https://pubmed.ncbi.nlm.nih.gov/" + safe_str(r.get("pmid")) + "/") if r.get("pmid") else safe_str(r.get("doi")),
                "query": query,
                "query_sort": sort,
            })
        return out

    def europepmc_by_pmid(self, pmid: str) -> dict[str, Any] | None:
        pmid = safe_str(pmid).strip()
        if not pmid:
            return None
        got = self.europepmc_search(f"EXT_ID:{pmid} AND SRC:MED", page_size=1, sort="CITED desc")
        if got:
            g = dict(got[0])
            g["query"] = f"EXT_ID:{pmid} AND SRC:MED"
            g["source"] = "EuropePMC seed PMID metadata"
            return g
        return None


def run_hf_ner(text: str, model_name: str, max_chars: int = 6000) -> list[str]:
    """Optional Hugging Face NER.  Fails closed when transformers/torch are absent."""
    if not text.strip():
        return []
    try:
        from transformers import pipeline  # type: ignore
        nlp = pipeline("token-classification", model=model_name, aggregation_strategy="simple")
        ents = nlp(text[:max_chars])
        labels = []
        for e in ents:
            word = safe_str(e.get("word") or e.get("entity_group") or "").strip()
            group = safe_str(e.get("entity_group") or e.get("entity") or "").strip()
            if word:
                labels.append(f"{word} [{group}]" if group else word)
        # stable unique order
        seen = set(); out = []
        for x in labels:
            if x not in seen:
                seen.add(x); out.append(x)
        return out[:40]
    except Exception:
        return []



def canonical_sample_set(value: Any) -> str:
    raw = safe_str(value) or "broad_cancer"
    head = re.split(r"[:=]", raw, maxsplit=1)[0]
    key = re.sub(r"[^a-z0-9]+", "_", head.lower()).strip("_")
    aliases = {
        "pan": "broad_cancer", "pancancer": "broad_cancer", "pan_cancer": "broad_cancer", "broad": "broad_cancer", "broad_cancer": "broad_cancer", "all": "broad_cancer", "all_cancers": "broad_cancer",
        "lymphomas": "lymphoma", "dlbcl": "lymphoma", "b_cell_lymphoma": "lymphoma", "bcell_lymphoma": "lymphoma", "hematolymphoid": "lymphoma",
        "brain": "brain_cns", "cns": "brain_cns", "glioma": "brain_cns", "glioblastoma": "brain_cns", "astrocytoma": "brain_cns", "meningioma": "brain_cns",
        "mammary": "breast", "breast_cancer": "breast",
        "pancreatic": "pancreas", "pancreatic_cancer": "pancreas", "pancreatobiliary": "pancreas", "biliary": "pancreas", "cholangiocarcinoma": "pancreas",
        "colon": "colorectal", "crc": "colorectal", "rectal": "colorectal", "rectum": "colorectal",
        "leukaemia": "leukemia", "aml": "leukemia", "all_leukemia": "leukemia", "mds": "leukemia", "myeloid": "leukemia", "hematologic": "leukemia", "haematologic": "leukemia",
        "nsclc": "lung", "sclc": "lung", "pulmonary": "lung",
        "prostatic": "prostate", "ovary": "ovarian", "fallopian_tube": "ovarian", "peritoneal": "ovarian", "hgsoc": "ovarian",
        "endometrium": "endometrial", "uterine": "endometrial",
        "stomach": "gastric_esophageal", "gastric": "gastric_esophageal", "gastroesophageal": "gastric_esophageal", "gej": "gastric_esophageal", "esophageal": "gastric_esophageal", "oesophageal": "gastric_esophageal",
        "soft_tissue": "sarcoma", "gist": "sarcoma", "liposarcoma": "sarcoma", "leiomyosarcoma": "sarcoma", "osteosarcoma": "sarcoma",
        "kidney": "renal", "rcc": "renal", "clear_cell_rcc": "renal", "ccrcc": "renal", "bladder": "urothelial", "urinary_tract": "urothelial",
        "hcc": "liver", "hepatocellular": "liver", "hnscc": "head_neck", "oral": "head_neck", "oropharyngeal": "head_neck", "laryngeal": "head_neck",
        "testicular": "germ_cell", "seminoma": "germ_cell", "nonseminoma": "germ_cell", "multiple_myeloma": "myeloma", "plasma_cell": "myeloma",
        "net": "neuroendocrine", "neuroendocrine_tumor": "neuroendocrine", "pediatric": "pediatric_solid", "paediatric": "pediatric_solid",
    }
    return aliases.get(key, key or "broad_cancer")



LYMPHOMA_ALLOWED_FEATURES = {
    "1p_loss", "1q_gain", "2p16_REL_BCL11A_gain_amp", "3q27_BCL6_alteration",
    "6q21_PRDM1_loss", "6q23_TNFAIP3_loss", "7_gain", "8q24_MYC_gain_amp",
    "9p24_JAK2_PDL1_PDL2_gain_amp", "9p21_CDKN2A_B_loss", "10q23_PTEN_loss",
    "11q_loss", "12p13_ETV6_loss", "12q15_MDM2_CDK4_gain_amp", "13q14_loss",
    "15q21_B2M_loss", "16p13_CIITA_loss", "17p13_TP53_loss",
    "18q21_BCL2_MALT1_gain_amp", "19_loss", "21q_RUNX1_region_CNA", "22q_loss",
}

CONTEXT_DENY_PATTERNS = {
    "lymphoma": [
        "GBM_context", "glioma", "BRAF_KIAA1549", "EGFR", "ERBB2", "HER2",
        "colon_context", "breast_context", "RCC_context", "germ_cell_context",
        "MYCN", "AR_gain", "pancreas", "pancreatic", "SMAD4_DCC", "KIT_PDGFRA",
    ],
    "brain_cns": ["ERBB2", "HER2", "breast_context", "colon_context", "germ_cell_context", "BCL2_MALT1"],
    "breast": ["GBM_context", "BRAF_KIAA1549", "germ_cell_context", "colon_context"],
    "pancreas": ["GBM_context", "BRAF_KIAA1549", "germ_cell_context", "breast_context"],
    "colorectal": ["GBM_context", "BRAF_KIAA1549", "germ_cell_context", "breast_context"],
    "leukemia": ["GBM_context", "BRAF_KIAA1549", "ERBB2", "HER2", "colon_context", "breast_context", "germ_cell_context"],
}

def feature_allowed_in_context(feature_id: Any, cancer_type: Any) -> bool:
    fid = safe_str(feature_id)
    context = canonical_sample_set(cancer_type)
    if context in {"broad_cancer", "pan_cancer", "all", "all_cancers"}:
        return True
    if context == "lymphoma":
        return fid in LYMPHOMA_ALLOWED_FEATURES
    denied = CONTEXT_DENY_PATTERNS.get(context, [])
    if any(pat.lower() in fid.lower() for pat in denied):
        return False
    return True


def context_literature_terms(cancer_type: str, fallback_terms: str) -> str:
    context = canonical_sample_set(cancer_type)
    terms = {
        "lymphoma": 'lymphoma OR DLBCL OR "diffuse large B-cell lymphoma" OR "large B-cell lymphoma" OR "B-cell lymphoma" OR "Hodgkin lymphoma" OR PMBCL',
        "brain_cns": 'glioma OR glioblastoma OR astrocytoma OR meningioma OR "CNS tumor" OR "brain tumor"',
        "breast": 'breast cancer OR breast carcinoma OR mammary carcinoma',
        "pancreas": 'pancreatic cancer OR pancreatic carcinoma OR pancreatobiliary OR cholangiocarcinoma',
        "colorectal": 'colorectal cancer OR colon cancer OR rectal cancer OR colorectal carcinoma',
        "leukemia": 'leukemia OR AML OR ALL OR MDS OR myeloid neoplasm',
        "lung": 'lung cancer OR NSCLC OR SCLC OR pulmonary carcinoma',
        "prostate": 'prostate cancer OR prostate carcinoma',
        "ovarian": 'ovarian cancer OR high-grade serous carcinoma OR fallopian tube carcinoma',
        "endometrial": 'endometrial cancer OR uterine carcinoma',
        "gastric_esophageal": 'gastric cancer OR esophageal cancer OR gastroesophageal carcinoma',
        "sarcoma": 'sarcoma OR GIST OR liposarcoma OR leiomyosarcoma OR osteosarcoma',
        "renal": 'renal cell carcinoma OR kidney cancer OR RCC',
        "urothelial": 'urothelial carcinoma OR bladder cancer',
        "thyroid": 'thyroid carcinoma OR thyroid cancer',
        "melanoma": 'melanoma',
        "liver": 'hepatocellular carcinoma OR liver cancer OR HCC',
        "head_neck": 'head and neck cancer OR HNSCC OR oral carcinoma OR oropharyngeal carcinoma',
        "germ_cell": 'germ cell tumor OR testicular cancer OR seminoma',
        "myeloma": 'multiple myeloma OR plasma cell neoplasm',
        "neuroblastoma": 'neuroblastoma',
        "neuroendocrine": 'neuroendocrine tumor OR neuroendocrine carcinoma',
        "pediatric_solid": 'pediatric cancer OR pediatric solid tumor OR childhood cancer',
    }
    return terms.get(context, fallback_terms or 'cancer OR tumor OR tumour OR carcinoma OR leukemia OR lymphoma OR glioma OR sarcoma')


def make_query(feature_id: str, kb: dict[str, Any], lymphoma_terms: str, cancer_type: str = "broad_cancer") -> str:
    genes = [g for g in split_genes(kb.get("genes", "")) if not g.lower().startswith("chr") and "region" not in g.lower() and g.lower() != "broad"]
    display = safe_str(kb.get("display", feature_id)).replace("/", " ")
    gene_part = " OR ".join(genes[:4]) if genes else display
    context_terms = context_literature_terms(cancer_type, lymphoma_terms)
    return f"(({gene_part}) AND ({context_terms}) AND (copy number OR amplification OR deletion OR gain OR loss OR CNA OR \"copy-number alteration\"))"


def cna_query_variants(feature_id: str, kb: dict[str, Any], fallback_terms: str, cancer_type: str) -> list[str]:
    genes = [g for g in split_genes(kb.get("genes", "")) if g and not g.lower().startswith("chr") and "region" not in g.lower()]
    display = safe_str(kb.get("display") or feature_id).replace("/", " ")
    category = safe_str(kb.get("category", ""))
    context = context_literature_terms(cancer_type, fallback_terms)
    cytos = re.findall(r"\b\d+[pq]\d+(?:\.\d+)?(?:[-_a-zA-Z0-9.]*)?\b", feature_id + " " + display)
    gene_part = " OR ".join(genes[:4]) if genes else display
    queries = [
        f"(({gene_part}) AND ({context}) AND (copy number OR amplification OR deletion OR gain OR loss OR CNA OR \"copy-number alteration\"))",
        f"(({display}) AND ({context}) AND (genomic OR molecular OR copy number OR CNA))",
    ]
    if category:
        queries.append(f"(({gene_part}) AND ({category}) AND ({context}))")
    if cytos:
        queries.append(f"(({gene_part} OR {' OR '.join(cytos[:2])}) AND ({context}) AND (amplification OR deletion OR gain OR loss))")
    # stable unique order, conservative cap
    out, seen = [], set()
    for q in queries:
        q = re.sub(r"\s+", " ", q).strip()
        key = q.lower()
        if q and key not in seen:
            seen.add(key); out.append(q)
    return out[:4]


def compact_sentences(text: str, max_sentences: int = 3) -> str:
    text = re.sub(r"<[^>]+>", " ", safe_str(text))
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s+", text)
    keep, seen = [], set()
    for p0 in parts:
        p = p0.strip()
        key = p.lower()[:160]
        if len(p) < 35 or key in seen:
            continue
        seen.add(key); keep.append(p)
        if len(keep) >= max_sentences:
            break
    return " ".join(keep)


def deterministic_literature_synthesis(abstract_text: str, built: dict[str, Any], cancer_type: str) -> str:
    context = canonical_sample_set(cancer_type)
    display = safe_str(built.get("display")) or "this CNA feature"
    genes = safe_str(built.get("genes"))
    base = safe_str(built.get("biological_interpretation"))
    hint = safe_str(built.get("classification_hint"))
    extracted = compact_sentences(abstract_text, max_sentences=2)
    bits = []
    if base:
        bits.append(base)
    if extracted:
        bits.append("PubMed/Europe-PMC fallback evidence: " + extracted)
    if hint:
        bits.append("Classification relevance: " + hint)
    if not bits:
        bits.append(f"{display} ({genes}) is interpreted as a supportive CNA feature in the {context} context; no abstract-level literature synthesis was available in this run.")
    return " ".join(bits)


def get_detected_feature_ids(driver_hits: pd.DataFrame, cancer_type: str = "broad_cancer") -> list[str]:
    features: list[str] = []
    if not driver_hits.empty and "feature_id" in driver_hits.columns:
        features.extend([normalize_feature_id(x) for x in driver_hits["feature_id"].astype(str).tolist()])
    out, seen = [], set()
    for fid in features:
        fid = normalize_feature_id(fid)
        if not fid or fid.lower() in MISSING_STRINGS or fid in seen:
            continue
        if feature_allowed_in_context(fid, cancer_type):
            seen.add(fid); out.append(fid)
    return out


def _num_float(x: Any, default: float = 0.0) -> float:
    try:
        s = safe_str(x).replace(",", "").strip()
        return float(s) if s else default
    except Exception:
        return default


def score_reference_influence(ref: dict[str, Any], feature_id: str, built: dict[str, Any], cancer_type: str) -> tuple[float, str]:
    title = safe_str(ref.get("title"))
    abstract = safe_str(ref.get("abstract"))
    journal = safe_str(ref.get("journal"))
    text = f"{title} {abstract}".lower()
    genes = [g.lower() for g in split_genes(built.get("genes", "")) if g and not g.lower().startswith("chr") and "region" not in g.lower()]
    citations = _num_float(ref.get("cited_by_count"), 0.0)
    year = _num_float(ref.get("year"), 0.0)
    score = 0.0
    reasons = []
    if citations > 0:
        score += min(45.0, 10.0 * math.log1p(citations))
        reasons.append(f"citation_count={int(citations)}")
    gene_hits = [g for g in genes if re.search(r"(?<![a-z0-9])" + re.escape(g) + r"(?![a-z0-9])", text)]
    if gene_hits:
        score += min(20.0, 8.0 + 3.0 * len(gene_hits))
        reasons.append("gene_match=" + "/".join(gene_hits[:4]).upper())
    cna_words = [w for w in ["copy number", "copy-number", "amplification", "deletion", "gain", "loss", "cna"] if w in text]
    if cna_words:
        score += 12.0
        reasons.append("CNA_term_match")
    context = canonical_sample_set(cancer_type)
    ctx_terms = {
        "lymphoma": ["lymphoma", "dlbcl", "b-cell", "hodgkin", "large b-cell", "pmbl", "pmbcl"],
        "brain_cns": ["glioma", "glioblastoma", "astrocytoma", "meningioma", "cns"],
        "breast": ["breast", "mammary", "her2"],
        "pancreas": ["pancrea", "biliary", "cholangi"],
        "colorectal": ["colorectal", "colon", "rectal"],
        "leukemia": ["leukemia", "leukaemia", "aml", "all", "mds", "myeloid"],
    }.get(context, [context.replace("_", " "), "cancer", "tumor", "tumour"])
    if any(t in text for t in ctx_terms):
        score += 14.0
        reasons.append("sample_set_context_match")
    if re.search(r"review|guideline|classification|who classification|consensus|landscape|genomic", title.lower() + " " + journal.lower()):
        score += 6.0
        reasons.append("review_or_classification_signal")
    if year >= 2018:
        score += 3.0
        reasons.append("recent")
    if safe_str(ref.get("source")).lower().startswith("built-in"):
        score += 2.0
        reasons.append("curated_seed")
    return round(min(100.0, score), 3), "; ".join(reasons) if reasons else "metadata_available"


class ReferenceInfluenceLLMSelector:
    """Optional local Hugging Face LLM selector for influential references."""
    def __init__(self, model_names: str, local_files_only: bool = False, max_input_chars: int = 3600, max_new_tokens: int = 80):
        self.model_names = [m.strip() for m in safe_str(model_names).split(",") if m.strip()]
        self.local_files_only = bool(local_files_only)
        self.max_input_chars = int(max_input_chars or 3600)
        self.max_new_tokens = int(max_new_tokens or 80)
        self._pipes: dict[str, Any] = {}
        self._disabled = False

    def select(self, feature_id: str, display: str, genes: str, cancer_type: str, refs: list[dict[str, Any]], top_n: int) -> tuple[list[int], str, list[dict[str, Any]]]:
        trials: list[dict[str, Any]] = []
        if self._disabled or not self.model_names or not refs:
            return [], "disabled_or_no_refs", trials
        try:
            from transformers import pipeline  # type: ignore
        except Exception as e:
            self._disabled = True
            msg = f"transformers_unavailable: {type(e).__name__}: {e}"
            return [], msg, [{"feature_id": feature_id, "model_name": "all", "model_layer": "reference_influence_selection", "status": "failed", "message": msg}]
        candidates = []
        for i, r in enumerate(refs[:20], start=1):
            title = safe_str(r.get("title"))[:220]
            year = safe_str(r.get("year"))
            journal = safe_str(r.get("journal"))[:80]
            cited = safe_str(r.get("cited_by_count"))
            abstract = safe_str(r.get("abstract"))[:420]
            candidates.append(f"{i}. PMID {safe_str(r.get('pmid'))}; Year {year}; Cited {cited}; Journal {journal}; Title: {title}; Abstract: {abstract}")
        prompt = (
            "You are ranking biomedical papers for a molecular pathology CNA report. "
            f"Cancer context: {canonical_sample_set(cancer_type)}. CNA feature: {display}. Genes/region: {genes}. "
            f"Select the {min(top_n, len(refs))} most influential and most context-relevant papers. "
            "Prefer high citation count, direct gene/CNA evidence, disease-context match, guidelines/classification, or large genomic cohorts. "
            "Return only comma-separated paper numbers, no prose. Candidates:\n" + "\n".join(candidates)
        )
        prompt = prompt[: self.max_input_chars]
        for model_name in self.model_names:
            try:
                if model_name not in self._pipes:
                    task = "text2text-generation" if ("flan" in model_name.lower() or "t5" in model_name.lower()) else "summarization"
                    self._pipes[model_name] = pipeline(task, model=model_name, tokenizer=model_name, device=-1)
                nlp = self._pipes[model_name]
                if "text2text" in safe_str(getattr(nlp, "task", "")):
                    out = nlp(prompt, max_new_tokens=self.max_new_tokens, truncation=True)
                    txt = safe_str(out[0].get("generated_text") if out else "")
                else:
                    out = nlp(prompt, max_length=min(120, self.max_new_tokens + 40), min_length=8, do_sample=False, truncation=True)
                    txt = safe_str(out[0].get("summary_text") if out else "")
                nums = []
                for m in re.finditer(r"\b(\d{1,2})\b", txt):
                    n = int(m.group(1))
                    if 1 <= n <= len(refs) and (n - 1) not in nums:
                        nums.append(n - 1)
                    if len(nums) >= top_n:
                        break
                if nums:
                    trials.append({"feature_id": feature_id, "model_name": model_name, "model_layer": "reference_influence_selection", "status": "completed", "message": txt[:500]})
                    return nums, model_name, trials
                trials.append({"feature_id": feature_id, "model_name": model_name, "model_layer": "reference_influence_selection", "status": "failed", "message": "no_parseable_indices: " + txt[:300]})
            except Exception as e:
                trials.append({"feature_id": feature_id, "model_name": model_name, "model_layer": "reference_influence_selection", "status": "failed", "message": f"{type(e).__name__}: {str(e)[:240]}"})
        return [], "no_llm_reference_selector_completed", trials


def merge_reference_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for r0 in records:
        r = dict(r0)
        key = (safe_str(r.get("pmid")) or safe_str(r.get("doi")) or safe_str(r.get("title"))).lower().strip()
        if not key:
            continue
        old = by_key.get(key)
        if old is None:
            by_key[key] = r
            continue
        old_seed = safe_str(old.get("title")) == "PMID seed from built-in CNA knowledge dictionary"
        new_seed = safe_str(r.get("title")) == "PMID seed from built-in CNA knowledge dictionary"
        if old_seed and not new_seed:
            by_key[key] = r
        else:
            for k, v in r.items():
                if safe_str(v) and not safe_str(old.get(k)):
                    old[k] = v
    return list(by_key.values())


def rank_and_select_references(
    feature_id: str,
    built: dict[str, Any],
    refs: list[dict[str, Any]],
    cancer_type: str,
    top_n: int,
    selector: ReferenceInfluenceLLMSelector | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not refs:
        return [], []
    refs = merge_reference_records(refs)
    scored = []
    for r in refs:
        rr = dict(r)
        score, reason = score_reference_influence(rr, feature_id, built, cancer_type)
        rr["influence_score"] = score
        rr["influence_reason"] = reason
        rr["selected_influential"] = "false"
        rr["influence_rank"] = ""
        rr["llm_selection_model"] = ""
        rr["llm_selection_status"] = "not_attempted"
        scored.append(rr)
    scored.sort(key=lambda x: (_num_float(x.get("influence_score"), 0), _num_float(x.get("cited_by_count"), 0), _num_float(x.get("year"), 0)), reverse=True)
    trials: list[dict[str, Any]] = []
    selected_idx: list[int] = []
    model_used = "deterministic_influence_score"
    if selector is not None and scored:
        selected_idx, model_used, trials = selector.select(
            feature_id=feature_id,
            display=safe_str(built.get("display", feature_id)),
            genes=safe_str(built.get("genes", "")),
            cancer_type=cancer_type,
            refs=scored,
            top_n=top_n,
        )
    if not selected_idx:
        selected_idx = list(range(min(top_n, len(scored))))
        if not model_used.startswith("deterministic"):
            model_used = f"deterministic_fallback_after_{model_used}"
    selected_set = set(selected_idx[:top_n])
    rank = 1
    ordered_indices = list(selected_idx[:top_n]) + [i for i in range(len(scored)) if i not in selected_set]
    final, seen = [], set()
    for i in ordered_indices:
        if i in seen or i >= len(scored):
            continue
        seen.add(i)
        rr = dict(scored[i])
        rr["selection_method"] = "citation_relevance_plus_optional_huggingface_llm_selection"
        if i in selected_set:
            rr["selected_influential"] = "true"
            rr["influence_rank"] = rank
            rr["llm_selection_model"] = model_used
            rr["llm_selection_status"] = "selected_by_llm" if not model_used.startswith("deterministic") else "selected_by_deterministic_score"
            rank += 1
        else:
            rr["llm_selection_model"] = model_used
            rr["llm_selection_status"] = "not_selected_top_reference"
        final.append(rr)
    return final, trials


class LiteratureLLMSynthesizer:
    def __init__(self, model_names: str, local_files_only: bool = False, max_input_chars: int = 2800, max_new_tokens: int = 96):
        self.model_names = [m.strip() for m in safe_str(model_names).split(",") if m.strip()]
        self.local_files_only = bool(local_files_only)
        self.max_input_chars = int(max_input_chars or 2800)
        self.max_new_tokens = int(max_new_tokens or 96)
        self._pipes: dict[str, Any] = {}
        self._disabled = False

    def synthesize(self, feature_id: str, display: str, genes: str, cancer_type: str, text: str) -> tuple[str, str, list[dict[str, Any]]]:
        trials: list[dict[str, Any]] = []
        if self._disabled or not self.model_names or not safe_str(text):
            return "", "disabled_or_no_text", trials
        try:
            from transformers import pipeline  # type: ignore
        except Exception as e:
            self._disabled = True
            msg = f"transformers_unavailable: {type(e).__name__}: {e}"
            return "", msg, [{"feature_id": feature_id, "model_name": "all", "model_layer": "literature_synthesis", "status": "failed", "message": msg}]
        prompt = (
            "You are writing a concise molecular pathology CNA report. "
            f"Cancer context: {canonical_sample_set(cancer_type)}. CNA feature: {display}. Genes/region: {genes}. "
            "Using only the literature text below, write exactly two sentences: "
            "(1) biological relevance of this CNA feature; (2) diagnostic/classification caveat. "
            "Do not invent drugs or diagnoses. Literature text: " + safe_str(text)[: self.max_input_chars]
        )
        for model_name in self.model_names:
            try:
                if model_name not in self._pipes:
                    task = "text2text-generation" if ("flan" in model_name.lower() or "t5" in model_name.lower()) else "summarization"
                    self._pipes[model_name] = pipeline(task, model=model_name, tokenizer=model_name, device=-1)
                nlp = self._pipes[model_name]
                if "text2text" in safe_str(getattr(nlp, "task", "")):
                    out = nlp(prompt, max_new_tokens=self.max_new_tokens, truncation=True)
                    txt = safe_str(out[0].get("generated_text") if out else "")
                else:
                    out = nlp(prompt, max_length=min(160, max(50, self.max_new_tokens + 40)), min_length=25, do_sample=False, truncation=True)
                    txt = safe_str(out[0].get("summary_text") if out else "")
                txt = re.sub(r"\s+", " ", txt).strip()
                if len(txt) >= 40:
                    trials.append({"feature_id": feature_id, "model_name": model_name, "model_layer": "literature_synthesis", "status": "completed", "message": txt[:500]})
                    return txt, model_name, trials
                trials.append({"feature_id": feature_id, "model_name": model_name, "model_layer": "literature_synthesis", "status": "failed", "message": "empty_or_too_short_generation"})
            except Exception as e:
                trials.append({"feature_id": feature_id, "model_name": model_name, "model_layer": "literature_synthesis", "status": "failed", "message": f"{type(e).__name__}: {str(e)[:240]}"})
        return "", "no_llm_model_completed", trials


def build_sample_literature(
    sample_knowledge: pd.DataFrame,
    feature_kb: pd.DataFrame,
    references: pd.DataFrame,
    web: bool,
    client: LiteratureClient | None,
    deep_literature: bool,
    deep_max_papers_per_feature: int,
    top_papers_per_sample: int,
    lymphoma_terms: str,
    cancer_type: str,
    enable_llm_ranker: bool,
    ranker_models: str,
    ranker_local_files_only: bool,
    ranker_max_candidates_per_sample: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if sample_knowledge.empty or "sample" not in sample_knowledge.columns:
        empty = pd.DataFrame(columns=["sample", "feature_id", "feature_display", "paper_rank", "influence_score", "pmid", "title", "journal", "year", "url", "cited_by_count", "selection_method", "llm_selection_model", "influence_reason"])
        return empty, pd.DataFrame(columns=["sample", "n_candidate_papers", "n_selected_papers", "n_features_with_literature", "top_paper_pmids", "top_paper_titles", "top_paper_influence_scores", "literature_selection_method"]), pd.DataFrame(columns=["sample", "feature_id", "model_name", "model_layer", "status", "message"])
    kb = feature_kb.set_index("feature_id").to_dict(orient="index") if not feature_kb.empty and "feature_id" in feature_kb.columns else {}
    refs_by_feature: dict[str, list[dict[str, Any]]] = {}
    if not references.empty and "feature_id" in references.columns:
        for fid, sub in references.groupby(references["feature_id"].astype(str)):
            refs_by_feature[normalize_feature_id(fid)] = sub.to_dict(orient="records")
    ranker = ReferenceInfluenceLLMSelector(
        ranker_models,
        local_files_only=ranker_local_files_only,
        max_input_chars=3600,
        max_new_tokens=80,
    ) if enable_llm_ranker else None
    records: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    trials: list[dict[str, Any]] = []
    for sample, sub in sample_knowledge.groupby(sample_knowledge["sample"].astype(str)):
        candidate_refs: list[dict[str, Any]] = []
        features = [normalize_feature_id(x) for x in sub.get("feature_id", pd.Series(dtype=str)).astype(str).tolist()]
        features = [f for f in features if f and f != "none_detected" and feature_allowed_in_context(f, cancer_type)]
        for fid in features:
            built = dict(kb.get(fid, BUILTIN_FEATURE_KB.get(fid, {})))
            for r in refs_by_feature.get(fid, []):
                rr = dict(r); rr["feature_id"] = fid; candidate_refs.append(rr)
            if deep_literature and web and client is not None:
                for q in cna_query_variants(fid, built, lymphoma_terms, cancer_type):
                    for g in client.europepmc_search(q, page_size=max(3, int(deep_max_papers_per_feature or 0)), sort="CITED desc"):
                        g["feature_id"] = fid; candidate_refs.append(g)
                    for g in client.europepmc_search(q, page_size=max(3, min(8, int(deep_max_papers_per_feature or 8))), sort="P_PDATE_D desc"):
                        g["feature_id"] = fid; candidate_refs.append(g)
        # Score per feature first, then pool per sample.
        pooled: list[dict[str, Any]] = []
        for fid in features:
            built = dict(kb.get(fid, BUILTIN_FEATURE_KB.get(fid, {})))
            fid_refs = [r for r in candidate_refs if normalize_feature_id(r.get("feature_id")) == fid]
            ranked, tr = rank_and_select_references(fid, built, fid_refs, cancer_type, top_n=max(3, min(8, int(top_papers_per_sample or 12))), selector=ranker)
            for t in tr:
                t = dict(t); t["sample"] = sample; trials.append(t)
            pooled.extend(ranked)
        pooled = merge_reference_records(pooled)
        # Re-sort pooled sample papers by score and keep top N for clinician/report display.
        pooled.sort(key=lambda r: (_num_float(r.get("influence_score"), 0), _num_float(r.get("cited_by_count"), 0), _num_float(r.get("year"), 0)), reverse=True)
        top_n = max(1, int(top_papers_per_sample or 12))
        selected = pooled[:top_n]
        for i, r in enumerate(selected, start=1):
            records.append({
                "sample": sample,
                "feature_id": normalize_feature_id(r.get("feature_id")),
                "feature_display": safe_str(kb.get(normalize_feature_id(r.get("feature_id")), {}).get("display", normalize_feature_id(r.get("feature_id")))),
                "paper_rank": i,
                "influence_score": r.get("influence_score", ""),
                "pmid": r.get("pmid", ""),
                "title": r.get("title", ""),
                "journal": r.get("journal", ""),
                "year": r.get("year", ""),
                "url": r.get("url", ""),
                "cited_by_count": r.get("cited_by_count", ""),
                "selection_method": r.get("selection_method", r.get("llm_selection_status", "")),
                "llm_selection_model": r.get("llm_selection_model", ""),
                "influence_reason": r.get("influence_reason", ""),
            })
        summaries.append({
            "sample": sample,
            "n_candidate_papers": len(pooled),
            "n_selected_papers": len(selected),
            "n_features_with_literature": len(set(features)),
            "top_paper_pmids": ";".join([safe_str(r.get("pmid")) for r in selected if safe_str(r.get("pmid"))][:10]),
            "top_paper_titles": " | ".join([safe_str(r.get("title")) for r in selected if safe_str(r.get("title"))][:6]),
            "top_paper_influence_scores": ";".join([safe_str(r.get("influence_score")) for r in selected[:10]]),
            "literature_selection_method": "deep_pubmed_europepmc_scrape_plus_optional_huggingface_llm_reference_selection" if enable_llm_ranker else "deep_pubmed_europepmc_scrape_plus_deterministic_influence_score",
        })
    return pd.DataFrame(records), pd.DataFrame(summaries), pd.DataFrame(trials)


def build_feature_kb(
    region_catalog: pd.DataFrame,
    driver_hits: pd.DataFrame,
    web: bool,
    client: LiteratureClient | None,
    max_papers: int,
    lymphoma_terms: str,
    enable_hf_ner: bool,
    hf_model: str,
    cancer_type: str = "broad_cancer",
    enable_literature_llm: bool = False,
    literature_llm_models: str = "",
    literature_llm_local_files_only: bool = False,
    literature_llm_max_features: int = 24,
    literature_llm_max_input_chars: int = 2800,
    literature_llm_max_new_tokens: int = 96,
    literature_reference_llm_selection: bool = True,
    literature_top_references: int = 8,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    # Start from built-in KB, then add any catalog features missing from built-ins.
    kb_records: list[dict[str, Any]] = []
    refs: list[dict[str, Any]] = []
    llm_trial_rows: list[dict[str, Any]] = []

    catalog_by_id = {}
    if not region_catalog.empty and "feature_id" in region_catalog.columns:
        for _, r in region_catalog.iterrows():
            fid = normalize_feature_id(r.get("feature_id", ""))
            if fid and feature_allowed_in_context(fid, cancer_type):
                catalog_by_id[fid] = r.to_dict()

    # Query literature primarily for CNA features actually found in the analyzed samples.
    # If no driver hit exists, fall back to context-allowed catalog features so output schemas remain stable.
    detected_feature_ids = get_detected_feature_ids(driver_hits, cancer_type=cancer_type)
    if detected_feature_ids:
        feature_ids = [fid for fid in detected_feature_ids if feature_allowed_in_context(fid, cancer_type)]
    else:
        feature_ids = [fid for fid in list(dict.fromkeys(list(catalog_by_id.keys()) + list(BUILTIN_FEATURE_KB.keys()))) if feature_allowed_in_context(fid, cancer_type)]
    errors = []
    llm_synth = LiteratureLLMSynthesizer(
        literature_llm_models,
        local_files_only=literature_llm_local_files_only,
        max_input_chars=literature_llm_max_input_chars,
        max_new_tokens=literature_llm_max_new_tokens,
    ) if enable_literature_llm else None
    llm_attempts = 0
    ref_selector = ReferenceInfluenceLLMSelector(
        literature_llm_models,
        local_files_only=literature_llm_local_files_only,
        max_input_chars=max(3200, literature_llm_max_input_chars),
        max_new_tokens=min(120, max(40, literature_llm_max_new_tokens)),
    ) if (enable_literature_llm and literature_reference_llm_selection) else None

    for fid in feature_ids:
        built = dict(BUILTIN_FEATURE_KB.get(fid, {}))
        cat = catalog_by_id.get(fid, {})
        genes = safe_str(built.get("genes") or cat.get("genes") or "")
        display = safe_str(built.get("display") or cat.get("label") or fid)
        built.setdefault("genes", genes)
        built.setdefault("display", display)
        built.setdefault("category", "CNA feature")
        built.setdefault("biological_interpretation", "CNA feature detected in the pan-cancer CNA region catalog. Interpret in the context of histology and integrated molecular data.")
        built.setdefault("classification_hint", "Supportive CNA pattern feature.")
        built.setdefault("caveat", "CNA-only evidence is supportive, not diagnostic by itself.")
        built.setdefault("tier", "supportive-CNA")
        built.setdefault("seed_pmids", [])

        feature_refs = []
        for pmid in built.get("seed_pmids", []) or []:
            seed_row = client.europepmc_by_pmid(str(pmid)) if (web and client is not None and hasattr(client, "europepmc_by_pmid")) else None
            if seed_row:
                seed_row["feature_id"] = fid
                feature_refs.append(seed_row)
            else:
                feature_refs.append({
                    "feature_id": fid,
                    "source": "built-in PMID seed",
                    "pmid": str(pmid),
                    "pmcid": "",
                    "doi": "",
                    "title": "PMID seed from built-in CNA knowledge dictionary",
                    "journal": "",
                    "year": "",
                    "authors": "",
                    "cited_by_count": "",
                    "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                    "query": "built-in seed",
                    "abstract": "",
                })
        if web and client is not None:
            # Deep PubMed/Europe-PMC scraping: query multiple gene/region/context variants,
            # merge metadata, then rank/select the influential papers below.
            queries = cna_query_variants(fid, built, fallback_terms=lymphoma_terms, cancer_type=cancer_type)
            per_query = max(4, int(max_papers))
            for q in queries:
                got = client.europepmc_search(q, page_size=per_query, sort="CITED desc")
                for g in got:
                    g["feature_id"] = fid
                    feature_refs.append(g)
                # A second small recency query helps newly published classifier papers.
                got_recent = client.europepmc_search(q, page_size=max(3, min(8, per_query // 2)), sort="P_PDATE_D desc")
                for g in got_recent:
                    g["feature_id"] = fid
                    feature_refs.append(g)

        seen = set(); dedup_refs = []
        for r in feature_refs:
            key = safe_str(r.get("pmid")) or safe_str(r.get("doi")) or safe_str(r.get("title"))
            key = key.lower()
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            dedup_refs.append(r)
        dedup_refs, selection_trials = rank_and_select_references(
            feature_id=fid,
            built=built,
            refs=dedup_refs,
            cancer_type=cancer_type,
            top_n=int(literature_top_references or 8),
            selector=ref_selector,
        )
        for tr in selection_trials:
            tr = dict(tr)
            tr.setdefault("feature_id", fid)
            tr.setdefault("display", display)
            tr.setdefault("cancer_type", canonical_sample_set(cancer_type))
            llm_trial_rows.append(tr)
        refs.extend(dedup_refs)

        selected_refs = [r for r in dedup_refs if safe_str(r.get("selected_influential")).lower() == "true"] or dedup_refs[: int(literature_top_references or 8)]
        abstracts = " ".join([safe_str(r.get("title")) + ". " + safe_str(r.get("abstract")) for r in selected_refs if r.get("abstract") or r.get("title")])
        hf_entities = run_hf_ner(abstracts, hf_model) if enable_hf_ner else []

        llm_text = ""
        llm_status = "not_enabled"
        llm_model_used = ""
        if llm_synth is not None and abstracts.strip() and llm_attempts < int(literature_llm_max_features or 0):
            llm_attempts += 1
            llm_text, llm_model_used, trials = llm_synth.synthesize(fid, display, genes, cancer_type, abstracts)
            llm_status = "completed" if llm_text else (llm_model_used or "failed")
            for tr in trials:
                tr = dict(tr)
                tr.setdefault("feature_id", fid)
                tr.setdefault("display", display)
                tr.setdefault("cancer_type", canonical_sample_set(cancer_type))
                llm_trial_rows.append(tr)
        elif llm_synth is not None and abstracts.strip():
            llm_status = f"not_attempted_max_features_{literature_llm_max_features}"
        elif llm_synth is not None:
            llm_status = "not_attempted_no_literature_text"

        deterministic = deterministic_literature_synthesis(abstracts, built, cancer_type)
        literature_synthesis = llm_text or deterministic
        literature_synthesis_source = "huggingface_llm" if llm_text else "deterministic_pubmed_text_fallback"

        bio = safe_str(built.get("biological_interpretation", "")) or deterministic
        hint = safe_str(built.get("classification_hint", "")) or "Supportive CNA pattern feature."
        caveat = safe_str(built.get("caveat", "")) or "CNA-only evidence is supportive, not diagnostic by itself."

        kb_records.append({
            "feature_id": fid,
            "display": display,
            "genes": genes,
            "category": built.get("category", "CNA feature"),
            "tier": built.get("tier", "supportive-CNA"),
            "biological_interpretation": bio,
            "classification_hint": hint,
            "caveat": caveat,
            "literature_synthesis": literature_synthesis,
            "literature_synthesis_source": literature_synthesis_source,
            "literature_llm_model_used": llm_model_used,
            "literature_llm_status": llm_status,
            "n_seed_pmids": len(built.get("seed_pmids", []) or []),
            "n_web_references": sum(1 for r in dedup_refs if r.get("source") == "EuropePMC"),
            "n_selected_influential_references": sum(1 for r in dedup_refs if safe_str(r.get("selected_influential")).lower() == "true"),
            "top_pmids": ";".join([safe_str(r.get("pmid")) for r in selected_refs if r.get("pmid")][:8]),
            "top_reference_titles": " | ".join([safe_str(r.get("title")) for r in selected_refs if safe_str(r.get("title")) and safe_str(r.get("title")) != "PMID seed from built-in CNA knowledge dictionary"][:5]),
            "reference_selection_method": "PubMed/Europe-PMC cited/recent metadata plus optional local Hugging Face reference selection",
            "hf_entities": "; ".join(hf_entities),
        })
    metrics = {
        "knowledge_features": len(kb_records),
        "references": len(refs),
        "web_errors": client.errors if client else errors,
        "cancer_type": canonical_sample_set(cancer_type),
        "literature_llm_enabled": bool(enable_literature_llm),
        "literature_llm_attempted_features": int(llm_attempts),
        "literature_llm_completed_features": int(sum(1 for r in kb_records if r.get("literature_synthesis_source") == "huggingface_llm")),
        "detected_feature_count": len(detected_feature_ids),
        "literature_reference_llm_selection_enabled": bool(literature_reference_llm_selection),
        "literature_top_references_per_feature": int(literature_top_references or 8),
    }
    return pd.DataFrame(kb_records), pd.DataFrame(refs), pd.DataFrame(llm_trial_rows), metrics

def build_sample_knowledge(classification: pd.DataFrame, driver_hits: pd.DataFrame, feature_kb: pd.DataFrame, cancer_type: str = "broad_cancer") -> tuple[pd.DataFrame, pd.DataFrame]:
    kb = feature_kb.set_index("feature_id").to_dict(orient="index") if not feature_kb.empty and "feature_id" in feature_kb.columns else {}
    sample_records: list[dict[str, Any]] = []
    summary_records: list[dict[str, Any]] = []

    dh_by_sample: dict[str, pd.DataFrame] = {}
    if not driver_hits.empty and "sample" in driver_hits.columns:
        tmp = driver_hits.copy()
        tmp["sample"] = tmp["sample"].astype(str)
        for s, sub in tmp.groupby("sample"):
            dh_by_sample[str(s)] = sub

    if classification.empty or "sample" not in classification.columns:
        return pd.DataFrame(), pd.DataFrame()

    for _, row in classification.iterrows():
        sample = safe_str(row.get("sample"))
        flags = split_flags(row.get("driver_region_flags", ""))
        features: list[str] = []
        for flag in flags:
            features.extend(feature_ids_from_flag(flag))
        if sample in dh_by_sample:
            features.extend([normalize_feature_id(x) for x in dh_by_sample[sample].get("feature_id", pd.Series(dtype=str)).astype(str).tolist()])
        # stable unique order
        unique_features = []
        seen = set()
        for f in features:
            if f and f.lower() not in MISSING_STRINGS and f not in seen and feature_allowed_in_context(f, cancer_type):
                seen.add(f); unique_features.append(f)

        refined, rationale = infer_refined_class(row, unique_features, cancer_type=cancer_type)
        if not unique_features:
            sample_records.append({
                "sample": sample,
                "feature_id": "none_detected",
                "display": "No canonical CNA feature detected",
                "genes": "",
                "event_state": "",
                "event_cytoband": "",
                "tier": "none",
                "category": "CNA-flat or no catalog hit",
                "biological_interpretation": "No canonical CNA region from the context-specific region catalog was detected under the current thresholds.",
                "classification_hint": rationale,
                "caveat": "This does not exclude balanced rearrangements, mutations, expression changes, or low-purity/subclonal events.",
                "literature_synthesis": "No PubMed/Europe-PMC literature synthesis was generated because no catalog feature was detected for this sample.",
                "literature_synthesis_source": "not_applicable",
                "literature_llm_model_used": "",
                "literature_llm_status": "not_applicable",
                "n_web_references": 0,
                "n_selected_influential_references": 0,
                "top_pmids": "",
                "top_reference_titles": "",
                "reference_selection_method": "not_applicable",
            })
        else:
            for fid in unique_features:
                info = kb.get(fid, {})
                sub = dh_by_sample.get(sample, pd.DataFrame())
                hit_sub = sub[sub.get("feature_id", pd.Series(dtype=str)).astype(str) == fid] if not sub.empty and "feature_id" in sub.columns else pd.DataFrame()
                states = ";".join(sorted(set(hit_sub.get("event_state", pd.Series(dtype=str)).astype(str).tolist()))) if not hit_sub.empty else ""
                cytos = ";".join(sorted(set(hit_sub.get("event_cytoband", pd.Series(dtype=str)).astype(str).tolist()))) if not hit_sub.empty else ""
                sample_records.append({
                    "sample": sample,
                    "feature_id": fid,
                    "display": info.get("display", fid),
                    "genes": info.get("genes", ""),
                    "event_state": states,
                    "event_cytoband": cytos,
                    "tier": info.get("tier", "supportive-CNA"),
                    "category": info.get("category", "CNA feature"),
                    "biological_interpretation": info.get("biological_interpretation", "CNA feature detected."),
                    "classification_hint": info.get("classification_hint", "Supportive CNA pattern feature."),
                    "caveat": info.get("caveat", "CNA-only evidence is not diagnostic by itself."),
                    "literature_synthesis": info.get("literature_synthesis", ""),
                    "literature_synthesis_source": info.get("literature_synthesis_source", ""),
                    "literature_llm_model_used": info.get("literature_llm_model_used", ""),
                    "literature_llm_status": info.get("literature_llm_status", ""),
                    "n_web_references": info.get("n_web_references", 0),
                    "n_selected_influential_references": info.get("n_selected_influential_references", 0),
                    "top_pmids": info.get("top_pmids", ""),
                    "top_reference_titles": info.get("top_reference_titles", ""),
                    "reference_selection_method": info.get("reference_selection_method", ""),
                })

        top_features = []
        for fid in unique_features:
            info = kb.get(fid, {})
            top_features.append(info.get("display", fid))
        synth_bits = []
        llm_statuses = []
        for fid in unique_features[:6]:
            info = kb.get(fid, {})
            syn = safe_str(info.get("literature_synthesis", ""))
            if syn:
                synth_bits.append(syn)
            st = safe_str(info.get("literature_llm_status", ""))
            if st:
                llm_statuses.append(f"{fid}:{st}")
        influential_pmids = []
        influential_titles = []
        literature_strength = 0
        for fid in unique_features[:8]:
            info = kb.get(fid, {})
            n_inf = 0
            try:
                n_inf = int(float(info.get("n_selected_influential_references", 0) or 0))
            except Exception:
                n_inf = 0
            if n_inf > 0:
                literature_strength += 1
            for pm in split_field(info.get("top_pmids", "")):
                if pm and pm not in influential_pmids:
                    influential_pmids.append(pm)
            titles = safe_str(info.get("top_reference_titles", ""))
            if titles:
                influential_titles.extend([t.strip() for t in titles.split(" | ") if t.strip()])
        summary_records.append({
            "sample": sample,
            "knowledge_refined_class": refined,
            "knowledge_refined_class_rationale": rationale,
            "knowledge_literature_synthesis": " ".join(synth_bits[:3]),
            "knowledge_literature_llm_status": "; ".join(llm_statuses[:8]),
            "knowledge_literature_strength": min(8, literature_strength),
            "knowledge_influential_pmids": ";".join(influential_pmids[:12]),
            "knowledge_influential_titles": " | ".join(influential_titles[:6]),
            "n_knowledge_features": len(unique_features),
            "knowledge_features": "; ".join(top_features),
            "knowledge_feature_ids": ";".join(unique_features),
        })

    return pd.DataFrame(sample_records), pd.DataFrame(summary_records)


def write_empty_outputs(reason: str) -> None:
    pd.DataFrame(columns=["feature_id", "display", "genes", "category", "tier", "biological_interpretation", "classification_hint", "caveat", "literature_synthesis", "literature_synthesis_source", "literature_llm_model_used", "literature_llm_status", "n_seed_pmids", "n_web_references", "top_pmids", "hf_entities"]).to_csv("knowledge_base.tsv", sep="\t", index=False)
    pd.DataFrame(columns=["sample", "feature_id", "display", "genes", "event_state", "event_cytoband", "tier", "category", "biological_interpretation", "classification_hint", "caveat", "literature_synthesis", "literature_synthesis_source", "literature_llm_model_used", "literature_llm_status", "n_web_references", "top_pmids"]).to_csv("sample_knowledge.tsv", sep="\t", index=False)
    pd.DataFrame(columns=["sample", "knowledge_refined_class", "knowledge_refined_class_rationale", "knowledge_literature_synthesis", "knowledge_literature_llm_status", "n_knowledge_features", "knowledge_features", "knowledge_feature_ids"]).to_csv("sample_knowledge_summary.tsv", sep="\t", index=False)
    pd.DataFrame(columns=["feature_id", "source", "pmid", "pmcid", "doi", "title", "journal", "year", "authors", "cited_by_count", "url", "query", "abstract"]).to_csv("knowledge_references.tsv", sep="\t", index=False)
    pd.DataFrame(columns=["sample", "feature_id", "feature_display", "paper_rank", "influence_score", "pmid", "title", "journal", "year", "url"]).to_csv("sample_literature.tsv", sep="\t", index=False)
    pd.DataFrame(columns=["sample", "n_candidate_papers", "n_selected_papers", "n_features_with_literature", "top_paper_pmids", "top_paper_titles", "top_paper_influence_scores", "literature_selection_method"]).to_csv("sample_literature_summary.tsv", sep="\t", index=False)
    pd.DataFrame(columns=["sample", "feature_id", "pmid", "model_name", "status", "score_0_10", "message"]).to_csv("knowledge_literature_ranker_trials.tsv", sep="\t", index=False)
    pd.DataFrame(columns=["feature_id", "display", "cancer_type", "model_name", "status", "message"]).to_csv("knowledge_llm_trials.tsv", sep="\t", index=False)
    Path("knowledge_metrics.json").write_text(json.dumps({"status": "empty", "reason": reason}, indent=2))
    Path("knowledge_cache.json").write_text(json.dumps({"status": "empty", "reason": reason}, indent=2))


def main() -> None:
    ap = argparse.ArgumentParser(description="Build cancer-agnostic CNA knowledge enrichment tables using curated rules and optional public web literature metadata.")
    ap.add_argument("--classification", required=True)
    ap.add_argument("--clean-events", required=True)
    ap.add_argument("--driver-hits", required=True)
    ap.add_argument("--region-catalog", required=True)
    ap.add_argument("--enable-web", default="true")
    ap.add_argument("--allow-fail", default="true")
    ap.add_argument("--cache-dir", default="knowledge_http_cache")
    ap.add_argument("--max-papers", type=int, default=6)
    ap.add_argument("--timeout", type=float, default=20)
    ap.add_argument("--sleep", type=float, default=0.25)
    ap.add_argument("--lymphoma-terms", default='lymphoma OR DLBCL OR "diffuse large B-cell lymphoma" OR "large B-cell lymphoma" OR "B-cell lymphoma"')
    ap.add_argument("--cancer-terms", default="cancer OR tumor OR tumour OR carcinoma OR leukemia OR lymphoma OR glioma OR sarcoma OR CNA")
    ap.add_argument("--cancer-type", default="pan_cancer")
    ap.add_argument("--user-agent", default="OncoTracerAI-CNA-knowledge-enrichment/1.0")
    ap.add_argument("--enable-hf-ner", default="false")
    ap.add_argument("--hf-model", default="d4data/biomedical-ner-all")
    ap.add_argument("--enable-literature-llm", default="false")
    ap.add_argument("--literature-llm-models", default="google/flan-t5-small,google/flan-t5-base,Falconsai/medical_summarization")
    ap.add_argument("--literature-llm-local-files-only", default="false")
    ap.add_argument("--literature-llm-max-features", type=int, default=24)
    ap.add_argument("--literature-llm-max-input-chars", type=int, default=2800)
    ap.add_argument("--literature-llm-max-new-tokens", type=int, default=96)
    ap.add_argument("--deep-literature", default="true")
    ap.add_argument("--deep-max-papers-per-feature", type=int, default=25)
    ap.add_argument("--deep-top-papers-per-sample", type=int, default=12)
    ap.add_argument("--deep-enable-llm-ranker", default="true")
    ap.add_argument("--deep-llm-ranker-models", default="google/flan-t5-small,google/flan-t5-base,Falconsai/medical_summarization")
    ap.add_argument("--deep-llm-ranker-local-files-only", default="false")
    ap.add_argument("--deep-llm-ranker-max-candidates-per-sample", type=int, default=18)
    ap.add_argument("--literature-reference-llm-selection", default="true")
    ap.add_argument("--literature-top-references", type=int, default=8)
    args = ap.parse_args()

    def as_bool(x: Any) -> bool:
        return safe_str(x).strip().lower() in {"true", "1", "yes", "y", "on"}

    allow_fail = as_bool(args.allow_fail)
    try:
        classification = read_tsv(args.classification)
        clean_events = read_tsv(args.clean_events)
        driver_hits = read_tsv(args.driver_hits)
        region_catalog = read_tsv(args.region_catalog)
        web = as_bool(args.enable_web)
        client = LiteratureClient(Path(args.cache_dir), timeout=args.timeout, user_agent=args.user_agent, sleep=args.sleep) if web else None
        feature_kb, refs, llm_trials, metrics = build_feature_kb(
            region_catalog=region_catalog,
            driver_hits=driver_hits,
            web=web,
            client=client,
            max_papers=args.max_papers,
            lymphoma_terms=(args.cancer_terms or args.lymphoma_terms),
            enable_hf_ner=as_bool(args.enable_hf_ner),
            hf_model=args.hf_model,
            cancer_type=args.cancer_type,
            enable_literature_llm=as_bool(args.enable_literature_llm),
            literature_llm_models=args.literature_llm_models,
            literature_llm_local_files_only=as_bool(args.literature_llm_local_files_only),
            literature_llm_max_features=args.literature_llm_max_features,
            literature_llm_max_input_chars=args.literature_llm_max_input_chars,
            literature_llm_max_new_tokens=args.literature_llm_max_new_tokens,
            literature_reference_llm_selection=as_bool(args.literature_reference_llm_selection),
            literature_top_references=args.literature_top_references,
        )
        sample_k, sample_summary = build_sample_knowledge(classification, driver_hits, feature_kb, cancer_type=args.cancer_type)
        sample_lit, sample_lit_summary, ranker_trials = build_sample_literature(
            sample_knowledge=sample_k,
            feature_kb=feature_kb,
            references=refs,
            web=web,
            client=client,
            deep_literature=as_bool(args.deep_literature),
            deep_max_papers_per_feature=args.deep_max_papers_per_feature,
            top_papers_per_sample=args.deep_top_papers_per_sample,
            lymphoma_terms=(args.cancer_terms or args.lymphoma_terms),
            cancer_type=args.cancer_type,
            enable_llm_ranker=as_bool(args.deep_enable_llm_ranker),
            ranker_models=args.deep_llm_ranker_models,
            ranker_local_files_only=as_bool(args.deep_llm_ranker_local_files_only),
            ranker_max_candidates_per_sample=args.deep_llm_ranker_max_candidates_per_sample,
        )

        # Keep all outputs deterministic and easy to inspect.
        feature_kb.to_csv("knowledge_base.tsv", sep="\t", index=False)
        sample_k.to_csv("sample_knowledge.tsv", sep="\t", index=False)
        sample_summary.to_csv("sample_knowledge_summary.tsv", sep="\t", index=False)
        refs.to_csv("knowledge_references.tsv", sep="\t", index=False)
        sample_lit.to_csv("sample_literature.tsv", sep="\t", index=False)
        sample_lit_summary.to_csv("sample_literature_summary.tsv", sep="\t", index=False)
        ranker_trials.to_csv("knowledge_literature_ranker_trials.tsv", sep="\t", index=False)
        llm_trials.to_csv("knowledge_llm_trials.tsv", sep="\t", index=False)
        metrics.update({
            "status": "completed",
            "web_enabled": web,
            "hf_ner_enabled": as_bool(args.enable_hf_ner),
            "literature_llm_enabled": as_bool(args.enable_literature_llm),
            "literature_llm_models": args.literature_llm_models,
            "literature_reference_llm_selection": as_bool(args.literature_reference_llm_selection),
            "literature_top_references": args.literature_top_references,
            "samples": int(classification["sample"].nunique()) if "sample" in classification.columns else int(len(classification)),
            "sample_knowledge_rows": int(len(sample_k)),
            "sample_literature_rows": int(len(sample_lit)),
            "deep_literature_enabled": as_bool(args.deep_literature),
            "deep_literature_top_papers_per_sample": args.deep_top_papers_per_sample,
            "deep_literature_llm_ranker_enabled": as_bool(args.deep_enable_llm_ranker),
            "deep_literature_llm_ranker_trials": int(len(ranker_trials)),
        })
        Path("knowledge_metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
        Path("knowledge_cache.json").write_text(json.dumps({"feature_count": len(feature_kb), "reference_count": len(refs), "sample_literature_count": len(sample_lit), "llm_trial_count": len(llm_trials), "ranker_trial_count": len(ranker_trials), "web_errors": metrics.get("web_errors", [])}, indent=2, ensure_ascii=False))
    except Exception as exc:
        if allow_fail:
            write_empty_outputs(f"{type(exc).__name__}: {exc}")
        else:
            raise


if __name__ == "__main__":
    main()
