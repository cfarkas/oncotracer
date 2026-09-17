"""Offline literature retrieval, cache and citation provenance contracts."""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import requests

SCRIPTS = Path(__file__).resolve().parents[1] / "bin/cna_classifier_nf/bin"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("literature_retrieval_tests", SCRIPTS / "05_scrape_cna_knowledge.py")
knowledge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(knowledge)
sys.path.remove(str(SCRIPTS))

ARTICLE = {"pmid": "12345678", "doi": "10.1234/ABC", "title": "Synthetic copy number study",
           "abstractText": "Synthetic copy number evidence is used only for retrieval contract testing.",
           "journalInfo": {"journal": {"title": "Synthetic Journal"}}}


def payload(*articles):
    return {"hitCount": len(articles), "resultList": {"result": list(articles)}}


def response(data=None, status=200, retry_after=None):
    result = MagicMock(status_code=status)
    result.headers = {} if retry_after is None else {"Retry-After": retry_after}
    result.json.return_value = payload(ARTICLE) if data is None else data
    if status >= 400:
        result.raise_for_status.side_effect = requests.HTTPError(str(status), response=result)
    return result


class LiteratureRetrievalTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cache = Path(directory.name)
        self.client = knowledge.LiteratureClient(self.cache, sleep=0)
        self.client.session = MagicMock()
        sleeper = patch.object(knowledge.time, "sleep")
        self.sleep = sleeper.start()
        self.addCleanup(sleeper.stop)

    def test_transient_retry_recovers_with_bounded_backoff(self):
        self.client.session.get.side_effect = [response(status=429, retry_after="999"),
                                               requests.Timeout("synthetic timeout"), response()]
        rows = self.client.europepmc_search("synthetic query")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["journal"], "Synthetic Journal")
        self.assertEqual(rows[0]["url"], "https://pubmed.ncbi.nlm.nih.gov/12345678/")
        self.assertEqual(self.client.request_log[-1]["attempts"], 3)
        self.assertEqual(self.client.request_log[-1]["status"], "retrieved")
        self.assertEqual(self.client.errors, [])
        self.assertEqual([call.args[0] for call in self.sleep.call_args_list], [8, 1])
        self.assertEqual(self.client.session.get.call_args.kwargs["timeout"], (5, 20))

    def test_failed_response_is_not_cached_as_empty_evidence(self):
        self.client.session.get.return_value = response({"error": "service unavailable"})
        self.assertEqual(self.client.europepmc_search("synthetic query"), [])
        self.assertEqual(self.client.session.get.call_count, 3)
        self.assertEqual(self.client.request_log[-1]["status"], "retrieval_failed")
        self.assertFalse(list(self.cache.glob("*.json")))
        self.client.session.get.return_value = response()
        self.assertEqual(len(self.client.europepmc_search("synthetic query")), 1)

    def test_permanent_errors_do_not_retry_or_disable_other_queries(self):
        self.client.session.get.return_value = response(status=400)
        for query in ("bad query one", "bad query two"):
            self.assertEqual(self.client.europepmc_search(query), [])
        self.assertEqual(self.client.session.get.call_count, 2)
        self.assertFalse(self.client.disabled)
        self.sleep.assert_not_called()
        self.client.session.get.return_value = response()
        self.assertEqual(len(self.client.europepmc_search("valid query")), 1)

    def test_circuit_bounds_failures_but_preserves_cached_evidence(self):
        self.client.session.get.return_value = response()
        expected = self.client.europepmc_search("cached query")
        self.client.session.get.side_effect = requests.ConnectionError("offline")
        self.client.europepmc_search("uncached one")
        self.client.europepmc_search("uncached two")
        self.assertTrue(self.client.disabled)
        self.assertEqual(self.client.session.get.call_count, 7)
        self.assertEqual(self.client.europepmc_search("cached query"), expected)
        self.assertTrue(self.client.request_log[-1]["cache_hit"])
        self.assertEqual(self.client.europepmc_search("uncached three"), [])
        self.assertEqual(self.client.request_log[-1]["status"], "skipped_network_disabled")
        self.assertEqual(self.client.session.get.call_count, 7)

    def test_successful_empty_search_is_distinct_and_expires(self):
        self.client.session.get.return_value = response(payload())
        self.assertEqual(self.client.europepmc_search("empty query"), [])
        self.assertEqual(self.client.request_log[-1]["status"], "no_results")
        cache_file = next(self.cache.glob("*.json"))
        self.client.europepmc_search("empty query")
        self.assertEqual(self.client.session.get.call_count, 1)
        old = time.time() - 86401
        os.utime(cache_file, (old, old))
        self.client.session.get.return_value = response()
        self.assertEqual(len(self.client.europepmc_search("empty query")), 1)
        self.assertEqual(self.client.session.get.call_count, 2)

    def test_corrupt_cache_is_replaced_and_cache_write_failure_retains_evidence(self):
        self.client.session.get.return_value = response()
        self.client.europepmc_search("cached query")
        next(self.cache.glob("*.json")).write_text('{"error":"bad cache"}')
        self.assertEqual(len(self.client.europepmc_search("cached query")), 1)
        self.assertIn("cache_error", self.client.request_log[-1])
        with patch.object(knowledge.tempfile, "NamedTemporaryFile", side_effect=OSError("disk full")):
            self.assertEqual(len(self.client.europepmc_search("new query")), 1)
        self.assertEqual(self.client.request_log[-1]["status"], "retrieved")
        self.assertIn("disk full", self.client.request_log[-1]["cache_error"])

    def test_metadata_only_records_have_resolvable_links(self):
        self.client.session.get.return_value = response(payload(
            {"doi": "https://doi.org/10.1234/ABC", "title": "DOI record"},
            {"pmcid": "PMC12345", "title": "PMC record"},
            {"source": "PPR", "id": "PPR12345", "title": "Source record"}))
        rows = self.client.europepmc_search("synthetic query")
        self.assertEqual([row["url"] for row in rows], ["https://doi.org/10.1234/abc",
                         "https://pmc.ncbi.nlm.nih.gov/articles/PMC12345/",
                         "https://europepmc.org/article/PPR/PPR12345"])
        self.assertEqual({row["evidence_status"] for row in rows}, {"metadata_only"})

    def test_seed_lookup_requires_matching_retrieved_pmid(self):
        self.client.session.get.return_value = response()
        self.assertIsNone(self.client.europepmc_by_pmid("123 OR 456"))
        self.client.session.get.assert_not_called()
        self.assertIsNone(self.client.europepmc_by_pmid("87654321"))
        self.assertEqual(self.client.europepmc_by_pmid("12345678")["pmid"], "12345678")

    def test_feature_status_distinguishes_failure_no_results_and_disabled(self):
        def build(web=True):
            with patch.object(knowledge, "cna_query_variants", return_value=["synthetic query"]):
                return knowledge.build_feature_kb(
                    pd.DataFrame(), pd.DataFrame([{"sample": "synthetic", "feature_id": "7p11_EGFR_gain_amp"}]),
                    web, self.client, 4, "cancer", False, "unused")
        kb, _, _, _ = build(web=False)
        self.assertEqual(kb.iloc[0]["literature_retrieval_status"], "not_enabled")
        self.client.session.get.return_value = response(payload())
        kb, _, _, metrics = build()
        self.assertEqual(kb.iloc[0]["literature_retrieval_status"], "no_results")
        self.assertEqual(metrics["literature_retrieval_status_counts"]["no_results"], 1)
        for file in self.cache.glob("*.json"):
            file.unlink()
        self.client.session.get.side_effect = requests.Timeout("offline")
        kb, _, _, metrics = build()
        self.assertEqual(kb.iloc[0]["literature_retrieval_status"], "retrieval_failed")
        self.assertEqual(kb.iloc[0]["n_usable_literature_abstracts"], 0)
        self.assertTrue(metrics["literature_requests"])


