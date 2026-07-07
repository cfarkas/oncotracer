#!/usr/bin/env bash
set -euo pipefail

INPUT_DIR=""
OUTDIR=""
CSV_NAME="merged_probes_methyl_calls_general.csv"
TOP_N="10"
HIGH_THR="0.95"
LIKELY_THR="0.80"

usage() {
  cat <<'EOF'
Usage:
  sturgeon_consolidate_report.sh --input_dir STURGEON_OUTPUT_DIR [options]

Required:
  --input_dir DIR_OR_CSV       Sturgeon output folder, predictions folder, or a single merged_probes_methyl_calls_general.csv file.

Options:
  --outdir DIR                 Output directory. Default: same as --input_dir, or CSV parent if input is a file.
  --csv_name NAME              CSV filename to search recursively.
                               Default: merged_probes_methyl_calls_general.csv
  --top_n N                    Number of ranked classes to include per sample.
                               Default: 10
  --high_thr FLOAT             High-confidence threshold.
                               Default: 0.95
  --likely_thr FLOAT           Likely/confident threshold.
                               Default: 0.80
  -h, --help                   Show this help.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --input_dir|--input-dir|-i)
      INPUT_DIR="${2:-}"; shift 2 ;;
    --outdir|-o)
      OUTDIR="${2:-}"; shift 2 ;;
    --csv_name|--csv-name)
      CSV_NAME="${2:-}"; shift 2 ;;
    --top_n|--top-n)
      TOP_N="${2:-}"; shift 2 ;;
    --high_thr|--high-thr)
      HIGH_THR="${2:-}"; shift 2 ;;
    --likely_thr|--likely-thr)
      LIKELY_THR="${2:-}"; shift 2 ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "ERROR: unknown argument: $1" >&2
      usage >&2
      exit 1 ;;
  esac
done

if [[ -z "$INPUT_DIR" ]]; then
  echo "ERROR: --input_dir is required." >&2
  usage >&2
  exit 1
fi

if [[ ! -e "$INPUT_DIR" ]]; then
  echo "ERROR: input does not exist: $INPUT_DIR" >&2
  exit 1
fi

if [[ -z "$OUTDIR" ]]; then
  if [[ -f "$INPUT_DIR" ]]; then
    OUTDIR="$(cd "$(dirname "$INPUT_DIR")" && pwd)"
  else
    OUTDIR="$(cd "$INPUT_DIR" && pwd)"
  fi
fi

mkdir -p "$OUTDIR"

python3 - "$INPUT_DIR" "$OUTDIR" "$CSV_NAME" "$TOP_N" "$HIGH_THR" "$LIKELY_THR" <<'PY'
import csv
import html
import math
import pathlib
import re
import shutil
import subprocess
import sys
import textwrap
from collections import defaultdict
from datetime import datetime

input_arg = pathlib.Path(sys.argv[1]).expanduser()
outdir = pathlib.Path(sys.argv[2]).expanduser()
csv_name = sys.argv[3]
top_n = int(sys.argv[4])
high_thr = float(sys.argv[5])
likely_thr = float(sys.argv[6])

outdir.mkdir(parents=True, exist_ok=True)

