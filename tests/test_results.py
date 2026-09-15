"""Organized native publication and nondestructive legacy result browsing."""
import contextlib
import fcntl
import io
import json
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from oncotracer_cli.cli import build_parser, main
from oncotracer_cli.results import MARKER, organize_plot_exports, publish_refinement, write_results_index
from oncotracer_cli.runtime import OncoTracerError


class ResultsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "results"
        (self.root / ".oncotracer-native").mkdir(parents=True)
        self.put("06_workflow_summary/workflow_summary.json", json.dumps({
            "workflow_status": "complete", "completed_samples": ["synthetic"], "failed_samples": []}))
        self.put("06_workflow_summary/native_run_manifest.json", '{"files":[]}\n')

    def put(self, relative, contents="synthetic\n"):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        return path

    def assert_links(self):
        class Links(HTMLParser):
            def __init__(self):
                super().__init__()
                self.links=[]
            def handle_starttag(self, tag, attrs):
                if tag == "a":
                    self.links.extend(value for key,value in attrs if key == "href")
        count=0
        for page in self.root.rglob("*.html"):
            parser=Links()
            parser.feed(page.read_text())
            for href in parser.links:
                url=urlsplit(href)
                if not url.scheme and url.path:
                    target=page.parent / unquote(url.path)
                    self.assertTrue(target.is_file(), (page,href))
                    self.assertTrue(target.resolve().is_relative_to(self.root.resolve()))
                    count+=1
        self.assertGreater(count, 5)

    def test_publish_one_final_set_keeps_authoritative_legacy_paths(self):
        stage=self.root / "02_bam_refinement"
        dataset="illumina_qdnaseq_100kb"
        raw=stage / "diagnostics" / dataset
        for relative in ("04_final_results/final_segments.tsv", "04_final_results/final_segments.bed",
                         "01_tables/refined_bins.tsv.gz", "01_tables/sample_refinement_summary.csv",
                         "01_tables/final_segments.tsv", "03_consolidated/final_segments.tsv"):
            self.put(str((raw / relative).relative_to(self.root)))
        result=publish_refinement(stage,dataset)
        self.assertEqual(result, stage / dataset)
        self.assertEqual((result / "04_final_results/final_segments.tsv").read_bytes(),
                         (raw / "04_final_results/final_segments.tsv").read_bytes())
        self.assertTrue((result / "01_tables/refined_bins.tsv.gz").is_file())
        self.assertFalse((result / "01_tables/final_segments.tsv").exists())
        self.assertFalse((result / "03_consolidated").exists())
        self.assertTrue((raw / "03_consolidated/final_segments.tsv").is_file())
        publish_refinement(stage,dataset)
        self.assertEqual(len(json.loads((result / "published_results.json").read_text())["files"]),4)

    def test_organize_secondary_formats_preserves_primary_and_report_paths(self):
        names=("cna_per_sample_pages.pdf", "cna_log2_ratio_profiles_all_samples.pdf",
               "cna_genome_overview.pdf", "cna_genome_overview.png", "cna_genome_overview.svg",
               "plot_table_burden_mb.tsv", "llm_reports/index.html", "per_sample_log2_cna/synthetic.pdf")
        before={name:self.put("04_cna_custom_plots/"+name).read_bytes() for name in names}
        stage=self.root / "04_cna_custom_plots"
        organize_plot_exports(stage)
        for name in names:
            relative=("exports/"+name.rsplit(".",1)[1]+"/"+name if name.startswith("cna_genome") else
                      "tables/"+name if name.startswith("plot_table") else name)
            self.assertEqual((stage / relative).read_bytes(),before[name])
        self.assertFalse((stage / "cna_genome_overview.png").exists())
        self.assertFalse((stage / "plot_table_burden_mb.tsv").exists())
        organize_plot_exports(stage)

    def test_legacy_refresh_preserves_science_and_links_all_stages(self):
        self.put("01_samurai_ont/results/ichorcna/synthetic/synthetic/model-fit.pdf")
        self.put("01_samurai_ont/results/ichorcna/ichorcna_sample_status.json","{}")
        self.put("02_bam_refinement/ONT_ichorcna_500kb/04_final_results/final_segments.tsv")
        self.put("02_bam_refinement/ONT_ichorcna_500kb/01_tables/final_segments.tsv")
        self.put("02_bam_refinement/ONT_ichorcna_500kb/01_tables/refined_bins.tsv.gz")
        self.put("03_cna_codification/cna_events.tsv")
        self.put("04_cna_custom_plots/cna_per_sample_pages.pdf")
        self.put("04_cna_custom_plots/cna_genome_overview.svg")
        self.put("05_cna_classifier/06_knowledge/knowledge_metrics.json","{}")
        self.put("05_cna_classifier/03_report/cna_classifier_report.html","<html>Existing report</html>")
        self.put("07_methylation/methylation_status.json","{}")
        self.put("07_methylation/synthetic/marlin_prediction.tsv")
        before={p:p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        result=write_results_index(self.root)
        self.assertEqual(result,self.root / "index.html")
        self.assertTrue(result.read_text().startswith(MARKER))
        self.assert_links()
        self.assertEqual(before,{p:p.read_bytes() for p in before})
        catalog=json.loads((self.root / "06_workflow_summary/results_catalog.json").read_text())
        self.assertEqual(len(catalog["stages"]),7)
        self.assertTrue(next(r for r in catalog["stages"] if r["stage"]=="02_bam_refinement")["diagnostic_file_count"])
        self.assertIn("native_run_manifest.json",(self.root / "06_workflow_summary/index.html").read_text())
        self.assertTrue((self.root / "05_cna_classifier/06_knowledge/index.html").is_file())
        write_results_index(self.root)
        self.assertEqual(before,{p:p.read_bytes() for p in before})

    def test_empty_disabled_gistic_placeholders_stay_on_disk_but_are_not_listed(self):
        placeholders = [
            self.put("05_cna_classifier/04_gistic/gistic2_command.txt", ""),
            self.put("05_cna_classifier/03_report/report_tables/gistic2_command.txt", ""),
            self.put("05_cna_classifier/04_gistic/gistic2_versions.txt", ""),
        ]
        status = self.put("05_cna_classifier/04_gistic/gistic2_status.tsv",
                          "status\treason\nskipped\t--run_gistic false\n")
        write_results_index(self.root)
        catalog = json.loads((self.root / "06_workflow_summary/results_catalog.json").read_text())
        stage = next(row for row in catalog["stages"] if row["stage"] == "05_cna_classifier")
        listed = stage["primary_files"] + stage["supporting_files"]
        self.assertIn(str(status.relative_to(self.root)), listed)
        for path in placeholders:
            self.assertTrue(path.is_file())
            self.assertEqual(path.stat().st_size, 0)
            self.assertNotIn(str(path.relative_to(self.root)), listed)
            self.assertNotIn(path.name, (self.root / "05_cna_classifier/index.html").read_text())
        self.assertIn("gistic2_status.tsv", (self.root / "05_cna_classifier/index.html").read_text())
        self.assert_links()

    def test_methylation_only_and_partial_status_are_explicit(self):
        self.put("06_workflow_summary/workflow_summary.json",json.dumps({"workflow_status":"partial_failure",
            "cna_status":"not_requested","methylation_status":"partial_failure",
            "methylation_completed_samples":["synthetic"],"methylation_failed_samples":["other"]}))
        write_results_index(self.root)
        page=(self.root / "index.html").read_text()
        self.assertIn("CNA: not requested",page)
        self.assertIn("Methylation status: partial_failure",page)
        self.assertNotIn("Status: complete",page)

    def test_unrelated_index_or_catalog_fails_before_any_writes(self):
        for collision,contents in (("index.html","user page"),
                                   ("06_workflow_summary/results_catalog.json",'{"schema":"user"}')):
            with self.subTest(collision=collision):
                path=self.put(collision,contents)
                before={p:p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
                with self.assertRaises(OncoTracerError):
                    write_results_index(self.root)
                self.assertEqual(before,{p:p.read_bytes() for p in self.root.rglob("*") if p.is_file()})
                path.rename(path.with_name(path.name + ".held"))

    def test_external_symlink_files_are_not_exposed(self):
        secret=Path(self.temp.name) / "outside.txt"
        secret.write_text("do not expose")
        stage=self.root / "03_cna_codification"
        stage.mkdir()
        (stage / "cna_events.tsv").symlink_to(secret)
        write_results_index(self.root)
        self.assertNotIn("cna_events.tsv",(stage / "index.html").read_text())
        self.assertEqual(secret.read_text(),"do not expose")

    def test_public_refresh_command_and_active_run_lock(self):
        args=build_parser().parse_args(["results","--outdir",str(self.root)])
        self.assertEqual(args.func.__name__,"command_results")
        lock_path=self.root / ".oncotracer-native/run.lock"
        with lock_path.open("a+") as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["results","--outdir",str(self.root)]),2)
            self.assertFalse((self.root / "index.html").exists())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["results","--outdir",str(self.root)]),0)
        self.assertTrue((self.root / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()
