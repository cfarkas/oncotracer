from pathlib import Path
import json
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import Mock

from oncotracer_cli.cna_quality import (POLICY, publish_assessment, load_assessment,
    eligible_classifier_samples, invalidate_derived_cna, summary_fields, assessment_html)
from oncotracer_cli.runtime import OncoTracerError
from oncotracer_cli.completion import summarize_completion

ROOT=Path(__file__).resolve().parents[1]

class CnaQualityTests(unittest.TestCase):
    def fixture(self, root):
        path=root/"01_samurai_ont/qdnaseq/qdnaseq_sample_status.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({"samples":[
            {"sample":"control", "qc_policy":POLICY,"status":"failed","assessment":"not_assessable",
             "error":"low_coverage_at_selected_bin_size","median_reads_per_bin":1,"zero_read_bin_fraction":.3},
            {"sample":"neutral","qc_policy":POLICY,"status":"complete","assessment":"assessable",
             "error":None,"median_reads_per_bin":40,"zero_read_bin_fraction":0}]}))
        return path

    def test_sparse_control_is_unavailable_not_flat_and_completion_stays_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=self.fixture(root)
            data=publish_assessment(root,path,50)
            self.assertEqual(eligible_classifier_samples(root),"neutral")
            self.assertEqual(eligible_classifier_samples(root,"control"),"")
            self.assertIn("NOT ASSESSABLE",assessment_html(data))
            self.assertIn("control",(root/"06_workflow_summary/cna_assessment.tsv").read_text())
            summary={"input_samples":["control","neutral"],"requested_branches":["cna","reports"],
                     "cna_status":"partial_failure","completed_samples":["neutral"],"failed_samples":["control"],
                     "cna_classifier_status":"complete","cna_reports_completed":True,**summary_fields(data)}
            result=summarize_completion(summary)
            self.assertEqual(result["completed_count"],1)
            self.assertEqual(result["branches"]["cna"]["sample_statuses"]["control"],"not_assessable")
            self.assertEqual(result["branches"]["reports"]["sample_statuses"]["control"],"not_assessable")

    def test_old_results_require_coverage_recheck(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);path=self.fixture(root)
            data=json.loads(path.read_text());data["samples"][0].pop("qc_policy");data["samples"][1].pop("qc_policy")
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(OncoTracerError,"Resume"):
                eligible_classifier_samples(root)
            new=publish_assessment(root,path,50)
            self.assertTrue(all(r["assessment"]=="not_assessable" for r in new["samples"]))

    def test_evidence_change_archives_old_plots_preserving_bams_and_variants(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);status=self.fixture(root);seg=status.parent/"all_segments.seg";seg.write_text("old")
            for name in ("02_bam_refinement","03_cna_codification","04_cna_custom_plots","05_cna_classifier","08_variants"):
                p=root/name;p.mkdir();(p/"result.txt").write_text(name)
            bam=root/"01_samurai_ont/sample.bam";bam.write_bytes(b"unaltered")
            invalidate_derived_cna(root,status,seg)
            self.assertFalse((root/"05_cna_classifier").exists())
            self.assertEqual(bam.read_bytes(),b"unaltered")
            self.assertTrue((root/"08_variants/result.txt").exists())
            (root/"05_cna_classifier").mkdir();(root/"05_cna_classifier/new.txt").write_text("valid")
            invalidate_derived_cna(root,status,seg)
            self.assertTrue((root/"05_cna_classifier/new.txt").exists())
            refreshed=json.loads(status.read_text());refreshed["updated_at"]="later bookkeeping refresh"
            status.write_text(json.dumps(refreshed))
            invalidate_derived_cna(root,status,seg)
            self.assertTrue((root/"05_cna_classifier/new.txt").exists())
            seg.write_text("changed")
            invalidate_derived_cna(root,status,seg)
            self.assertFalse((root/"05_cna_classifier").exists())
            self.assertEqual(len(list((root/".oncotracer-native/superseded-cna").iterdir())),2)

    def test_html_escapes_sample_labels(self):
        self.assertNotIn("<script>",assessment_html({"samples":[{"sample":"<script>","assessment":"not_assessable"}]}))

    def test_ichor_coverage_checks_count_bins_excluding_unmappable_regions(self):
        from oncotracer_cli.cna_quality import assess_ichor_counts
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def wig(name,values):
                p=root/name;p.write_text("fixedStep chrom=chr1 start=1 step=500000 span=500000\n"+"\n".join(map(str,values))+"\n");return p
            gc=wig("gc.wig",[.5]*300);mapping=wig("map.wig",[.95]*200+[0]*100)
            good=wig("good.wig",[40]*200+[0]*100)
            low=wig("low.wig",[1]*200+[0]*100)
            self.assertEqual(assess_ichor_counts(good,gc,mapping)["assessment"],"assessable")
            self.assertEqual(assess_ichor_counts(low,gc,mapping)["assessment"],"not_assessable")

    def test_all_unassessable_skips_refinement_and_reports_explicitly(self):
        from oncotracer_cli.engine import run_refinement_and_outputs
        from oncotracer_cli.runtime import StageLedger
        from oncotracer_cli.classifier import run_native_classifier
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);status=self.fixture(root)
            data=json.loads(status.read_text());data["samples"]=data["samples"][:1]
            status.write_text(json.dumps(data));(status.parent/"all_segments.seg").write_text("ID\tchrom\tstart\tend\tnum.mark\tseg.mean\n")
            runner=Mock();tools=Mock()
            run_refinement_and_outputs(ROOT,{"ont_binsize_kb":50},"ont",root/"01_samurai_ont",status.parent,
                root/"bams",root,root/"reference",runner,tools,force=False,caller="qdnaseq")
            runner.run.assert_not_called()
            summary=json.loads((root/"06_workflow_summary/workflow_summary.json").read_text())
            self.assertEqual(summary["cna_status"],"not_assessable")
            run_native_classifier(ROOT,{"run_pdf_reports":False,"run_clinician_reports":False},root,root/"reference",
                runner,StageLedger(root/"state.json"),tools,force=False)
            runner.run.assert_not_called()
            self.assertIn("NOT ASSESSABLE",(root/"05_cna_classifier/final_report.html").read_text())

    def test_unassessable_notice_has_no_fabricated_event_counts_in_pdf(self):
        import sys
        from pypdf import PdfReader
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);status=self.fixture(root);publish_assessment(root,status,50)
            result=subprocess.run([sys.executable,str(ROOT/"bin/cna_classifier_nf/bin/cna_assessment_reports.py"),
                 "--assessment",str(root/"06_workflow_summary/cna_assessment.json"),"--outdir",str(root/"reports")],text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            text=" ".join(page.extract_text() for page in PdfReader(root/"reports/cna_assessment.pdf").pages)
            self.assertIn("NOT ASSESSABLE",text)
            self.assertIn("control",text)
            self.assertNotIn("CNA-ultracomplex",text)

    @unittest.skipUnless(shutil.which("Rscript"), "base R unavailable")
    def test_real_r_coverage_gate(self):
        result=subprocess.run([shutil.which("Rscript"),"--vanilla","tests/test_cna_coverage.R"],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn("CNA_COVERAGE_GATE_OK",result.stdout)