FULL_NAMES = {
    "CONTR": "control / non-tumour reference",
    "ADENOPIT": "adenohypophysis control",
    "CEBM": "cerebellum control",
    "HEMI": "cerebral hemisphere control",
    "HYPTHAL": "hypothalamus control",
    "INFLAM": "inflammatory/reactive tissue control",
    "PINEAL": "pineal control",
    "PONS": "pons control",
    "REACT": "reactive control",
    "WM": "white matter control",

    "ATRT": "atypical teratoid/rhabdoid tumour",
    "CNS NB": "CNS neuroblastoma methylation class",
    "FOXR2": "FOXR2-activated methylation subclass",
    "ETMR": "embryonal tumour with multilayered rosettes",
    "HGNET": "high-grade neuroepithelial tumour methylation class",
    "BCOR": "BCOR-altered methylation subclass",
    "MN1": "MN1-altered methylation subclass",

    "MB G3G4": "medulloblastoma group 3/4",
    "G3": "group 3 methylation subclass",
    "G4": "group 4 methylation subclass",
    "MB SHH": "medulloblastoma SHH-activated methylation class",
    "CHL AD INF": "childhood/adolescent/infant methylation subclass",
    "MB WNT": "medulloblastoma WNT-activated methylation class",

    "EPN": "ependymoma methylation class",
    "MPE": "myxopapillary ependymoma",
    "PF A": "posterior fossa ependymoma group A",
    "PF B": "posterior fossa ependymoma group B",
    "RELA": "RELA-fusion ependymoma methylation class",
    "SPINE": "spinal ependymoma methylation class",
    "YAP": "YAP-fusion ependymoma methylation class",
    "SUBEPN": "subependymoma",

    "CN": "central neurocytoma methylation class",
    "DLGNT": "diffuse leptomeningeal glioneuronal tumour",
    "ENB": "esthesioneuroblastoma methylation class",

    "LGG": "low-grade glioma methylation class",
    "LGG PA": "low-grade glioma / pilocytic astrocytoma methylation class group",
    "PA": "pilocytic astrocytoma methylation class",
    "PA/GG ST": "pilocytic astrocytoma / ganglioglioma, supratentorial methylation class",
    "DIG/DIA": "desmoplastic infantile ganglioglioma/astrocytoma methylation class",
    "DNT": "dysembryoplastic neuroepithelial tumour methylation class",
    "GG": "ganglioglioma methylation class",
    "RGNT": "rosette-forming glioneuronal tumour methylation class",
    "LIPN": "liponeurocytoma methylation class",
    "PGG": "papillary glioneuronal tumour methylation class",
    "nC": "not otherwise coded subclass",
    "RETB": "retinoblastoma methylation class",

    "DMG": "diffuse midline glioma methylation class",
    "K27": "H3 K27-altered/K27 methylation subclass label",
    "GBM": "glioblastoma methylation class",
    "G34": "G34-mutant glioblastoma methylation subclass",
    "MES": "mesenchymal glioblastoma methylation subclass",
    "MID": "midline glioblastoma methylation subclass",
    "MYCN": "MYCN-amplified glioblastoma methylation subclass",
    "RTK I": "RTK I glioblastoma methylation subclass",
    "RTK II": "RTK II glioblastoma methylation subclass",
    "RTK III": "RTK III glioblastoma methylation subclass",

    "A IDH": "astrocytoma, IDH-mutant methylation class",
    "O IDH": "oligodendroglioma, IDH-mutant methylation class",

    "LYMPHO": "lymphoma methylation class",
    "PLASMA": "plasma cell neoplasm methylation class",
    "MELAN": "melanoma methylation class",
    "MELCYT": "melanocytoma methylation class",

    "CHORDM": "chordoma methylation class",
    "EFT": "Ewing family tumour methylation class",
    "CIC": "CIC-rearranged sarcoma methylation subclass",
    "EWS": "Ewing sarcoma methylation subclass",
    "HMB": "haemangioblastoma methylation class",
    "MNG": "meningioma methylation class",
    "SFT HMPC": "solitary fibrous tumour / haemangiopericytoma methylation class",

    "SCHW": "schwannoma methylation class",
    "SCHW MEL": "melanotic schwannoma methylation class",

    "ANA PA": "anaplastic pilocytic astrocytoma methylation class",
    "CHGL": "chordoid glioma methylation class",
    "IHG": "infant-type hemispheric glioma methylation class",
    "LGG MYB": "MYB/MYBL1-altered low-grade glioma methylation class",
    "MYB": "MYB/MYBL1-altered methylation subclass",
    "SEGA": "subependymal giant cell astrocytoma methylation class",
    "PXA": "pleomorphic xanthoastrocytoma methylation class",

    "PIN T": "pineal tumour methylation class",
    "PB A": "pineoblastoma group A",
    "PB B": "pineoblastoma group B",
    "PPT": "pineal parenchymal tumour methylation class",
    "PTPR": "papillary tumour of the pineal region methylation class",

    "PLEX": "choroid plexus tumour methylation class",
    "AD": "adult choroid plexus tumour methylation subclass",
    "PED A": "paediatric choroid plexus tumour methylation subclass A",
    "PED B": "paediatric choroid plexus tumour methylation subclass B",

    "CPH": "craniopharyngioma methylation class",
    "ADM": "adamantinomatous craniopharyngioma",
    "PAP": "papillary craniopharyngioma",

    "PITAD": "pituitary adenoma methylation class",
    "ACTH": "ACTH-producing pituitary adenoma methylation class",
    "FSH LH": "FSH/LH-producing pituitary adenoma methylation class",
    "PRL": "prolactin-producing pituitary adenoma methylation class",
    "STH": "somatotroph pituitary adenoma methylation class",
    "STH DNS A": "densely granulated somatotroph adenoma methylation subclass A",
    "STH DNS B": "densely granulated somatotroph adenoma methylation subclass B",
    "STH SPA": "sparsely granulated somatotroph adenoma methylation subclass",
    "TSH": "TSH-producing pituitary adenoma methylation class",
    "SCO GCT": "spindle cell oncocytoma / granular cell tumour methylation class",
}

