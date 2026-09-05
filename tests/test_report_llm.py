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
