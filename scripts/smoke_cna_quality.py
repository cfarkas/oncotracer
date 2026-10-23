"""Offline end-to-end CNA reporting smoke: synthetic inputs, no alignments."""
from pathlib import Path
import argparse, csv, json, sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from oncotracer_cli.classifier import run_native_classifier
from oncotracer_cli.engine import Toolchain
from oncotracer_cli.runtime import CommandRunner, StageLedger, atomic_write_workflow_summary
from oncotracer_cli.cna_quality import POLICY, publish_assessment, summary_fields
p=argparse.ArgumentParser();p.add_argument("--work-dir",required=True);p.add_argument("--classifier-prefix",required=True);a=p.parse_args()
root=Path(a.work_dir);root.mkdir(parents=True,exist_ok=False)
status=root/"01_samurai_ont/qdnaseq/qdnaseq_sample_status.json";status.parent.mkdir(parents=True)
rows=[]
for name,good in [("sparse_control",False),("neutral",True),("focal_loss",True)]:
 rows.append({"sample":name,"qc_policy":POLICY,"assessment":"assessable" if good else "not_assessable", "status":"complete" if good else "failed",
              "median_reads_per_bin":40 if good else 1,"zero_read_bin_fraction":0 if good else .3,"error":None if good else "low_coverage_at_selected_bin_size"})
status.write_text(json.dumps({"samples":rows}));assessment=publish_assessment(root,status,50)
d=root/"03_cna_codification";d.mkdir()
(d/"cna_events.tsv").write_text("sample\tstate\tchrom\tstart\tend\tn_bins\tmean_log2\tmedian_log2\tcytoband\n"
 "sparse_control\tdeep_loss\t1\t1\t200000000\t4000\t-300\t-300\t1p\n"
 "focal_loss\tloss\t9\t20000000\t24000000\t80\t-1\t-1\t9p21\n")
(d/"cna_cytogenomic_notation.tsv").write_text("sample\tn_cna_events\tmolecular_cytogenomic_notation\nneutral\t0\tNo high-confidence CNA\nfocal_loss\t1\tloss\nsparse_control\t1\tINVALID OLD LOSS\n")
summary={"mode":"ont","workflow_status":"partial_failure","cna_status":"partial_failure","completed_samples":["neutral","focal_loss"],
         "failed_samples":["sparse_control"],"input_samples":[r["sample"] for r in rows],"requested_branches":["cna","reports"],**summary_fields(assessment)}
atomic_write_workflow_summary(root/"06_workflow_summary",summary)
config={"run_gistic":False,"knowledge_web":False,"knowledge_hf_ner":False,"knowledge_literature_llm":False,"knowledge_catalog_llm":False,
        "knowledge_deep_literature":False,"knowledge_deep_enable_llm_ranker":False,"pathology_use_biomed_models":False,"knowledge_literature_reference_llm_selection":False}
tools=Toolchain(classifier_prefix=Path(a.classifier_prefix),runtime_cache=root/"cache")
runner=CommandRunner(root/"trace.tsv");ledger=StageLedger(root/"state.json")
run_native_classifier(ROOT,config,root,root/"reference",runner,ledger,tools,force=False)
cls=root/"05_cna_classifier"
with (cls/"tables/classification/cna_patient_classification.tsv").open() as h:classified=list(csv.DictReader(h,delimiter="\t"))
assert {r["sample"] for r in classified} == {"neutral","focal_loss"}
assert 'sparse_control' not in (cls/"diagnostics/prepared/clean_events.tsv").read_text()
assert "NOT ASSESSABLE" in (cls/"final_report.html").read_text()
from pypdf import PdfReader
assert "NOT ASSESSABLE" in " ".join(p.extract_text() for p in PdfReader(cls/"final_report.pdf").pages)
# A repeat must respect the layout manifest and preserve the explicit notices.
run_native_classifier(ROOT,config,root,root/"reference",runner,ledger,tools,force=False)
assert "NOT ASSESSABLE" in (cls/"clinician_report.html").read_text()
print(json.dumps({"passed":True,"synthetic_only":True,"sparse_control_excluded":True,"neutral_and_focal_loss_retained":True,"html_pdf_notice":True,"resume_passed":True}))
