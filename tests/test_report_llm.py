"""Offline report-generation contracts; optional real tiny-model CPU smoke test."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "bin/cna_classifier_nf/bin"
sys.path.insert(0, str(SCRIPTS))
import llm_runtime as runtime


def load_script(name, filename):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


knowledge = load_script("report_knowledge_tests", "05_scrape_cna_knowledge.py")
pdf = load_script("report_pdf_tests", "06_pdf_knowledge_reports.py")
sys.path.remove(str(SCRIPTS))

EVIDENCE = [
    {
        "id": "S1",
        "title": "MYC copy number in lymphoma",
        "pmid": "12345678",
        "doi": "",
        "abstract": "MYC copy gain is associated with altered proliferation in lymphoma models.",
    }
]
CLAIM = "MYC copy gain is associated with altered proliferation in lymphoma models."


def response(text=CLAIM, sources=None):
    return json.dumps(
        {"claims": [{"text": text, "sources": ["S1"] if sources is None else sources}]}
    )


class ReportLinkTests(unittest.TestCase):
    def test_standalone_defaults_and_relocated_index_links(self):
        rows = [{"sample": "synthetic", "html": "synthetic.html", "pdf": "synthetic.pdf"}]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            pdf.write_index(output, rows)
            default = (output / "index.html").read_text()
            self.assertIn("href='../cna_classifier_report.html'", default)
            self.assertIn("href='../clinician_reports/index.html'", default)
            pdf.write_index(output, rows,
                            cohort_report_href="../../05_cna_classifier/03_report/cna_classifier_report.html",
                            clinician_reports_href="",
                            knowledge_evidence_href="../../05_cna_classifier/06_knowledge/")
            moved = (output / "index.html").read_text()
            self.assertIn("href='../../05_cna_classifier/03_report/cna_classifier_report.html'", moved)
            self.assertIn("href='../../05_cna_classifier/06_knowledge/knowledge_metrics.json'", moved)
            self.assertNotIn("Clinician driver summaries", moved)
            self.assertIn("href='synthetic.html'", moved)
            self.assertTrue((output / "pdf_report_index.tsv").is_file())
            self.assertTrue((output / "pdf_html_report_index.tsv").is_file())


class CitationTests(unittest.TestCase):
    def test_valid_claim_gets_known_pmid_and_review_caveat(self):
        result = runtime.validate_synthesis(response(), EVIDENCE)
        self.assertIn("[PMID 12345678]", result)
        self.assertIn("does not establish a diagnosis", result)
        self.assertNotIn("[S1]", result)

    def test_invalid_generations_are_rejected(self):
        invalid = [
            "A long fluent answer without any source evidence or citations.",
            response(sources=[]),
            response(sources=["S2"]),
            response(sources=["S1", "S1"]),
            response(
                "The patient has a confirmed lymphoma diagnosis from these findings."
            ),
            response("We recommend treatment for this copy number alteration now."),
            response(CLAIM + " PMID 99999999"),
            response(CLAIM + " https://example.org"),
            response(CLAIM + " <b>safe</b>"),
            json.dumps({"claims": []}),
            json.dumps({"claims": [{"text": CLAIM, "sources": [True]}]}),
            json.dumps(
                {
                    "claims": [{"text": CLAIM, "sources": ["S1"]}],
                    "diagnosis": "invented",
                }
            ),
        ]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                runtime.validate_synthesis(text, EVIDENCE)

    def test_selection_requires_exact_visible_ids(self):
        self.assertEqual(
            runtime.parse_reference_selection("[3, 1]", {"1", "3"}, 2), [2, 0]
        )
        for text in (
            "Paper 1 is relevant",
            "2024",
            "1,1",
            "2",
            "1,3,5",
            "01",
            "1.0",
            "1\n3",
        ):
            with self.subTest(text=text), self.assertRaises(ValueError):
                runtime.parse_reference_selection(text, {"1", "3"}, 2)

    def test_seed_titles_are_not_evidence(self):
        self.assertFalse(
            runtime.usable_evidence(
                {"title": "PMID seed from built-in CNA knowledge dictionary"}
            )
        )
        self.assertFalse(
            runtime.usable_evidence({"title": "Real title"}, abstract_required=True)
        )
        self.assertTrue(runtime.usable_evidence(EVIDENCE[0], abstract_required=True))


class RuntimeTests(unittest.TestCase):
    def fake_modules(self, encoder_decoder=True):
        config = types.SimpleNamespace(
            is_encoder_decoder=encoder_decoder, _commit_hash="resolved-sha"
        )
        model = MagicMock(config=config)
        transformer = types.SimpleNamespace(
            __version__="test",
            AutoConfig=MagicMock(),
            AutoTokenizer=MagicMock(),
            AutoModelForSeq2SeqLM=MagicMock(),
            AutoModelForCausalLM=MagicMock(),
        )
        transformer.AutoConfig.from_pretrained.return_value = config
        transformer.AutoModelForSeq2SeqLM.from_pretrained.return_value = model
        transformer.AutoModelForCausalLM.from_pretrained.return_value = model
        torch = types.SimpleNamespace(
            __version__="test", set_num_threads=MagicMock(), float32="float32"
        )
        return transformer, torch, model

    def test_all_loaders_are_local_only_and_cpu_with_revision(self):
        for encoder_decoder in (True, False):
            with self.subTest(encoder_decoder=encoder_decoder):
                transformer, torch, model = self.fake_modules(encoder_decoder)
                loader = runtime.LocalReportLLM(threads=2)
                with patch.dict(
                    sys.modules, {"transformers": transformer, "torch": torch}
                ):
                    bundle = loader._load("org/model@fixed-sha", True)
                    self.assertIs(loader._load("org/model@fixed-sha", True), bundle)
                factories = [
                    transformer.AutoConfig,
                    transformer.AutoTokenizer,
                    (
                        transformer.AutoModelForSeq2SeqLM
                        if encoder_decoder
                        else transformer.AutoModelForCausalLM
                    ),
                ]
                for factory in factories:
                    factory.from_pretrained.assert_called_once()
                    args, kwargs = factory.from_pretrained.call_args
                    self.assertEqual(args, ("org/model",))
                    self.assertIs(kwargs["local_files_only"], True)
                    self.assertIs(kwargs["trust_remote_code"], False)
                    self.assertEqual(
                        kwargs["revision"],
                        (
                            "fixed-sha"
                            if factory is transformer.AutoConfig
                            else "resolved-sha"
                        ),
                    )
                self.assertTrue(
                    factories[-1].from_pretrained.call_args.kwargs["use_safetensors"]
                )
                model.to.assert_called_once_with(device="cpu", dtype="float32")
                model.eval.assert_called_once()
                torch.set_num_threads.assert_called_once_with(2)
                self.assertEqual(bundle[-1]["model_revision"], "resolved-sha")

    def test_failed_load_is_not_retried_per_feature(self):
        transformer, torch, _ = self.fake_modules()
        transformer.AutoConfig.from_pretrained.side_effect = OSError("not cached")
        loader = runtime.LocalReportLLM()
        with patch.dict(sys.modules, {"transformers": transformer, "torch": torch}):
            for _ in range(3):
                with self.assertRaises(RuntimeError):
                    loader._load("missing", True)
        transformer.AutoConfig.from_pretrained.assert_called_once()

    def test_context_budget_preserves_whole_evidence_and_instructions(self):
        class Tokenizer:
            model_max_length = 550
            chat_template = None
            pad_token_id = 0
            eos_token_id = 1

            def encode(self, text, **kwargs):
                return list(text)

            def __call__(self, text, **kwargs):
                self.prompt = text
                self.options = kwargs
                return {"input_ids": types.SimpleNamespace(shape=(1, len(text)))}

            def decode(self, tokens, **kwargs):
                return "1"

        tokenizer = Tokenizer()
        model = MagicMock(config=types.SimpleNamespace(is_encoder_decoder=True))
        model.generate.return_value = [[1, 2]]
        torch = types.SimpleNamespace(inference_mode=contextlib.nullcontext)
        engine = runtime.LocalReportLLM()
        records = [
            {**EVIDENCE[0], "id": str(i), "abstract": "word " * 200}
            for i in range(1, 10)
        ]
        with patch.object(engine, "_load", return_value=(model, tokenizer, torch, {})):
            text, visible, audit = engine.generate(
                "mock",
                local_files_only=True,
                instructions="Keep these instructions intact.",
                evidence=records,
                max_input_chars=550,
                max_new_tokens=20,
            )
        self.assertTrue(tokenizer.prompt.startswith("Keep these instructions intact."))
        self.assertLessEqual(len(tokenizer.prompt), 550)
        self.assertGreater(len(visible), 0)
        self.assertLess(len(visible), len(records))
        self.assertEqual(
            json.loads(tokenizer.prompt.split("instructions):\n")[1]), visible
        )
        self.assertFalse(tokenizer.options["truncation"])
        self.assertFalse(model.generate.call_args.kwargs["do_sample"])
        self.assertEqual(audit["source_ids"], ";".join(r["id"] for r in visible))
        self.assertEqual(len(audit["prompt_sha256"]), 64)

    def test_causal_context_reserves_generation_space(self):
        model = types.SimpleNamespace(
            config=types.SimpleNamespace(
                is_encoder_decoder=False, max_position_embeddings=512
            )
        )
        tokenizer = types.SimpleNamespace(model_max_length=10**30)
        self.assertEqual(runtime.LocalReportLLM._input_limit(model, tokenizer, 96), 416)


class KnowledgeIntegrationTests(unittest.TestCase):
    def test_offline_catalog_does_not_load_any_llm(self):
        fid = "2p16_REL_BCL11A_gain_amp"
        with patch.object(
            knowledge.REPORT_LLM,
            "generate",
            side_effect=AssertionError("must not load"),
        ) as generate:
            kb, refs, trials, metrics = knowledge.build_feature_kb(
                pd.DataFrame(),
                pd.DataFrame([{"feature_id": fid}]),
                False,
                None,
                3,
                "lymphoma",
                False,
                "",
                cancer_type="lymphoma",
                enable_literature_llm=True,
                literature_llm_models="model",
            )
        generate.assert_not_called()
        self.assertFalse(refs.empty)
        self.assertEqual(kb.iloc[0]["literature_synthesis_source"], "built_in_catalog")
        self.assertEqual(metrics["literature_llm_attempted_features"], 0)
        self.assertIn("prompt_sha256", trials.columns)
        self.assertNotIn("fallback evidence", kb.iloc[0]["literature_synthesis"])

    def test_synthesis_rejects_bad_model_then_uses_fallback_model(self):
        synth = knowledge.LiteratureLLMSynthesizer("bad,good", local_files_only=True)
        outputs = [
            (response(sources=["S9"]), EVIDENCE, {"response_text": "bad"}),
            (response(), EVIDENCE, {"prompt_sha256": "sha"}),
        ]
        with patch.object(
            knowledge.REPORT_LLM, "generate", side_effect=outputs
        ) as generate:
            text, model, trials = synth.synthesize(
                "MYC", "MYC gain", "MYC", "lymphoma", EVIDENCE
            )
        self.assertEqual(model, "good")
        self.assertIn("PMID 12345678", text)
        self.assertEqual([t["status"] for t in trials], ["failed", "completed"])
        self.assertIn("unknown_citation", trials[0]["message"])
        self.assertEqual(trials[1]["prompt_sha256"], "sha")
        self.assertTrue(
            all(c.kwargs["local_files_only"] for c in generate.call_args_list)
        )

    def test_failed_generation_keeps_deterministic_knowledge(self):
        class Client:
            errors = []

            def europepmc_search(self, *args, **kwargs):
                return [{**EVIDENCE[0], "source": "EuropePMC"}]

        with patch.object(
            knowledge.REPORT_LLM,
            "generate",
            side_effect=RuntimeError("model unavailable"),
        ):
            kb, refs, trials, metrics = knowledge.build_feature_kb(
                pd.DataFrame(),
                pd.DataFrame([{"feature_id": "8q24_MYC_gain_amp"}]),
                True,
                Client(),
                3,
                "lymphoma",
                False,
                "",
                cancer_type="lymphoma",
                enable_literature_llm=True,
                literature_llm_models="model",
            )
        self.assertEqual(
            kb.iloc[0]["literature_synthesis_source"],
            "deterministic_pubmed_text_fallback",
        )
        self.assertEqual(kb.iloc[0]["literature_llm_model_used"], "")
        self.assertGreater(metrics["literature_llm_failed_trials"], 0)
        self.assertIn("MYC", kb.iloc[0]["literature_synthesis"])

    def test_sample_ranker_cap_and_order_are_not_discarded(self):
        fid = "8q24_MYC_gain_amp"
        refs = pd.DataFrame(
            [
                {
                    **EVIDENCE[0],
                    "feature_id": fid,
                    "pmid": str(100 + i),
                    "cited_by_count": 3 - i,
                }
                for i in range(3)
            ]
        )

        def generate(model, **kwargs):
            self.assertEqual(len(kwargs["evidence"]), 2)
            return "2,1", kwargs["evidence"], {}

        with patch.object(
            knowledge.REPORT_LLM, "generate", side_effect=generate
        ) as call:
            papers, summary, trials = knowledge.build_sample_literature(
                pd.DataFrame([{"sample": "synthetic", "feature_id": fid}]),
                pd.DataFrame(),
                refs,
                False,
                None,
                False,
                3,
                2,
                "lymphoma",
                "lymphoma",
                True,
                "model",
                True,
                2,
            )
        call.assert_called_once()
        self.assertEqual(papers.pmid.tolist(), ["101", "100"])
        self.assertEqual(
            summary.iloc[0]["literature_selection_method"],
            "llm_selection_with_deterministic_remainder",
        )
        self.assertEqual(trials.iloc[0]["sample"], "synthetic")
        self.assertNotIn("synthetic", call.call_args.kwargs["instructions"])

    def test_html_distinguishes_sources_and_escapes_model_text(self):
        for source, label in [
            ("huggingface_llm", "AI draft"),
            ("built_in_catalog", "Built-in catalog"),
            ("deterministic_pubmed_text_fallback", "no AI generation"),
        ]:
            with self.subTest(source=source):
                data = {
                    "ks_row": pd.Series(
                        {
                            "knowledge_literature_synthesis": "text <script>bad</script>",
                            "knowledge_literature_sources": source,
                        }
                    ),
                    "sample_knowledge": pd.DataFrame(),
                }
                rendered = pdf.html_interpretation(
                    pd.Series({"sample": "synthetic"}), data
                )
                self.assertIn(label, rendered)
                self.assertNotIn("<script>", rendered)
                self.assertNotIn("high-confidence CNA", rendered)


class CatalogDraftTests(unittest.TestCase):
    def build(self, **options):
        return knowledge.build_feature_kb(
            pd.DataFrame(),
            pd.DataFrame([{"feature_id": "8q24_MYC_gain_amp"}]),
            False, None, 3, "lymphoma", False, "",
            cancer_type="lymphoma", literature_llm_models="local-model",
            literature_llm_local_files_only=True, **options,
        )

    def valid_generation(self, model, **kwargs):
        evidence = kwargs["evidence"]
        self.assertEqual(evidence[0]["id"], "C1")
        self.assertEqual(evidence[0]["source"], "bundled_cna_catalog")
        self.assertEqual(evidence[0]["pmid"], "")
        self.assertEqual(evidence[0]["doi"], "")
        self.assertNotIn("abstract", evidence[0])
        self.assertEqual(evidence[0]["catalog_text"], knowledge.BUILTIN_FEATURE_KB["8q24_MYC_gain_amp"]["biological_interpretation"])
        self.assertTrue(kwargs["local_files_only"])
        self.assertIn("not a retrieved paper", kwargs["instructions"])
        return response("MYC copy gain affects transcriptional regulatory pathways in cancer biology.", ["C1"]), evidence, {"source_ids": "C1", "prompt_sha256": "recorded"}

    def test_explicit_catalog_route_generates_offline_without_fake_literature(self):
        with patch.object(knowledge.REPORT_LLM, "generate", side_effect=self.valid_generation) as generate:
            kb, refs, trials, metrics = self.build(enable_catalog_llm=True, enable_literature_llm=True)
        generate.assert_called_once()
        row = kb.iloc[0]
        self.assertEqual(row["literature_synthesis_source"], "huggingface_catalog_llm")
        self.assertEqual(row["catalog_llm_model_used"], "local-model")
        self.assertEqual(row["catalog_llm_status"], "completed_draft_needs_review")
        self.assertIn("Bundled CNA catalog: ", row["literature_synthesis"])
        self.assertIn("AI draft from bundled catalog", row["literature_synthesis"])
        self.assertNotIn("AI-generated literature draft", row["literature_synthesis"])
        self.assertNotIn("PMID", row["literature_synthesis"])
        self.assertEqual(metrics["literature_llm_attempted_features"], 0)
        self.assertEqual(metrics["literature_llm_completed_features"], 0)
        self.assertEqual(metrics["catalog_llm_attempted_features"], 1)
        self.assertEqual(metrics["catalog_llm_completed_features"], 1)
        self.assertEqual(trials.model_layer.tolist(), ["catalog_synthesis"])
        self.assertEqual(trials.source_ids.tolist(), ["C1"])
        self.assertFalse(any(refs.get("source", pd.Series(dtype=str)) == "EuropePMC"))

    def test_disabled_catalog_route_does_not_use_seed_metadata_for_generation(self):
        with patch.object(knowledge.REPORT_LLM, "generate") as generate:
            kb, refs, trials, metrics = self.build(enable_literature_llm=True)
        generate.assert_not_called()
        self.assertFalse(metrics["catalog_llm_enabled"])
        self.assertEqual(metrics["catalog_llm_attempted_features"], 0)
        self.assertEqual(kb.iloc[0]["literature_synthesis_source"], "built_in_catalog")

    def test_invalid_catalog_generation_remains_failed_with_deterministic_fallback(self):
        def invalid(model, **kwargs):
            return response(sources=["S1"]), kwargs["evidence"], {"response_text": "uncited output"}
        with patch.object(knowledge.REPORT_LLM, "generate", side_effect=invalid):
            kb, refs, trials, metrics = self.build(enable_catalog_llm=True)
        self.assertEqual(kb.iloc[0]["literature_synthesis_source"], "built_in_catalog")
        self.assertEqual(kb.iloc[0]["catalog_llm_status"], "no_llm_model_completed")
        self.assertEqual(metrics["catalog_llm_completed_features"], 0)
        self.assertEqual(metrics["catalog_llm_failed_trials"], 1)
        self.assertEqual(metrics["literature_llm_failed_trials"], 0)
        self.assertIn("unknown_citation", trials.iloc[0]["message"])
        self.assertEqual(trials.iloc[0]["response_text"], "uncited output")

    def test_no_detected_feature_does_not_trigger_catalog_wide_generation(self):
        with patch.object(knowledge.REPORT_LLM, "generate") as generate:
            kb, refs, trials, metrics = knowledge.build_feature_kb(
                pd.DataFrame(), pd.DataFrame(), False, None, 3, "lymphoma", False, "",
                cancer_type="lymphoma", enable_catalog_llm=True, literature_llm_models="local-model",
            )
        generate.assert_not_called()
        self.assertEqual(metrics["catalog_llm_attempted_features"], 0)
        self.assertTrue((kb.catalog_llm_status == "not_attempted_no_detected_feature").all())

    def test_catalog_feature_limit_prevents_model_work(self):
        with patch.object(knowledge.REPORT_LLM, "generate") as generate:
            kb, refs, trials, metrics = self.build(enable_catalog_llm=True, literature_llm_max_features=0)
        generate.assert_not_called()
        self.assertEqual(kb.iloc[0]["catalog_llm_status"], "not_attempted_max_features_0")

    def test_catalog_html_pdf_labels_and_model_trace(self):
        info = pd.Series({"literature_synthesis_source": "huggingface_catalog_llm", "catalog_llm_model_used": "local-model", "catalog_llm_status": "completed_draft_needs_review", "literature_llm_status": "not_enabled"})
        self.assertEqual(pdf.synthesis_model_trace(info), "local-model / completed_draft_needs_review")
        self.assertIn("bundled catalog and retrieved abstracts", pdf.literature_label("huggingface_catalog_llm;huggingface_llm"))
        data = {"ks_row": pd.Series({"knowledge_literature_synthesis": "A catalog draft <unsafe>.", "knowledge_literature_sources": "huggingface_catalog_llm"}), "sample_knowledge": pd.DataFrame()}
        row = pd.Series({"sample": "synthetic"})
        rendered = pdf.html_interpretation(row, data)
        self.assertIn("AI draft from bundled catalog", rendered)
        self.assertNotIn("<unsafe>", rendered)
        paragraphs = pdf.interpretation_paragraphs(row, data["ks_row"], pd.DataFrame())
        self.assertTrue(any("AI draft from bundled catalog" in item.getPlainText() for item in paragraphs if hasattr(item, "getPlainText")))


class ReportPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.clinician = load_script("report_clinician_tests", "08_clinician_driver_reports.py")
        cls.cohort = load_script("report_cohort_tests", "03_plot_report.py")

    def fixture(self, context=True):
        row = pd.Series({"sample": "synthetic", "rule_based_cna_class": "MYCN_neuroblastoma_pattern",
                         "cna_burden_class": "CNA-high_complex", "n_cna_events": 3, "altered_mb": 5.4})
        background = "Bundled lymphoma biology <source> & context."
        hint = "Lymphoma catalog association; review context."
        sk = pd.DataFrame([{"sample": "synthetic", "feature_id": "1q_gain", "display": "Broad 1q gain",
                            "genes": "MCL1", "biological_interpretation": background, "classification_hint": hint,
                            "literature_synthesis": "Unchanged source draft.", "literature_synthesis_source": "huggingface_llm"}])
        ks = pd.DataFrame([{"sample": "synthetic", "knowledge_refined_class": "Breast context CNA pattern",
                            "knowledge_literature_synthesis": "Unchanged mixed source summary.",
                            "knowledge_literature_sources": "huggingface_llm;deterministic_pubmed_text_fallback"}]) if context else pd.DataFrame()
        pr = pd.DataFrame([{"sample": "synthetic", "probable_cna_classification": "Breast context CNA pattern",
                            "probable_cna_score": 80, "agreement_call": "PATHOLOGY_NOT_PROVIDED"}]) if context else pd.DataFrame()
        empty = pd.DataFrame()
        data = pdf.sample_report_data("synthetic", row, empty, empty, empty, empty, empty, empty,
                                      sk, ks, empty, empty, empty, pr, 0, True)
        return row, sk, ks, pr, data, background, hint

    @staticmethod
    def pdf_text(path):
        from pypdf import PdfReader
        return " ".join(" ".join(page.extract_text() for page in PdfReader(path).pages).split())

    def test_knowledge_html_pdf_prioritize_context_and_preserve_catalog_values(self):
        import html
        row, sk, ks, pr, data, background, hint = self.fixture()
        originals = [obj.copy(deep=True) for obj in (row, sk, ks, pr)]
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            pdf.build_sample_pdf(out / "report.pdf", "synthetic", row, data)
            pdf.build_sample_html(out / "report.html", "synthetic", row, data, "report.pdf")
            rendered = (out / "report.html").read_text()
            text = self.pdf_text(out / "report.pdf")
            for document in (rendered, text):
                self.assertLess(document.index("Breast context CNA pattern"), document.index("MYCN_neuroblastoma_pattern"))
                self.assertIn("Cross-context catalog pattern (not a diagnosis)", document)
                self.assertIn("Catalog background; may describe other tumor types", document)
                self.assertIn("Catalog relevance; may describe other tumor types", document)
                self.assertIn("This background is not the context-aware sample assessment.", document)
                self.assertIn("Unchanged source draft.", document)
                self.assertIn("Unchanged mixed source summary.", document)
                self.assertIn(hint, document)
            self.assertIn(html.escape(background), rendered)
            self.assertNotIn("<source>", rendered)
            self.assertIn(background, text)
            self.assertNotIn("MYCN_neuroblastoma_pattern", rendered.split("</div>", 1)[0])
        pd.testing.assert_series_equal(row, originals[0])
        for current, original in zip((sk, ks, pr), originals[1:]):
            pd.testing.assert_frame_equal(current, original)

    def test_clinician_missing_context_does_not_promote_catalog_pattern(self):
        for context in (True, False):
            with self.subTest(context=context), tempfile.TemporaryDirectory() as directory:
                row, sk, ks, pr, data, background, hint = self.fixture(context)
                empty = pd.DataFrame()
                ks_row = ks.iloc[0] if not ks.empty else pd.Series(dtype=object)
                pr_row = pr.iloc[0] if not pr.empty else pd.Series(dtype=object)
                out = Path(directory)
                self.clinician.build_pdf(out / "report.pdf", "synthetic", row, pd.Series(dtype=object), pr_row, ks_row, sk, empty)
                self.clinician.build_html(out / "report.html", "synthetic", row, pd.Series(dtype=object), pr_row, ks_row, sk, empty, "report.pdf")
                for document in ((out / "report.html").read_text(), self.pdf_text(out / "report.pdf")):
                    primary = "Breast context CNA pattern" if context else "Context-aware interpretation unavailable."
                    self.assertLess(document.index(primary), document.index("MYCN_neuroblastoma_pattern"))
                    self.assertIn("Cross-context catalog pattern (not a diagnosis)", document)
                    self.assertIn("Catalog background; may describe other tumor types", document)
                    self.assertIn(hint, document)
                    if not context:
                        self.assertIn("No context-aware CNA assessment was supplied", document)
                interpretation = dict(self.clinician.make_interpretation_pairs(pr_row, row, ks_row, sk))
                self.assertNotIn("MYCN_neuroblastoma_pattern", interpretation["Context-aware CNA interpretation"])

    def test_cohort_catalog_labels_and_no_cna_branch(self):
        row, sk, ks, pr, data, background, hint = self.fixture()
        empty = pd.DataFrame()
        for count in (0, 3):
            row["n_cna_events"] = count
            rendered = self.cohort.build_sample_interpretation(row, empty, empty, empty)
            self.assertIn("MYCN_neuroblastoma_pattern", rendered)
            self.assertIn("Cross-context catalog pattern (not a diagnosis)", rendered)
            self.assertNotIn("is classified as", rendered)
        with tempfile.TemporaryDirectory() as directory, contextlib.chdir(directory):
            self.cohort.make_sample_reports(pd.DataFrame([row]), empty, empty, empty, empty, empty, empty, pr)
            self.cohort.make_report([], pd.DataFrame([row]), empty, empty, empty, pr)
            index = Path("sample_reports/index.html").read_text()
            sample = Path("sample_reports/synthetic_CNA_report.html").read_text()
            report = Path("cna_classifier_report.html").read_text()
            self.assertIn("Cross-context catalog pattern (not a diagnosis)", index)
            self.assertIn("Cross-context catalog pattern counts (not diagnoses)", report)
            self.assertIn("Technical classification table preview", report)
            self.assertLess(sample.index("Breast context CNA pattern"), sample.index("MYCN_neuroblastoma_pattern"))


@unittest.skipUnless(
    os.environ.get("ONCOTRACER_TEST_TINY_LLM") == "1",
    "opt-in tiny CPU models; no downloads",
)
class TinyModelTests(unittest.TestCase):
    def test_real_local_seq2seq_and_causal_models(self):
        from tokenizers import Tokenizer
        from tokenizers.models import WordLevel
        from tokenizers.pre_tokenizers import Whitespace
        from transformers import (
            GPT2Config,
            GPT2LMHeadModel,
            PreTrainedTokenizerFast,
            T5Config,
            T5ForConditionalGeneration,
        )

        with tempfile.TemporaryDirectory() as directory:
            for kind in ("t5", "gpt2"):
                with self.subTest(kind=kind):
                    folder = Path(directory) / kind
                    backend = Tokenizer(
                        WordLevel(
                            {"[PAD]": 0, "[EOS]": 1, "[UNK]": 2, "MYC": 3, "gain": 4},
                            unk_token="[UNK]",
                        )
                    )
                    backend.pre_tokenizer = Whitespace()
                    tokenizer = PreTrainedTokenizerFast(
                        tokenizer_object=backend,
                        unk_token="[UNK]",
                        pad_token="[PAD]",
                        eos_token="[EOS]",
                        model_max_length=512,
                    )
                    if kind == "gpt2":
                        tokenizer.chat_template = "{% for message in messages %}{{ message['content'] }}{% endfor %} Assistant:"
                    tokenizer.save_pretrained(folder)
                    if kind == "t5":
                        model = T5ForConditionalGeneration(
                            T5Config(
                                vocab_size=5,
                                d_model=16,
                                d_ff=32,
                                num_layers=1,
                                num_decoder_layers=1,
                                num_heads=2,
                                decoder_start_token_id=0,
                                pad_token_id=0,
                                eos_token_id=1,
                            )
                        )
                    else:
                        model = GPT2LMHeadModel(
                            GPT2Config(
                                vocab_size=5,
                                n_embd=16,
                                n_layer=1,
                                n_head=2,
                                n_positions=512,
                                pad_token_id=0,
                                eos_token_id=1,
                                bos_token_id=0,
                            )
                        )
                    model.save_pretrained(folder, safe_serialization=True)
                    engine = runtime.LocalReportLLM(threads=1)
                    text, visible, audit = engine.generate(
                        str(folder),
                        local_files_only=True,
                        instructions="Summarize the evidence.",
                        evidence=EVIDENCE,
                        max_input_chars=1500,
                        max_new_tokens=6,
                    )
                    self.assertEqual(audit["device"], "cpu")
                    self.assertLessEqual(audit["generated_tokens"], 7)
                    self.assertGreater(audit["prompt_tokens"], 0)
                    self.assertEqual(visible[0]["id"], "S1")
                    self.assertFalse(engine._bundle[0].training)
                    self.assertEqual(
                        next(engine._bundle[0].parameters()).device.type, "cpu"
                    )


if __name__ == "__main__":
    unittest.main()
