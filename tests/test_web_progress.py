"""Variant stage labels and honest timing in the browser."""
import unittest
from oncotracer_cli.web_progress import progress_for_job

class VariantProgressTests(unittest.TestCase):
    def test_partial_results_have_a_distinct_label_and_final_elapsed(self):
        job = {"status": "partial_failure", "_started_at": 100.0, "_finished_at": 125.0}
        result = progress_for_job(job, "", now=200.0)
        self.assertEqual(result["stage"], "Partial failure")
        self.assertIn("retained", result["note"])
        self.assertEqual(result["elapsed_seconds"], 25)
        self.assertIsNone(result["eta_seconds"])
        self.assertIsNone(result["overall_eta_seconds"])
        job["status"] = "failed"
        self.assertEqual(progress_for_job(job, "", now=201.0)["stage"], "Failed")

    def test_variant_stages_replace_download_eta_without_guessing_runtime(self):
        stages = {
            "variant-SAMPLE_ILLUMINA-bcftools-ffperase-coverage": "Measuring coverage for FFPERASE",
            "variant-SAMPLE_ILLUMINA-bcftools-ffperase-picard": "Assessing FFPE artifacts",
            "variant-SAMPLE_ILLUMINA-bcftools-varlociraptor-estimate": "Measuring alignment properties for Varlociraptor",
            "variant-SAMPLE_ILLUMINA-bcftools-varlociraptor-preprocess": "Preparing Varlociraptor evidence",
            "variant-SAMPLE_ILLUMINA-bcftools-varlociraptor-call": "Calculating variant probabilities",
            "variant-SAMPLE_ILLUMINA-bcftools-varlociraptor-fdr": "Filtering variants by local FDR",
            "variant-SAMPLE_ILLUMINA-bcftools-annovar": "Annotating variants",
            "variant-SAMPLE_ILLUMINA-bcftools-pileup": "Calling small variants",
            "variant-SAMPLE_ILLUMINA-mutect2-call": "Calling small variants",
            "variant-SAMPLE_ONT-clairs_to-call": "Calling small variants",
            "variant-SAMPLE_ONT-clair3-call": "Calling small variants",
            "variant-SAMPLE_ILLUMINA-freebayes-call": "Calling small variants",
            "variant-SAMPLE_ILLUMINA-mutect2-filter": "Filtering small variants",
            "variant-align_sample-bcftools-call": "Calling small variants",
        }
        download = "  hg38-00-0000.part: 40% | 10.0 MiB/s | ETA 60s\n"
        for command, expected in stages.items():
            with self.subTest(command=command):
                job = {"status": "running", "_started_at": 100.0}
                self.assertEqual(progress_for_job(job, download, now=120.0)["eta_seconds"], 60)
                result = progress_for_job(job, download + f"[{command}] tool --args\n", now=130.0)
                self.assertEqual(result["stage"], expected)
                self.assertEqual(result["elapsed_seconds"], 30)
                self.assertIsNone(result["eta_seconds"])
                self.assertIsNone(result["overall_eta_seconds"])
                self.assertIsNone(result["percent"])

    def test_stopping_takes_precedence_over_variant_stage(self):
        job = {"status": "stopping", "_started_at": 10.0}
        result = progress_for_job(job, "[variant-S-clair3-varlociraptor-call] tool\n", now=35.0)
        self.assertEqual(result["stage"], "Stopping analysis")
        self.assertEqual(result["elapsed_seconds"], 25)
        self.assertIsNone(result["eta_seconds"])

if __name__ == "__main__":
    unittest.main()
