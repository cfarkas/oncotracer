"""Explicit HTML/PDF notices for samples with no assessable CNA result."""
import html
import json
import re
from pathlib import Path

def unassessable_reports(assessment_path, outdir, suffix):
    if not assessment_path:
        return []
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    rows=[]
    styles=getSampleStyleSheet()
    outdir=Path(outdir);outdir.mkdir(parents=True,exist_ok=True)
    for row in json.loads(Path(assessment_path).read_text()).get("samples",[]):
        if row["assessment"] == "assessable":
            continue
        name=row["sample"]
        slug=re.sub(r"[^A-Za-z0-9_.-]", "_", name)
        pdf=outdir/(slug+suffix+".pdf");page=pdf.with_suffix(".html")
        lines=["Sample: "+name, "CNA NOT ASSESSABLE", row.get("reason", "Insufficient reliable CNA evidence"),
               "Bin size (kb): "+str(row.get("bin_size_kb")), "Median reads per usable bin: "+str(row.get("median_reads_per_bin")),
               "Empty usable bin fraction: "+str(row.get("zero_read_bin_fraction")),
               "This is not a normal/flat profile or a deep-loss call. No CNA burden, driver, pathology-concordance or subtype inference is made.",
               "Consider coarser bins and recheck coverage, or additional sequencing. Other analyses retain their own assessment status."]
        SimpleDocTemplate(str(pdf)).build([item for line in lines for item in (Paragraph(html.escape(line), styles["BodyText"]), Spacer(1,12))])
        page.write_text('<!doctype html><meta charset="utf-8"><title>CNA not assessable</title>'+''.join('<p>'+html.escape(line)+'</p>' for line in lines))
        rows.append({"sample":name,"html":page.name,"pdf":pdf.name,"probable_cna_classification":"NOT ASSESSABLE",
                     "knowledge_refined_class":"NOT ASSESSABLE","rule_based_cna_class":"NOT ASSESSABLE",
                     "n_cna_events":"not assessable","n_drivers":"not assessable"})
    return rows

if __name__ == "__main__":
    import argparse
    from pypdf import PdfReader, PdfWriter
    p=argparse.ArgumentParser();p.add_argument("--assessment",required=True);p.add_argument("--outdir",required=True);a=p.parse_args()
    root=Path(a.outdir)
    rows=unassessable_reports(a.assessment,root/"samples","_CNA_not_assessable")
    writer=PdfWriter()
    for row in rows:
        for page in PdfReader(root/"samples"/row["pdf"]).pages:writer.add_page(page)
    if writer.pages:
        with (root/"cna_assessment.pdf").open("wb") as h:writer.write(h)