class LiteratureCitationTests(unittest.TestCase):
    def test_identifier_bridges_merge_records_and_keep_provenance(self):
        records = [
            {"pmid": "123", "title": "PMID seed from built-in CNA knowledge dictionary", "source": "built-in PMID seed"},
            {"doi": "doi:10.1234/ABC", "title": "Verified synthetic study", "url": "https://doi.org/10.1234/ABC", "query": "first query"},
            {"pmid": "123", "doi": "https://doi.org/10.1234/abc", "title": "Verified synthetic study", "abstract": "Synthetic evidence.", "source": "EuropePMC", "query": "second query"},
        ]
        merged = knowledge.merge_reference_records(records)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["source"], "EuropePMC")
        self.assertEqual(merged[0]["abstract"], "Synthetic evidence.")
        self.assertEqual(merged[0]["url"], "https://pubmed.ncbi.nlm.nih.gov/123/")
        self.assertIn("https://doi.org/10.1234/ABC", merged[0]["source_urls"])
        self.assertIn("first query", merged[0]["retrieval_queries"])
        self.assertIn("second query", merged[0]["retrieval_queries"])
        self.assertEqual(len(knowledge.merge_reference_records(merged)), 1)

    def test_distinct_identified_articles_with_same_title_remain_distinct(self):
        records = [{"pmid": "123", "title": "Same title"}, {"pmid": "456", "title": "Same title"}]
        self.assertEqual(len(knowledge.merge_reference_records(records)), 2)

    def test_fallback_excerpts_cite_their_own_retrieved_identifiers(self):
        refs = [{"pmid": "123", "abstract": "The first synthetic abstract provides a separate evidence sentence."},
                {"doi": "10.1234/ABC", "abstract": "The second synthetic abstract provides another evidence sentence."}]
        result = knowledge.deterministic_literature_synthesis("unattributed combined text", {}, "pan_cancer", refs=refs)
        self.assertIn("evidence sentence. [PMID 123]", result)
        self.assertIn("evidence sentence. [DOI 10.1234/abc]", result)
        self.assertNotIn("unattributed", result)


if __name__ == "__main__":
    unittest.main()