def safe_float(x):
    try:
        v = float(str(x).strip())
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    except Exception:
        return None

def safe_int(x):
    v = safe_float(x)
    if v is None:
        return ""
    return int(round(v))

def parse_label(label):
    parts = re.split(r"\s+-\s+", str(label).strip(), maxsplit=2)
    while len(parts) < 3:
        parts.append("")
    return parts[0].strip(), parts[1].strip(), parts[2].strip()

def full_name(code):
    code = str(code).strip()
    return FULL_NAMES.get(code, "")

def display_full_name(code):
    return full_name(code) or "not mapped in local glossary"

def find_csvs(path):
    if path.is_file():
        return [path]
    files = sorted(path.rglob(csv_name))
    if not files:
        files = sorted(path.rglob("*methyl_calls_general.csv"))
    return files

def sample_from_path(csv_path):
    parent = csv_path.parent.name
    if parent.lower() in {"predictions", "output", "outputs"}:
        return csv_path.stem
    return parent

def confidence(score, family_score, family):
    if score >= high_thr:
        return "high_confidence"
    if score >= likely_thr:
        return "likely_or_confident_but_below_high_confidence"
    if family_score >= likely_thr:
        return "subclass_inconclusive_but_family_signal_strong"
    if family == "Control":
        return "control_or_low_tumour_fraction_possible"
    return "inconclusive_subthreshold"

def build_interpretation(top1, top2, family_score, margin):
    conf = confidence(top1["score"], family_score, top1["family"])
    class_full = top1.get("class_full_name") or top1["class_code"]
    subclass_full = top1.get("subclass_full_name") or top1["subclass_code"]

    if conf == "high_confidence":
        txt = (
            f"High-confidence methylation match to '{top1['full_label']}'. "
            f"Class full name: {class_full}. "
            f"Subclass full name: {subclass_full}."
        )
    elif conf == "likely_or_confident_but_below_high_confidence":
        txt = (
            f"Likely/confident methylation match to '{top1['full_label']}', but below the high-confidence threshold. "
            f"Class full name: {class_full}. "
            f"Subclass full name: {subclass_full}."
        )
    elif conf == "subclass_inconclusive_but_family_signal_strong":
        txt = (
            f"Subclass-level score is below threshold, but the aggregated family signal is strong for "
            f"'{top1['family']}'."
        )
    elif conf == "control_or_low_tumour_fraction_possible":
        txt = (
            f"Top match is a control/non-tumour class ('{top1['full_label']}'). "
            f"Consider low tumour fraction, non-neoplastic tissue, or insufficient tumour methylation signal."
        )
    else:
        txt = "Inconclusive/subthreshold Sturgeon result. Review the ranked classes and consider additional probes/sequencing."

    if top2 is not None and margin is not None:
        if margin >= 0.20:
            txt += f" Top1-top2 margin is large ({margin:.4f})."
        elif margin < 0.05:
            txt += f" Top1-top2 margin is small ({margin:.4f}); differential classes are close."

    txt += (
        " Sturgeon is a methylation-class similarity result; final reporting should integrate histology, "
        "tumour purity/fraction, CNV profile, and orthogonal molecular markers."
    )
    return txt, conf

def write_dict_csv(path, rows, fallback_fields):
    fields = list(rows[0].keys()) if rows else fallback_fields
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)

