"""Classifier model-cache discovery survives isolated Fontconfig HOME/XDG paths."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oncotracer_cli.engine import Toolchain

CACHE_KEYS = ("HF_HOME", "HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "TRANSFORMERS_CACHE",
              "XDG_CACHE_HOME")
HAS_HUB = importlib.util.find_spec("huggingface_hub") is not None


class ModelCacheEnvironmentTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.real_home = self.root / "caller-home"
        self.real_home.mkdir()
        prefix = self.root / "classifier"
        config = prefix / "etc/fonts/fonts.conf"
        config.parent.mkdir(parents=True)
        config.write_text("<fontconfig/>\n")
        self.toolchain = Toolchain(core_prefix=prefix, classifier_prefix=prefix,
                                   runtime_cache=self.root / "runtime-cache")
        environment = dict(os.environ)
        for key in CACHE_KEYS:
            environment.pop(key, None)
        environment.update(HOME=str(self.real_home), PYTHONDONTWRITEBYTECODE="1",
                           HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        patcher = patch.dict(os.environ, environment, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def child_environment(self):
        environment = dict(os.environ)
        for key, value in self.toolchain.environment("classifier").items():
            if value is None:
                environment.pop(key, None)
            else:
                environment[key] = value
        return environment

    def test_default_model_cache_preserved_with_font_isolation(self):
        environment = self.toolchain.environment("classifier")
        self.assertEqual(environment["HF_HOME"], str(self.real_home / ".cache/huggingface"))
        self.assertNotEqual(environment["HOME"], str(self.real_home))
        self.assertNotEqual(environment["XDG_CACHE_HOME"], str(self.real_home / ".cache"))
        self.assertTrue(Path(environment["HOME"]).is_relative_to(self.root / "runtime-cache"))
        self.assertTrue(Path(environment["FONTCONFIG_FILE"]).is_relative_to(self.root / "runtime-cache"))
        self.toolchain.validate_environment()
        self.assertEqual(os.environ["HOME"], str(self.real_home))

    def test_explicit_hf_home_precedes_xdg_and_expands_original_home(self):
        with patch.dict(os.environ, {"HF_HOME": "~/chosen-models", "XDG_CACHE_HOME": "/unused-cache"}):
            self.assertEqual(self.toolchain.environment("classifier")["HF_HOME"],
                             str(self.real_home / "chosen-models"))

    def test_original_xdg_cache_is_used_when_hf_home_is_absent(self):
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(self.root / "shared-cache")}):
            self.assertEqual(self.toolchain.environment("classifier")["HF_HOME"],
                             str(self.root / "shared-cache/huggingface"))

    def test_explicit_cache_overrides_are_preserved(self):
        with patch.dict(os.environ, {"HF_HUB_CACHE": "~/hub-cache",
                                     "HUGGINGFACE_HUB_CACHE": "$HOME/legacy-hub",
                                     "TRANSFORMERS_CACHE": str(self.root / "transformers-cache")}):
            environment = self.toolchain.environment("classifier")
            self.assertEqual(environment["HF_HUB_CACHE"], str(self.real_home / "hub-cache"))
            self.assertEqual(environment["HUGGINGFACE_HUB_CACHE"], str(self.real_home / "legacy-hub"))
            self.assertEqual(environment["TRANSFORMERS_CACHE"], str(self.root / "transformers-cache"))

    def test_other_stages_and_unisolated_runtime_keep_existing_environment(self):
        self.assertNotIn("HF_HOME", self.toolchain.environment("core"))
        self.assertEqual(Toolchain().environment("classifier"), {})

    @unittest.skipUnless(HAS_HUB, "optional Hugging Face cache resolver")
    def test_real_hub_resolver_finds_cached_model_without_network(self):
        revision = "1" * 40
        expected = self.real_home / ".cache/huggingface/hub/models--synthetic--cached-model/snapshots" / revision / "config.json"
        expected.parent.mkdir(parents=True)
        expected.write_text("{}")
        result = subprocess.run(
            [sys.executable, "-B", "-c",
             "import json; from huggingface_hub import constants, try_to_load_from_cache; "
             "print(json.dumps({'home': constants.HF_HOME, 'hub': constants.HF_HUB_CACHE, "
             "'cached': try_to_load_from_cache('synthetic/cached-model', 'config.json', revision='" + revision + "')}))"],
            env=self.child_environment(), capture_output=True, text=True, check=True,
        )
        actual = json.loads(result.stdout)
        self.assertEqual(actual["home"], str(self.real_home / ".cache/huggingface"))
        self.assertEqual(actual["cached"], str(expected))
        self.toolchain.validate_environment()

    @unittest.skipUnless(HAS_HUB, "optional Hugging Face cache resolver")
    def test_real_hub_resolver_retains_explicit_override_precedence(self):
        for overrides, expected in (
            ({"HUGGINGFACE_HUB_CACHE": "~/legacy-hub"}, self.real_home / "legacy-hub"),
            ({"HUGGINGFACE_HUB_CACHE": "~/legacy-hub", "HF_HUB_CACHE": "~/new-hub"}, self.real_home / "new-hub"),
        ):
            with self.subTest(overrides=overrides), patch.dict(os.environ, overrides):
                result = subprocess.run(
                    [sys.executable, "-B", "-c", "from huggingface_hub.constants import HF_HUB_CACHE; print(HF_HUB_CACHE)"],
                    env=self.child_environment(), capture_output=True, text=True, check=True,
                )
                self.assertEqual(result.stdout.strip(), str(expected))


if __name__ == "__main__":
    unittest.main()