def pdf_escape(s):
    s = str(s)
    replacements = {
        "≥": ">=", "≤": "<=", "–": "-", "—": "-", "−": "-",
        "’": "'", "‘": "'", "“": '"', "”": '"', "×": "x",
    }
    for old, new in replacements.items():
        s = s.replace(old, new)
    s = re.sub(r"[^\x20-\x7E]", "-", s)
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

def make_text_pdf(pdf_path, text_lines):
    pdf_path = pathlib.Path(pdf_path)
    page_w = 595
    page_h = 842
    margin = 45
    y_start = 800
    bottom = 45
    max_chars = 92

    pages = []
    current = []
    y = y_start

    for text, size in text_lines:
        size = int(size)
        wrapped = textwrap.wrap(str(text), width=max_chars) or [""]
        for line in wrapped:
            line_h = max(12, size + 5)
            if y - line_h < bottom and current:
                pages.append(current)
                current = []
                y = y_start
            current.append((line, size, y))
            y -= line_h

    if current:
        pages.append(current)
    if not pages:
        pages = [[("No content", 10, y_start)]]

    font_id = 3
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        font_id: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    }

    page_ids = []
    next_id = 4

    for page in pages:
        page_id = next_id
        content_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)

        stream_lines = []
        for txt, size, ycoord in page:
            stream_lines.append(
                f"BT /F1 {size} Tf 0 0 0 rg {margin} {ycoord} Td ({pdf_escape(txt)}) Tj ET"
            )

        stream = "\n".join(stream_lines).encode("latin-1", "replace")

        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {page_w} {page_h}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {content_id} 0 R >>"
        ).encode("ascii")

        objects[content_id] = (
            b"<< /Length " + str(len(stream)).encode("ascii") +
            b" >>\nstream\n" + stream + b"\nendstream"
        )

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii")

    with pdf_path.open("wb") as fh:
        fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = {0: 0}

        for obj_id in sorted(objects):
            offsets[obj_id] = fh.tell()
            fh.write(f"{obj_id} 0 obj\n".encode("ascii"))
            fh.write(objects[obj_id])
            fh.write(b"\nendobj\n")

        xref_pos = fh.tell()
        max_id = max(objects)
        fh.write(f"xref\n0 {max_id + 1}\n".encode("ascii"))
        fh.write(b"0000000000 65535 f \n")

        for obj_id in range(1, max_id + 1):
            fh.write(f"{offsets.get(obj_id, 0):010d} 00000 n \n".encode("ascii"))

        fh.write(
            f"trailer\n<< /Size {max_id + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_pos}\n%%EOF\n".encode("ascii")
        )

def pdf_has_text(pdf_path):
    pdf_path = pathlib.Path(pdf_path)
    if not pdf_path.exists() or pdf_path.stat().st_size < 700:
        return False

    pdftotext = shutil.which("pdftotext")
    if pdftotext:
        try:
            p = subprocess.run(
                [pdftotext, str(pdf_path), "-"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=20,
                check=False,
            )
            if p.returncode == 0:
                txt = p.stdout or ""
                return (
                    "Sturgeon consolidated methylation report" in txt
                    and (
                        "Class full name" in txt
                        or "No valid results" in txt
                        or "No valid Sturgeon" in txt
                    )
                )
        except Exception:
            pass

    return pdf_path.stat().st_size >= 700

csv_files = find_csvs(input_arg)
if not csv_files:
    print(f"ERROR: no Sturgeon methylation CSV files found under: {input_arg}", file=sys.stderr)
    sys.exit(2)

summary_rows = []
top_rows = []
codebook = {}
errors = []

for csv_path in csv_files:
    try:
        with csv_path.open("r", newline="") as fh:
            reader = csv.reader(fh)
            rows = [row for row in reader if any(str(x).strip() for x in row)]

        if len(rows) < 2:
            raise ValueError("CSV has no data rows")

        header = [x.strip() for x in rows[0]]
        data_rows = rows[1:]

        number_idx = 0
        for i, h in enumerate(header):
            if h.lower() == "number_probes":
                number_idx = i
                break

        class_cols = []
        for i, label in enumerate(header):
            if i == number_idx or not label.strip():
                continue

            family, class_code, subclass_code = parse_label(label)
            rec = {
                "full_label": label.strip(),
                "family": family,
                "class_code": class_code,
                "class_full_name": full_name(class_code),
                "subclass_code": subclass_code,
                "subclass_full_name": full_name(subclass_code),
            }
            class_cols.append((i, rec))
            codebook[label.strip()] = rec

        best_row = None
        best_probes = -1.0
        for row in data_rows:
            n = safe_float(row[number_idx]) if number_idx < len(row) else None
            n = n if n is not None else -1.0
            if best_row is None or n > best_probes:
                best_row = row
                best_probes = n

        if best_row is None:
            raise ValueError("No usable data row found")

        number_probes = safe_int(best_row[number_idx]) if number_idx < len(best_row) else ""

        scores = []
        for col_i, rec in class_cols:
            score = safe_float(best_row[col_i]) if col_i < len(best_row) else None
            if score is None:
                continue
            item = dict(rec)
            item["score"] = score
            scores.append(item)

        if not scores:
            raise ValueError("No numeric class scores found")

        scores.sort(key=lambda x: x["score"], reverse=True)
        top1 = scores[0]
        top2 = scores[1] if len(scores) > 1 else None
        top3 = scores[2] if len(scores) > 2 else None
        margin = top1["score"] - top2["score"] if top2 else None

        fam_scores = defaultdict(float)
        cls_scores = defaultdict(float)
        for s in scores:
            fam_scores[s["family"]] += s["score"]
            cls_scores[(s["family"], s["class_code"])] += s["score"]

        top_family, top_family_score = max(fam_scores.items(), key=lambda kv: kv[1])
        (top_class_family, top_class_code), top_class_score = max(cls_scores.items(), key=lambda kv: kv[1])

        interp, conf = build_interpretation(top1, top2, top_family_score, margin)
        sample = sample_from_path(csv_path)

        summary_rows.append({
            "sample": sample,
            "number_probes": number_probes,
            "csv_path": str(csv_path),
            "top1_score": f"{top1['score']:.12g}",
            "top1_family": top1["family"],
            "top1_class_code": top1["class_code"],
            "top1_class_full_name": top1["class_full_name"],
            "top1_subclass_code": top1["subclass_code"],
            "top1_subclass_full_name": top1["subclass_full_name"],
            "top1_full_label": top1["full_label"],
            "top2_score": f"{top2['score']:.12g}" if top2 else "",
            "top2_full_label": top2["full_label"] if top2 else "",
           "top3_score": f"{top3['score']:.12g}" if top3 else "",
            "top3_full_label": top3["full_label"] if top3 else "",
            "top1_top2_margin": f"{margin:.12g}" if margin is not None else "",
            "top_family": top_family,
            "top_family_score_sum": f"{top_family_score:.12g}",
            "top_class_family": top_class_family,
            "top_class_code": top_class_code,
            "top_class_full_name": full_name(top_class_code),
            "top_class_score_sum": f"{top_class_score:.12g}",
            "confidence_category": conf,
            "interpretation": interp,
        })

        for rank, s in enumerate(scores[:top_n], start=1):
            top_rows.append({
                "sample": sample,
                "rank": rank,
                "score": f"{s['score']:.12g}",
                "family": s["family"],
                "class_code": s["class_code"],
                "class_full_name": s["class_full_name"],
                "subclass_code": s["subclass_code"],
                "subclass_full_name": s["subclass_full_name"],
                "full_label": s["full_label"],
                "number_probes": number_probes,
                "csv_path": str(csv_path),
            })

    except Exception as exc:
        errors.append({"file": str(csv_path), "error": str(exc)})

summary_rows.sort(key=lambda r: (r["sample"], r["csv_path"]))
top_rows.sort(key=lambda r: (r["sample"], int(r["rank"])))

summary_csv = outdir / "sturgeon_consolidated_summary.csv"
top_csv = outdir / f"sturgeon_consolidated_top{top_n}.csv"
codebook_csv = outdir / "sturgeon_class_codebook_from_headers.csv"
errors_txt = outdir / "sturgeon_consolidated_errors.txt"
report_html = outdir / "sturgeon_consolidated_report.html"
report_md = outdir / "sturgeon_consolidated_report.md"
report_pdf = outdir / "sturgeon_consolidated_report.pdf"

write_dict_csv(summary_csv, summary_rows, ["sample", "warning"])
write_dict_csv(top_csv, top_rows, ["sample", "rank", "score", "full_label"])

codebook_rows = []
for i, key in enumerate(
    sorted(codebook, key=lambda k: (codebook[k]["family"], codebook[k]["class_code"], codebook[k]["subclass_code"])),
    start=1,
):
    rec = {"label_index": i}
    rec.update(codebook[key])
    codebook_rows.append(rec)

write_dict_csv(
    codebook_csv,
    codebook_rows,
    ["label_index", "full_label", "family", "class_code", "class_full_name", "subclass_code", "subclass_full_name"],
)

if errors:
    with errors_txt.open("w") as fh:
        for e in errors:
            fh.write(f"{e['file']}\t{e['error']}\n")
else:
    errors_txt.write_text("No errors detected.\n")

now = datetime.now().astimezone().isoformat(timespec="seconds")

# Markdown report. No output-files section.
md = []
md.append("# Sturgeon consolidated methylation report")
md.append("")
md.append(f"Generated: `{now}`")
md.append(f"Input: `{input_arg}`")
md.append(f"Files processed: `{len(csv_files)}`")
md.append(f"Thresholds: high confidence score >= {high_thr}; likely/confident score >= {likely_thr} and < {high_thr}; inconclusive < {likely_thr}.")
md.append("")
md.append("## Summary")
md.append("")

for r in summary_rows:
    md.extend([
        f"### {r['sample']}",
        "",
        f"- Measured probes: `{r['number_probes']}`",
        f"- Top methylation label: `{r['top1_full_label']}`",
        f"- Family / class / subclass: `{r['top1_family']}` / `{r['top1_class_code']}` / `{r['top1_subclass_code']}`",
        f"- Class full name: `{display_full_name(r['top1_class_code'])}`",
        f"- Subclass full name: `{display_full_name(r['top1_subclass_code'])}`",
        f"- Top score: `{r['top1_score']}`",
        f"- Top2: `{r['top2_full_label']}` with score `{r['top2_score']}`",
        f"- Top1-top2 margin: `{r['top1_top2_margin']}`",
        f"- Confidence category: `{r['confidence_category']}`",
        "",
        "**Interpretation**",
        "",
        r["interpretation"],
        "",
    ])

if not summary_rows:
    md.append("No valid results were found.")
    md.append("")

md.append("## Caution")
md.append("")
md.append("This report summarizes Sturgeon methylation-class scores. It is not a standalone final clinical diagnosis. Integrate the result with histopathology, tumour fraction/purity, CNV profile, and orthogonal molecular tests.")
report_md.write_text("\n".join(md) + "\n")

# HTML report. No output-files section.
h = []
h.append("<!doctype html><html><head><meta charset='utf-8'>")
h.append("<title>Sturgeon consolidated methylation report</title>")
h.append("<style>body{font-family:Arial,sans-serif;max-width:1250px;margin:30px auto;line-height:1.45;color:#111}table{border-collapse:collapse;width:100%;margin:1em 0;font-size:14px}th,td{border:1px solid #ddd;padding:6px;text-align:left;vertical-align:top}th{background:#f3f3f3}code{background:#f6f6f6;padding:2px 4px}</style>")
h.append("</head><body>")
h.append("<h1>Sturgeon consolidated methylation report</h1>")
h.append(f"<p><b>Generated:</b> <code>{html.escape(now)}</code><br>")
h.append(f"<b>Input:</b> <code>{html.escape(str(input_arg))}</code><br>")
h.append(f"<b>Files processed:</b> <code>{len(csv_files)}</code><br>")
h.append(f"<b>Thresholds:</b> high confidence score &ge; {high_thr}; likely/confident score &ge; {likely_thr} and &lt; {high_thr}; inconclusive &lt; {likely_thr}.</p>")
h.append("<h2>Summary</h2>")

if summary_rows:
    h.append("<table><thead><tr><th>Sample</th><th>Probes</th><th>Top label</th><th>Family</th><th>Class</th><th>Class full name</th><th>Subclass</th><th>Subclass full name</th><th>Score</th><th>Top2</th><th>Margin</th><th>Confidence</th></tr></thead><tbody>")
    for r in summary_rows:
        cells = [
            r["sample"],
            r["number_probes"],
            r["top1_full_label"],
            r["top1_family"],
            r["top1_class_code"],
            display_full_name(r["top1_class_code"]),
            r["top1_subclass_code"],
            display_full_name(r["top1_subclass_code"]),
            r["top1_score"],
            r["top2_full_label"],
            r["top1_top2_margin"],
            r["confidence_category"],
        ]
        h.append("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in cells) + "</tr>")
    h.append("</tbody></table>")

    for r in summary_rows:
        h.append(f"<h2>{html.escape(str(r['sample']))}</h2>")
        h.append(f"<p><b>Class full name:</b> {html.escape(display_full_name(r['top1_class_code']))}<br>")
        h.append(f"<b>Subclass full name:</b> {html.escape(display_full_name(r['top1_subclass_code']))}</p>")
        h.append(f"<p>{html.escape(str(r['interpretation']))}</p>")
else:
    h.append("<p>No valid results were found.</p>")

h.append("<h2>Caution</h2><p>This report summarizes Sturgeon methylation-class scores. It is not a standalone final clinical diagnosis. Integrate the result with histopathology, tumour fraction/purity, CNV profile, and orthogonal molecular tests.</p>")
h.append("</body></html>")
report_html.write_text("\n".join(h) + "\n")

# PDF report generated using only the Python standard library.
# This avoids empty PDFs caused by broken external HTML/PDF converters.
pdf_lines = []
pdf_lines.append(("Sturgeon consolidated methylation report", 18))
pdf_lines.append(("", 10))
pdf_lines.append((f"Generated: {now}", 10))
pdf_lines.append((f"Input: {input_arg}", 10))
pdf_lines.append((f"Files processed: {len(csv_files)}", 10))
pdf_lines.append((f"Thresholds: high confidence score >= {high_thr}; likely/confident score >= {likely_thr} and < {high_thr}; inconclusive < {likely_thr}.", 10))
pdf_lines.append(("", 10))
pdf_lines.append(("Summary", 14))
pdf_lines.append(("", 10))

if summary_rows:
    for r in summary_rows:
        pdf_lines.extend([
            (f"Sample: {r['sample']}", 13),
            (f"Measured probes: {r['number_probes']}", 10),
            (f"Top methylation label: {r['top1_full_label']}", 10),
            (f"Family / class / subclass: {r['top1_family']} / {r['top1_class_code']} / {r['top1_subclass_code']}", 10),
            (f"Class full name: {display_full_name(r['top1_class_code'])}", 10),
            (f"Subclass full name: {display_full_name(r['top1_subclass_code'])}", 10),
            (f"Top score: {r['top1_score']}", 10),
            (f"Top2: {r['top2_full_label']} with score {r['top2_score']}", 10),
            (f"Top1-top2 margin: {r['top1_top2_margin']}", 10),
            (f"Confidence category: {r['confidence_category']}", 10),
            ("", 10),
            ("Interpretation", 12),
            (r["interpretation"], 10),
            ("", 10),
        ])
else:
    pdf_lines.append(("No valid results were found.", 10))

pdf_lines.append(("Caution", 12))
pdf_lines.append(("This report summarizes Sturgeon methylation-class scores. It is not a standalone final clinical diagnosis. Integrate the result with histopathology, tumour fraction/purity, CNV profile, and orthogonal molecular tests.", 10))

make_text_pdf(report_pdf, pdf_lines)

if not pdf_has_text(report_pdf):
    print(f"ERROR: PDF was created but failed text/size validation: {report_pdf}", file=sys.stderr)
    sys.exit(3)

print("Done. Consolidated Sturgeon report written to:")
print(f"  {outdir}")
print("Main outputs:")
print(f"  HTML report : {report_html}")
print(f"  PDF report  : {report_pdf}")
print(f"  Markdown    : {report_md}")
print(f"  Summary CSV : {summary_csv}")
print(f"  Top CSV     : {top_csv}")
print(f"  Codebook    : {codebook_csv}")
if errors:
    print(f"Warnings/errors: {errors_txt}")
PY
